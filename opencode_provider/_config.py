"""Configuration for the OpenCode ACP provider.

All settings are read from environment variables at ``install()`` time.
"""

from __future__ import annotations

import os

ACP_BACKEND_OPENCODE = "opencode"

OPENCODE_BIN_DEFAULT = "opencode"
OPENCODE_SUBCMD = "acp"

PROTOCOL_VERSION_OPENCODE = 1


def read_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def is_opencode_selected() -> bool:
    """True when KIROCREW_ACP_BACKEND=opencode is set."""
    return read_env("KIROCREW_ACP_BACKEND", "").lower() == ACP_BACKEND_OPENCODE


def resolve_opencode_bin() -> str | None:
    """Resolve the opencode binary path (simple PATH lookup)."""
    import shutil

    override = read_env("OPENCODE_BIN")
    if override:
        return override
    return shutil.which(OPENCODE_BIN_DEFAULT)