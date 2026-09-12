"""Contract checks against the REAL kirocrew.

The other test modules fabricate ``kiro_crew`` with ``types.ModuleType`` and
inject it into ``sys.modules``, so they pass no matter what upstream does. This
module imports the installed kirocrew instead, and fails when a symbol we
monkey-patch is renamed, removed, or re-signatured — which is the way a version
bump actually breaks this provider.

Runs in a subprocess: ``install()`` mutates kirocrew's classes process-wide and
latches ``_installed``, so it needs an interpreter the other tests haven't
touched.

One table per way a version bump breaks us; see each table's comment.
"""

from __future__ import annotations

import importlib
import inspect
import re
import subprocess
import sys

# Attributes provider.py / install.py read or replace. Existence only.
ATTRS: dict[str, list[str]] = {
    "kiro_crew.acp.types": ["ACP_BACKENDS_KNOWN"],
    "kiro_crew.acp.client": [
        "AcpClient",
        "AcpError",
        "wrap_argv_async",  # _spawn; the sync wrap_argv() raises on a live loop
        "METHOD_INITIALIZE",
        "CLIENT_NAME",
        "CLIENT_VERSION",
        "ACP_CLIENT_CAPABILITIES",
        "_INIT_TIMEOUT",
        "METHOD_SESSION_LOAD",
        "METHOD_SESSION_NEW",
        # used by our _start_process
        "_STDOUT_BUFFER_LIMIT",
        "KIROCREW_SPAWNED_ENV",
        "KIROCREW_SPAWNED_VALUE",
        # used by our stream_command
        "_effective_prompt_timeout_async",
    ],
    "kiro_crew.acp.client:AcpClient": [
        "backend",
        "_spawn",
        "_initialize_session",
        "_extract_tool_call_refinement",
        "supports_steer",
        "_reject_unknown_server_request",
        "send_command",
        "stream_command",
        "_send_request",
        "_wait_for_response",
        "_send_response",
        "_send_prompt",
        "_read_prompt_response",
        "_dispatch_events",
        "ensure_ready",
        "_pooled_mcp_servers",
        "_capture_available_models",
        "stream_events",
        "_drain_stderr",
    ],
    "kiro_crew.sandbox": ["create_subprocess_limited", "scrub_agent_denied_env"],
    "kiro_crew.env": ["augmented_path"],
    "kiro_crew.platform_compat": ["IS_POSIX"],
    "kiro_crew.providers.acp:AcpProvider": [
        "start",
        "_apply_effort_overlay",
        "_apply_tool_search_overlay",
        "stream_command",
        "_to_llm_event",
    ],
    "kiro_crew.config.loader:KiroCrewConfig": ["create_provider_factory", "load"],
    "kiro_crew.acp_backends": [
        "ACP_BACKENDS_KNOWN",  # canonical; acp.types re-exports it
        "selectable_backends",  # read by resolve_selected_backend + the _bg path
        "register_selectable_backend",  # upstream's edition hook
        "resolve_selected_backend",
    ],
    "kiro_crew.session": ["_bg_runtime_backends"],  # the _bg routing predicate
    "kiro_crew.kiro_prerequisite:KiroPrerequisiteService": [
        "__init__",  # patched to force assume_ready
        "snapshot",  # the payload the SPA gate branches on
        "session_ready",  # gates poll-driven spawn sites
        "verified_ready",  # gates the reruns and POST /v1/chat/completions
        "initial_setup_complete",
    ],
    "kiro_crew.acp._dispatch": ["parse_session_update"],
}

# Instance attributes our patches read or write, checked by scanning the
# owner's __init__ source for `self.<name> =`. Static because constructing an
# AcpClient needs real config; a rename still surfaces, which is the point.
INIT_ATTRS: dict[str, list[str]] = {
    "kiro_crew.acp.client:AcpClient": [
        "_extra_env",  # _start_process
        "_work_dir",  # _start_process, _spawn, _initialize_session
        "_process",  # _start_process
        "_pid",  # _start_process
        "_stderr_task",  # _start_process
        "_sandbox_mode",  # _spawn
        "_sandbox_cleanup",  # _spawn
        "_model",  # _initialize_session
        "_resume_session_id",  # _initialize_session
        "_can_load_session",  # _initialize_session
        "_resumed",  # _initialize_session
        "_session_id",  # _initialize_session
        "_tool_call_params",  # _extract_tool_call_refinement
        "_cancelled",  # stream_command
    ],
    "kiro_crew.providers.acp:AcpProvider": [
        "_client",  # is_opencode_backend, start, stream_command
    ],
}

