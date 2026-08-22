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

import sys
import os

# ── Encoding fix (same as lenovo1996's gateway.py) ───────────────────────────
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure opencode_provider is importable
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 1. Install the OpenCode provider patch BEFORE importing kiro_crew
import opencode_provider
opencode_provider.install()

# 3. Boot KiroCrew normally
from kiro_crew.cli import main


def _entry():
    # Inject 'gateway' subcommand if not already present
    if len(sys.argv) == 1 or sys.argv[1] != "gateway":
        sys.argv.insert(1, "gateway")
    main()


if __name__ == "__main__":
    _entry()