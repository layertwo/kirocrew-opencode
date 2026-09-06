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
        "wrap_argv",
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
    "kiro_crew.sandbox": ["create_subprocess_limited", "scrub_agent_denied_env", "wrap_argv"],
    "kiro_crew.env": ["augmented_path"],
    "kiro_crew.platform_compat": ["IS_POSIX"],
    "kiro_crew.providers.acp:AcpProvider": [
        "start",
        "_apply_effort_overlay",
        "_apply_tool_search_overlay",
        "stream_command",
        "_to_llm_event",
    ],
    "kiro_crew.config.loader:KiroCrewConfig": ["create_provider_factory"],
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
]


def _resolve(path: str):
    """ "pkg.mod:Cls.attr" / "pkg.mod:attr" -> the object."""
    mod, _, rest = path.partition(":")
    obj = importlib.import_module(mod)
    for part in filter(None, rest.split(".")):
        obj = getattr(obj, part)
    return obj


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