# Keywords provider._refreshes_raw_params passes to parse_session_update.
# WRAPPED catches params our patch drops; this catches ones upstream dropped
# from under us — the probe would raise, get swallowed by its own except, and
# re-apply a patch that had already retired itself.
PROBE_KWARGS = (
    "tool_input_cache",
    "shell_cache",
    "raw_params_cache",
    "mcp_server_name_cache",
    "tool_name_cache",
)

# Membership sets install.py deliberately leaves opencode out of. Each must
# still exist (a rename means our exclusion stopped meaning anything) and must
# still exclude opencode after install().
EXCLUDED: list[str] = [
    "ACP_BACKENDS_ACP_RUNTIME",  # in => _bg sessions bypass our patched factory
    "ACP_BACKENDS_SESSION_SHARING",  # in => sessions multiplexed across a process
    "ACP_BACKENDS_STEER",  # in => steer requests to an agent that has no steer
    "ACP_BACKENDS_INTERNAL_SANDBOX",  # in => kiro-cli's sandbox wrap applied
    "ACP_BACKENDS_KIRO_IDENTITY_STORE",  # in => kiro identity retirement sweeps us
]

# Callables we replace with a wrapper that mirrors the upstream signature.
# Our replacement must keep accepting every parameter upstream declares,
# otherwise upstream call sites blow up with TypeError at runtime.
WRAPPED: list[str] = [
    "kiro_crew.acp._dispatch:parse_session_update",
    "kiro_crew.acp.client:AcpClient._spawn",
    "kiro_crew.acp.client:AcpClient.send_command",
    "kiro_crew.acp.client:AcpClient.stream_command",
    "kiro_crew.acp.client:AcpClient._extract_tool_call_refinement",
    "kiro_crew.acp.client:AcpClient._reject_unknown_server_request",
    "kiro_crew.providers.acp:AcpProvider.start",
    "kiro_crew.providers.acp:AcpProvider.stream_command",
    "kiro_crew.providers.acp:AcpProvider._apply_effort_overlay",
    "kiro_crew.providers.acp:AcpProvider._apply_tool_search_overlay",
    "kiro_crew.config.loader:KiroCrewConfig.create_provider_factory",
    "kiro_crew.config.loader:KiroCrewConfig.load",
    "kiro_crew.kiro_prerequisite:KiroPrerequisiteService.__init__",
]


def _returned_factory_sig() -> inspect.Signature:
    """Signature of the callable ``create_provider_factory()`` RETURNS.

    WRAPPED below compares ``create_provider_factory`` itself, which is ``(self)``
    both before and after install — a clean match, while the factory it returns
    is what upstream actually calls. That blind spot shipped a gateway-wide
    outage: our wrapper was keyword-only and every upstream call site passes the
    session key positionally.

    ``kiro_crew.acp.client`` is imported first because create_provider_factory
    defers ``from kiro_crew.providers.acp import AcpProvider`` to call time, and
    reaching that import through config.loader alone trips the
    acp -> client -> session -> config.loader cycle.
    """
    import kiro_crew.acp.client  # noqa: F401
    from kiro_crew.config.loader import KiroCrewConfig

    return inspect.signature(KiroCrewConfig.load().create_provider_factory())


def _positional_slots(sig: inspect.Signature) -> float:
    """How many positional args *sig* accepts (inf when it declares *args)."""
    kinds = [p.kind for p in sig.parameters.values()]
    if inspect.Parameter.VAR_POSITIONAL in kinds:
        return float("inf")
    return sum(
        k in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for k in kinds
    )


def _resolve(path: str):
    """ "pkg.mod:Cls.attr" / "pkg.mod:attr" -> the object."""
    mod, _, rest = path.partition(":")
    obj = importlib.import_module(mod)
    for part in filter(None, rest.split(".")):
        obj = getattr(obj, part)
    return obj


