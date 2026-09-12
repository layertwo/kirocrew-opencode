#!/usr/bin/env python3
"""gateway.py — Start KiroCrew gateway with the OpenCode ACP backend.

Usage:
    KIROCREW_ACP_BACKEND=opencode python gateway.py

    # Or pass through to kirocrew gateway:
    KIROCREW_ACP_BACKEND=opencode python gateway.py gateway

The OpenCode backend spawns ``opencode acp --cwd <work_dir>`` per session,
speaks numeric ACP protocol version 1, and reuses KiroCrew's own AcpClient
for all event parsing, tool-call extraction, and permission flows.
"""

import os
import sys

# Ensure opencode_provider is importable
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 1. Install the OpenCode provider patch BEFORE importing kiro_crew.
# Both imports below carry `noqa: E402` on purpose, and the order here IS the
# feature: the sys.path insert above has to run before opencode_provider can be
# imported at all, and install() has to run before kiro_crew.cli is imported, or
# the patches land after the factory that should have used them. Reordering to
# satisfy E402 would import kiro_crew.cli unpatched — do not.
import opencode_provider  # noqa: E402

opencode_provider.install()

# 2. Boot KiroCrew normally
from kiro_crew.cli import main  # noqa: E402


def _entry():
    # Inject 'gateway' subcommand if not already present
    if len(sys.argv) == 1 or sys.argv[1] != "gateway":
        sys.argv.insert(1, "gateway")
    main()


if __name__ == "__main__":
    _entry()
