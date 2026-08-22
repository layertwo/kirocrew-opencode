"""opencode_provider — OpenCode ACP backend for KiroCrew.

Drop-in package that makes KiroCrew drive the ``opencode acp`` subprocess
instead of kiro-cli, by monkey-patching ``AcpClient`` and the provider
factory at runtime. No source patches, no fork required.

Usage::

    import opencode_provider
    opencode_provider.install()  # before kirocrew gateway boots

Or use the bundled ``gateway.py`` entry point.
"""

from .install import install, is_installed

__all__ = ["install", "is_installed"]