"""Configuration for the OpenCode ACP provider.

Settings are read from environment variables at ``install()`` time.
"""

from __future__ import annotations

import os
import shutil

ACP_BACKEND_OPENCODE = "opencode"


def is_opencode_selected() -> bool:
    """True when KIROCREW_ACP_BACKEND=opencode is set."""
    return os.environ.get("KIROCREW_ACP_BACKEND", "").strip().lower() == ACP_BACKEND_OPENCODE


def resolve_opencode_bin() -> str | None:
    """Resolve the opencode binary path (OPENCODE_BIN override, else PATH)."""
    return os.environ.get("OPENCODE_BIN", "").strip() or shutil.which("opencode")
