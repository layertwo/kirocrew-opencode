"""Contract checks against the REAL kirocrew.

The other test modules fabricate ``kiro_crew`` with ``types.ModuleType`` and
inject it into ``sys.modules``, so they pass no matter what upstream does. This
module imports the installed kirocrew instead, and fails when a symbol we
monkey-patch is renamed, removed, or re-signatured — which is the way a version
bump actually breaks this provider.

Runs in a subprocess: ``install()`` mutates kirocrew's classes process-wide and
latches ``_installed``, so it needs an interpreter the other tests haven't
touched.

Covers module- and class-level symbols plus the signatures of everything we
wrap. Instance attributes set in ``AcpClient.__init__`` (``_extra_env``,
``_work_dir``, ``_tool_call_params`` …) are NOT covered — checking those needs a
constructed client, which needs real config.
"""

from __future__ import annotations

import importlib
import inspect
import subprocess
import sys

# Attributes provider.py / install.py read or replace. Existence only.
ATTRS: dict[str, list[str]] = {
    "kiro_crew.acp.types": ["ACP_BACKENDS_KNOWN"],
    "kiro_crew.acp.client": [
        "AcpClient", "AcpError", "wrap_argv", "METHOD_INITIALIZE", "CLIENT_NAME",
        "CLIENT_VERSION", "ACP_CLIENT_CAPABILITIES", "_INIT_TIMEOUT",
        "METHOD_SESSION_LOAD", "METHOD_SESSION_NEW",
        # used by our _start_process
        "_STDOUT_BUFFER_LIMIT", "KIROCREW_SPAWNED_ENV", "KIROCREW_SPAWNED_VALUE",
    ],
    "kiro_crew.acp.client:AcpClient": [
        "backend", "_spawn", "_initialize_session",
        "_extract_tool_call_refinement", "supports_steer",
        "_reject_unknown_server_request", "send_command", "stream_command",
        "_send_request", "_wait_for_response", "_send_response", "_send_prompt",
        "_read_prompt_response", "_dispatch_events", "ensure_ready",
        "_pooled_mcp_servers", "_capture_available_models", "stream_events",
        "_drain_stderr",
    ],
    "kiro_crew.sandbox": ["create_subprocess_limited", "scrub_agent_denied_env", "wrap_argv"],
    "kiro_crew.env": ["augmented_path"],
    "kiro_crew.platform_compat": ["IS_POSIX"],
    "kiro_crew.providers.acp:AcpProvider": [
        "start", "_apply_effort_overlay", "_apply_tool_search_overlay",
        "stream_command", "_to_llm_event",
    ],
    "kiro_crew.config.loader:KiroCrewConfig": ["create_provider_factory"],
    "kiro_crew.session:SessionManager": ["_bg_provider_is_kiro"],
    "kiro_crew.acp._dispatch": ["parse_session_update"],
}

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
    "kiro_crew.session:SessionManager._bg_provider_is_kiro",
]


def _resolve(path: str):
    """"pkg.mod:Cls.attr" / "pkg.mod:attr" -> the object."""
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

    # Upstream signatures, captured before install() swaps the callables out.
    before = {}
    for path in WRAPPED:
        try:
            before[path] = inspect.signature(_resolve(path))
        except (ImportError, AttributeError, ValueError) as exc:
            problems.append(f"{path}: cannot read upstream signature ({exc})")

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

    return problems


def test_kirocrew_contract():
    """The symbols we monkey-patch still exist and still take the same arguments."""
    proc = subprocess.run(
        [sys.executable, __file__], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, "\n" + proc.stdout + proc.stderr


if __name__ == "__main__":
    found = _check()
    for problem in found:
        print(f"✗ {problem}")
    print(f"\n{len(found)} contract violation(s)")
    sys.exit(1 if found else 0)