def _spawn_wrap_problem(problems: list[str]) -> None:
    """Our _spawn must AWAIT the sandbox wrap; the tables above cannot see this.

    ``wrap_argv`` and ``wrap_argv_async`` take the same arguments and return the
    same ``(argv, cleanup)``, so every check in this file passes either way —
    but the synchronous one raises whenever a loop is running ("performs
    blocking sandbox preparation and cannot run on an event loop") and ``_spawn``
    is a coroutine, so calling it failed every opencode spawn, on every session,
    behind a generic "Failed to create background session" log line.

    So run the real ``_spawn`` inside a real loop against the real upstream
    helper, stubbing only the binary lookup and the process launch, and require
    that it reaches the launch. mode="off" keeps the sandbox from probing the
    host: this is about which helper is awaited, not about sandboxing.
    """
    import asyncio
    import pathlib
    import tempfile

    from kiro_crew.acp.client import AcpClient

    import opencode_provider.provider as provider

    launched: list[list[str]] = []

    class _StubClient:
        # Only what our _spawn branch reads before it launches.
        _is_opencode = True
        _sandbox_mode = "off"
        _sandbox_cleanup = None
        _work_dir = pathlib.Path(tempfile.mkdtemp())

        async def _start_process(self, argv: list[str]) -> None:
            launched.append(argv)

    provider.resolve_opencode_bin = lambda: "/bin/true"  # skip the PATH lookup

    try:
        asyncio.run(AcpClient._spawn(_StubClient()))
    except Exception as exc:  # noqa: BLE001 — the raise is the failure
        problems.append(
            f"AcpClient._spawn: raised {exc!r} on a running event loop — the "
            f"opencode branch must `await wrap_argv_async(...)`; the sync "
            f"wrap_argv() refuses to run on a loop and _spawn is async"
        )
        return

    if not launched or "/bin/true" not in launched[0]:
        problems.append(f"AcpClient._spawn: finished without launching opencode (argv={launched})")


def _prerequisite_gate_problem(problems: list[str]) -> None:
    """The kiro-cli readiness gate must not fire: this image has no kiro-cli.

    Upstream derives readiness from the kiro-cli binary, and our backend is
    opencode — there is deliberately no kiro-cli to find. On a fresh data home
    that makes two of upstream's gates fire at a container that is working
    exactly as designed:

    * the dashboard SPA renders its "install kiro-cli" first-run gate instead of
      the app, because it branches on ``ready`` / ``initial_setup_complete``
      (``static/dist`` bundle, branch order in components/KiroPrerequisiteGate);
    * ``reject_if_kiro_unverified`` answers 503 for ``/api/models``, the
      destructive reruns (regenerate / edit-resend / rewind) and
      ``POST /v1/chat/completions`` — the last of which is the endpoint an
      OpenAI-compatible client talks to, so the container's headline use case
      breaks.

    So construct the real service against an empty data home, exactly as the
    dashboard does, and require the payload the SPA reads to describe a usable
    install. The predicates behind the 503s are checked too; nothing in this
    image can satisfy them by probing, so they must be answered without one.
    """
    import asyncio
    import tempfile
    from pathlib import Path

    from kiro_crew.kiro_prerequisite import KiroPrerequisiteService

    home = Path(tempfile.mkdtemp())

    async def snapshot() -> dict[str, object]:
        # An empty data home: a first boot with a fresh volume, which is the
        # default for a container and the state the gate fires on.
        with tempfile.TemporaryDirectory() as data_home:
            service = KiroPrerequisiteService(
                home=home,
                data_home=Path(data_home),
                environ={},  # no KIROCREW_HOME leaking in from the caller
            )
            payload = dict(await service.snapshot())
            payload["session_ready"] = await service.session_ready()
            payload["verified_ready"] = await service.verified_ready(max_age_secs=0.0)
            return payload

    payload = asyncio.run(snapshot())

    consequences = {
        "ready": "the SPA falls through to its first-run gate, and every "
        "reject_if_kiro_unverified route answers 503 (including POST /v1/chat/completions)",
        "initial_setup_complete": "the SPA renders the install-kiro-cli gate, not the app",
        "session_ready": "poll-driven spawn sites stay gated",
        "verified_ready": "regenerate / edit-resend / rewind answer 503",
    }
    for key, consequence in consequences.items():
        if not payload.get(key):
            problems.append(
                f"KiroPrerequisiteService reports {key}={payload.get(key)!r} for an "
                f"opencode gateway with no kiro-cli — {consequence}"
            )

    # Explicitly False, not merely absent: the key itself is newer than some
    # supported versions, and only `acp_supported === false` routes the SPA to
    # its "Kiro CLI update needed" gate (checked BEFORE initial_setup_complete).
    if payload.get("acp_supported") is False:
        problems.append(
            "KiroPrerequisiteService reports acp_supported=False for an opencode "
            "gateway — the SPA renders its 'Kiro CLI update needed' gate, which is "
            "checked BEFORE initial_setup_complete"
        )


