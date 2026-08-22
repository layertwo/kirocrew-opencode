"""provider.py — OpenCode ACP provider monkey-patches.

Patches AcpClient, AcpProvider, and _dispatch to handle the OpenCode backend.
Called by ``install.py`` at install time.

3 patches:
  1. AcpClient — _is_opencode, _spawn (opencode acp --cwd), _initialize_session
     (numeric protocol), _extract_tool_call_refinement (raw_params fix),
     supports_steer, send_command, stream_command, _reject_unknown_server_request
  2. AcpProvider — is_opencode_backend, route through AcpClient, skip overlays,
     stream_command routing
  3. _dispatch — _build_tool_refinement_event raw_params_cache refresh
     (root-cause fix for deny-by-default; benefits ALL backends)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ._config import (
    ACP_BACKEND_OPENCODE,
    PROTOCOL_VERSION_OPENCODE,
    OPENCODE_SUBCMD,
    resolve_opencode_bin,
)

logger = logging.getLogger(__name__)


def patch_client() -> None:
    """Monkey-patch AcpClient to handle the OpenCode backend."""
    from kiro_crew.acp.client import AcpClient
    from kiro_crew.acp.types import ACP_BACKEND_OPENCODE

    # ── _is_opencode property ──
    @property
    def _is_opencode(self) -> bool:  # type: ignore[no-untyped-def]
        return self.backend == ACP_BACKEND_OPENCODE

    AcpClient._is_opencode = _is_opencode

    # ── _spawn — add OpenCode branch ──
    _orig_spawn = AcpClient._spawn

    async def _spawn(self) -> None:  # type: ignore[no-untyped-def]
        if self._is_opencode:
            await asyncio.to_thread(self._work_dir.mkdir, parents=True, exist_ok=True)
            opencode_bin = await asyncio.to_thread(resolve_opencode_bin)
            if not opencode_bin:
                from kiro_crew.acp.client import AcpError
                raise AcpError(
                    "opencode not found in PATH (set OPENCODE_BIN or install opencode)"
                )
            argv = [opencode_bin, OPENCODE_SUBCMD, "--cwd", str(self._work_dir)]
            from kiro_crew.acp.client import wrap_argv
            argv, self._sandbox_cleanup = wrap_argv(
                argv,
                mode=self._sandbox_mode,
                strip_python_env=True,
                is_kiro_cli=False,
            )
            await self._start_process(argv)
            return
        await _orig_spawn(self)

    AcpClient._spawn = _spawn

    # ── _initialize_session — numeric protocol version, skip set_mode ──
    _orig_init = AcpClient._initialize_session

    async def _initialize_session(self) -> None:  # type: ignore[no-untyped-def]
        if not self._is_opencode:
            return await _orig_init(self)

        from kiro_crew.acp.client import (
            METHOD_INITIALIZE,
            CLIENT_NAME,
            CLIENT_VERSION,
            ACP_CLIENT_CAPABILITIES,
            _INIT_TIMEOUT,
        )

        init_id = await self._send_request(
            METHOD_INITIALIZE,
            {
                "protocolVersion": PROTOCOL_VERSION_OPENCODE,
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
                "clientCapabilities": ACP_CLIENT_CAPABILITIES,
            },
        )
        init_resp = await self._wait_for_response(init_id, timeout=_INIT_TIMEOUT)
        logger.info("OpenCode ACP initialized (protocol=%s)", init_resp.get("protocolVersion"))

        self._can_load_session = init_resp.get("agentCapabilities", {}).get("loadSession", False)

        # Capture available models from the initialize response so the model
        # dropdown and advertised_model_ids work. The original _initialize_session
        # calls _capture_available_models; we replicate it here.
        config_opts = init_resp.get("configOptions") or []
        if hasattr(self, "_capture_available_models"):
            try:
                self._capture_available_models(config_opts)
            except Exception:
                logger.warning("OpenCode: _capture_available_models failed", exc_info=True)

        self._resumed = False
        resume_sid = self._resume_session_id
        self._resume_session_id = None

        if resume_sid and self._can_load_session:
            from kiro_crew.acp.client import METHOD_SESSION_LOAD
            load_id = await self._send_request(
                METHOD_SESSION_LOAD, {"sessionId": resume_sid}
            )
            load_resp = await self._wait_for_response(load_id, timeout=_INIT_TIMEOUT)
            if not load_resp.get("error"):
                self._session_id = resume_sid
                self._resumed = True

        if not self._resumed:
            new_params: dict[str, Any] = {"cwd": str(self._work_dir)}
            new_params["mcpServers"] = self._pooled_mcp_servers()
            if self._model:
                new_params["model"] = self._model
            config_opts = init_resp.get("configOptions")
            if config_opts:
                new_params["configOptions"] = config_opts
            from kiro_crew.acp.client import METHOD_SESSION_NEW
            new_id = await self._send_request(METHOD_SESSION_NEW, new_params)
            session_resp = await self._wait_for_response(new_id, timeout=_INIT_TIMEOUT)
            if session_resp.get("error"):
                from kiro_crew.acp.client import AcpError
                raise AcpError(f"session/new failed: {session_resp['error']}")
            self._session_id = session_resp.get("sessionId", "")

        # Skip set_mode — OpenCode has no kiro modes.
        # Skip JSONL seek — OpenCode stores sessions via its own SDK.
        if self._model and not self._resumed:
            try:
                await self._send_request("session/set_model", {"model": self._model})
            except Exception:
                logger.warning("OpenCode: session/set_model failed (model=%s)", self._model, exc_info=True)

    AcpClient._initialize_session = _initialize_session

    # ── _extract_tool_call_refinement — refresh _tool_call_params (raw_params fix) ──
    _orig_extract_refinement = AcpClient._extract_tool_call_refinement

    def _extract_tool_call_refinement(self, msg):  # type: ignore[no-untyped-def]
        result = _orig_extract_refinement(self, msg)
        if result is None:
            return None
        params = msg.params or {}
        update = params.get("update", {})
        raw_input = update.get("rawInput")
        tool_use_id = update.get("toolCallId", "")
        if tool_use_id and isinstance(raw_input, dict) and raw_input:
            self._tool_call_params[tool_use_id] = raw_input
        return result

    AcpClient._extract_tool_call_refinement = _extract_tool_call_refinement

    # ── supports_steer — False for OpenCode ──
    _orig_supports_steer = AcpClient.supports_steer

    @property
    def supports_steer(self) -> bool:  # type: ignore[no-untyped-def]
        if self._is_opencode:
            return False
        return _orig_supports_steer.fget(self)  # type: ignore[attr-defined]

    AcpClient.supports_steer = supports_steer

    # ── _reject_unknown_server_request — answer _kiro.dev/* locally ──
    _orig_reject = AcpClient._reject_unknown_server_request

    async def _reject_unknown_server_request(self, msg) -> None:  # type: ignore[no-untyped-def]
        if (
            self._is_opencode
            and isinstance(msg.method, str)
            and msg.method.startswith("_kiro.dev/")
            and msg.id is not None
        ):
            await self._send_response(msg.id, {})
            return
        await _orig_reject(self, msg)

    AcpClient._reject_unknown_server_request = _reject_unknown_server_request

    # ── send_command — route through session/prompt for OpenCode ──
    _orig_send_command = AcpClient.send_command

    async def send_command(self, command: str, args: dict | None = None) -> str:  # type: ignore[no-untyped-def]
        if self._is_opencode:
            await self.ensure_ready()
            result = await self._read_prompt_response(
                await self._send_prompt(command), timeout=60.0
            )
            return result
        return await _orig_send_command(self, command, args)

    AcpClient.send_command = send_command

    # ── stream_command — route through session/prompt for OpenCode ──
    _orig_stream_command = AcpClient.stream_command

    async def stream_command(self, command: str, timeout: float = 60.0):  # type: ignore[no-untyped-def]
        if self._is_opencode:
            self._cancelled = False
            await self.ensure_ready()
            async for e in self._dispatch_events(
                await self._send_prompt(command), timeout
            ):
                yield e
            return
        async for e in _orig_stream_command(self, command, timeout):
            yield e

    AcpClient.stream_command = stream_command

    logger.info("opencode_provider: AcpClient patched ✅")


def patch_provider() -> None:
    """Monkey-patch AcpProvider to handle the OpenCode backend.

    Targets 0.3.0+ where is_session_sharing_eligible and start() use
    membership-set checks. OpenCode is not a member of either set, so the
    membership checks already exclude it — we only need is_opencode_backend
    and to route start() through AcpClient.ensure_ready().
    """
    from kiro_crew.providers.acp import AcpProvider
    from kiro_crew.acp.types import ACP_BACKEND_OPENCODE

    @property
    def is_opencode_backend(self) -> bool:  # type: ignore[no-untyped-def]
        return self._client.backend == ACP_BACKEND_OPENCODE

    AcpProvider.is_opencode_backend = is_opencode_backend

    _orig_start = AcpProvider.start

    async def start(self) -> None:  # type: ignore[no-untyped-def]
        if self.is_opencode_backend:
            await self._client.ensure_ready()
            return
        await _orig_start(self)

    AcpProvider.start = start

    _orig_effort = AcpProvider._apply_effort_overlay

    def _apply_effort_overlay(self) -> None:  # type: ignore[no-untyped-def]
        if not self.is_opencode_backend:
            _orig_effort(self)

    AcpProvider._apply_effort_overlay = _apply_effort_overlay

    _orig_tool_search = AcpProvider._apply_tool_search_overlay

    def _apply_tool_search_overlay(self) -> None:  # type: ignore[no-untyped-def]
        if not self.is_opencode_backend:
            _orig_tool_search(self)

    AcpProvider._apply_tool_search_overlay = _apply_tool_search_overlay

    _orig_stream = AcpProvider.stream_command

    async def stream_command(self, command: str):  # type: ignore[no-untyped-def]
        if self.is_opencode_backend:
            async for e in self._client.stream_events(command):
                yield self._to_llm_event(e)
            return
        async for e in _orig_stream(self, command):
            yield e

    AcpProvider.stream_command = stream_command

    logger.info("opencode_provider: AcpProvider patched ✅")


def patch_dispatch_raw_params() -> None:
    """Fix raw_params_cache refresh in _build_tool_refinement_event.

    Root-cause fix for deny-by-default: when an agent streams a tool call in
    two frames, the refinement handler refreshes shell_cache and
    tool_input_cache but never raw_params_cache. Benefits ALL backends.

    0.3.0's parse_session_update calls _build_tool_refinement_event WITHOUT
    passing raw_params_cache, so we patch parse_session_update itself to
    manually refresh the cache after calling the original.
    """
    try:
        from kiro_crew.acp import _dispatch
    except ImportError:
        logger.warning("opencode_provider: _dispatch.py not found — skipping raw_params fix")
        return

    _orig_parse = _dispatch.parse_session_update

    def parse_session_update(  # type: ignore[no-untyped-def]
        update,
        *,
        tool_input_cache=None,
        shell_cache=None,
        raw_params_cache=None,
        mcp_server_name_cache=None,
        tool_name_cache=None,
    ):
        events = _orig_parse(
            update,
            tool_input_cache=tool_input_cache,
            shell_cache=shell_cache,
            raw_params_cache=raw_params_cache,
            mcp_server_name_cache=mcp_server_name_cache,
            tool_name_cache=tool_name_cache,
        )
        # The original parse_session_update does NOT pass raw_params_cache to
        # _build_tool_refinement_event, so manually refresh from the update dict.
        if (
            raw_params_cache is not None
            and isinstance(update, dict)
            and update.get("sessionUpdate") == "tool_call_update"
        ):
            tool_use_id = update.get("toolCallId", "")
            raw_input = update.get("rawInput")
            if tool_use_id and isinstance(raw_input, dict) and raw_input:
                raw_params_cache[tool_use_id] = raw_input
        return events

    _dispatch.parse_session_update = parse_session_update

    logger.info("opencode_provider: _dispatch raw_params fix patched ✅")