def _bg_backend_routing_problem(problems: list[str]) -> None:
    """The gateway's CONFIG must name our backend, or _bg sessions spawn kiro-cli.

    ``BackgroundSessionRuntime._configured_bg_backend()`` reads
    ``cfg.agent.acp_backend`` — not the provider our factory patch injects — and
    hands the session to the multiplexed kiro-cli runtime whenever that value is
    in ``ACP_BACKENDS_ACP_RUNTIME`` (``{'', 'kas'}``) intersected with the
    selectable registry. A bare ``''`` is both, so auto-title and link-summary
    sessions spawned kiro-cli, which this image does not have:
    ``AcpRuntimeError: kiro-cli not found``, on every session, while interactive
    sessions worked fine.

    ``KIROCREW_ACP_BACKEND`` cannot fix that alone: upstream never reads it, so
    it reaches only our own patches. A value in the config is usable only if it
    also survives ``resolve_selected_backend`` (called inside every
    ``KiroCrewConfig.load()``, and re-run by bootstrap after policy narrowing) —
    which is what ``register_selectable_backend`` is for, and why upstream's
    ``_bg_runtime_backends()`` recomputes its intersection per call.

    So assert all four, through upstream's own code rather than our reading of
    it: the id is KNOWN in the canonical module and its ``acp.types`` re-export,
    it is selectable, the loaded config carries it, and the real ``_bg``
    predicate therefore keeps those sessions on the provider path.
    """
    from kiro_crew import acp_backends
    from kiro_crew.acp import types as t
    from kiro_crew.config.loader import KiroCrewConfig
    from kiro_crew.session import _bg_runtime_backends

    # Both, because acp.types re-exports the canonical set: reader modules that
    # `from kiro_crew.acp_backends import ACP_BACKENDS_KNOWN` (agent_sdk,
    # providers/mirrors) bind whichever copy is patched.
    for module, label in ((acp_backends, "acp_backends"), (t, "acp.types")):
        if "opencode" not in getattr(module, "ACP_BACKENDS_KNOWN", frozenset()):
            problems.append(
                f"{label}.ACP_BACKENDS_KNOWN: opencode missing — providers/acp.py "
                f"rejects an unknown acp_backend at construction, and "
                f"register_selectable_backend refuses to register an id that is not "
                f"known, so the config below can never name it"
            )

    if "opencode" not in acp_backends.selectable_backends():
        problems.append(
            "acp_backends.selectable_backends(): opencode missing — "
            "resolve_selected_backend() coerces agent.acp_backend back to kiro-cli "
            "inside KiroCrewConfig.load(), so the value cannot survive a config load "
            "(nor bootstrap's re-run of that gate after policy narrowing)"
        )

    cfg = KiroCrewConfig.load()
    if cfg.agent.acp_backend != "opencode":
        problems.append(
            f"KiroCrewConfig.load().agent.acp_backend == {cfg.agent.acp_backend!r} — "
            f"the gateway config does not name this build's backend, so background "
            f"sessions (auto-title, link-summary) take the multiplexed kiro-cli "
            f"runtime and fail with 'kiro-cli not found'"
        )
    elif cfg.agent.acp_backend in _bg_runtime_backends():
        problems.append(
            f"agent.acp_backend {cfg.agent.acp_backend!r} is still in "
            f"_bg_runtime_backends() ({sorted(_bg_runtime_backends())}) — "
            f"_bg_backend_supports_runtime() returns True and background sessions "
            f"bypass our factory for the multiplexed kiro-cli runtime"
        )


def _check() -> list[str]:
    import os

    os.environ["KIROCREW_ACP_BACKEND"] = "opencode"
    problems: list[str] = []

    for owner, attrs in ATTRS.items():
        try:
            target = _resolve(owner)
        except (ImportError, AttributeError) as exc:
            problems.append(f"{owner}: cannot resolve ({exc})")
            continue
        for attr in attrs:
            if not hasattr(target, attr):
                problems.append(f"{owner}.{attr}: missing from installed kirocrew")

    for owner, attrs in INIT_ATTRS.items():
        try:
            src = inspect.getsource(_resolve(owner).__init__)
        except (ImportError, AttributeError, OSError, TypeError) as exc:
            problems.append(f"{owner}.__init__: cannot read source ({exc})")
            continue
        # `self._x =`, `self._x: T =`, `self._x, self._y =` — not `==`.
        assigned = set(re.findall(r"self\.(_[A-Za-z0-9_]+)\s*(?::[^=\n]+)?=(?!=)", src))
        for attr in attrs:
            if attr not in assigned:
                problems.append(
                    f"{owner}.{attr}: no longer assigned in __init__ — "
                    f"our patches read it and will AttributeError"
                )

    # Upstream signatures, captured before install() swaps the callables out.
    before = {}
    for path in WRAPPED:
        try:
            before[path] = inspect.signature(_resolve(path))
        except (ImportError, AttributeError, ValueError) as exc:
            problems.append(f"{path}: cannot read upstream signature ({exc})")

    try:
        upstream_factory = _returned_factory_sig()
    except Exception as exc:  # noqa: BLE001 — any failure here is itself a contract break
        problems.append(f"create_provider_factory(): cannot introspect returned factory ({exc})")
        upstream_factory = None

    probe = before.get("kiro_crew.acp._dispatch:parse_session_update")
    if probe:
        for name in PROBE_KWARGS:
            if name not in probe.parameters:
                problems.append(
                    f"parse_session_update: dropped keyword '{name}' our probe passes"
                )

    from opencode_provider import install

    install()

    for path, upstream in before.items():
        patched = inspect.signature(_resolve(path))
        dropped = set(upstream.parameters) - set(patched.parameters)
        has_kwargs = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in patched.parameters.values()
        )
        if dropped and not has_kwargs:
            problems.append(
                f"{path}: our patch drops upstream parameter(s) {sorted(dropped)} — "
                f"upstream callers passing them will raise TypeError\n"
                f"    upstream: {upstream}\n"
                f"    patched:  {patched}"
            )

    # Same comparison one level down: the factory create_provider_factory
    # RETURNS. Positional arity is checked too, not just dropped names — the
    # outage was a wrapper that kept every parameter and still rejected the
    # session key because it declared them keyword-only.
    if upstream_factory is not None:
        try:
            patched_factory = _returned_factory_sig()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"create_provider_factory(): returned factory broke after install ({exc})")
        else:
            dropped = set(upstream_factory.parameters) - set(patched_factory.parameters)
            has_kwargs = any(
                p.kind is inspect.Parameter.VAR_KEYWORD
                for p in patched_factory.parameters.values()
            )
            if dropped and not has_kwargs:
                problems.append(
                    f"create_provider_factory() returned factory: our wrapper drops upstream "
                    f"parameter(s) {sorted(dropped)} — upstream callers passing them will raise "
                    f"TypeError\n    upstream: {upstream_factory}\n    patched:  {patched_factory}"
                )
            want, got = _positional_slots(upstream_factory), _positional_slots(patched_factory)
            if got < want:
                problems.append(
                    f"create_provider_factory() returned factory: our wrapper accepts {got} "
                    f"positional arg(s), upstream accepts {want} — every upstream call site "
                    f"passes the session key positionally and will raise TypeError\n"
                    f"    upstream: {upstream_factory}\n    patched:  {patched_factory}"
                )

    # Post-install: opencode is in KNOWN and in nothing else.
    from kiro_crew.acp import types as t

    if "opencode" not in getattr(t, "ACP_BACKENDS_KNOWN", frozenset()):
        problems.append("ACP_BACKENDS_KNOWN: install() failed to register opencode")
    for name in EXCLUDED:
        members = getattr(t, name, None)
        if members is None:
            problems.append(
                f"acp.types.{name}: gone — install.py's exclusion no longer means "
                f"anything; find what replaced it and re-check the exclusion"
            )
        elif "opencode" in members:
            problems.append(f"acp.types.{name}: now contains opencode — must not")

    _spawn_wrap_problem(problems)
    _prerequisite_gate_problem(problems)
    _bg_backend_routing_problem(problems)

    return problems


def test_kirocrew_contract():
    """The symbols we monkey-patch still exist and still take the same arguments."""
    proc = subprocess.run([sys.executable, __file__], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, "\n" + proc.stdout + proc.stderr


if __name__ == "__main__":
    found = _check()
    for problem in found:
        print(f"✗ {problem}")
    print(f"\n{len(found)} contract violation(s)")
    sys.exit(1 if found else 0)
