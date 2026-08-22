"""install.py — Install the OpenCode ACP backend into KiroCrew.

Entry point called before the gateway boots. Adds the ACP_BACKEND_OPENCODE
constant to acp.types, patches the provider factory to inject the opencode
backend, and delegates the ACP provider/client/dispatch patches to
``provider.py``.

Call ``install()`` BEFORE ``kirocrew gateway`` initialises its provider factory.
"""

from __future__ import annotations

import logging

from ._config import ACP_BACKEND_OPENCODE, is_opencode_selected

logger = logging.getLogger(__name__)

_installed = False


def is_installed() -> bool:
    return _installed


def install() -> None:
    """Apply all monkey-patches. Safe to call multiple times (idempotent)."""
    global _installed
    if _installed:
        return
    if not is_opencode_selected():
        logger.info(
            "opencode_provider: KIROCREW_ACP_BACKEND != 'opencode' — skipping install"
        )
        return

    _patch_types()
    _patch_factory()
    _patch_bg_sessions()

    from .provider import patch_client, patch_provider, patch_dispatch_raw_params

    patch_client()
    patch_provider()
    patch_dispatch_raw_params()

    _installed = True
    logger.info("opencode_provider installed — OpenCode ACP backend active")


def _patch_types() -> None:
    """Add ACP_BACKEND_OPENCODE to acp.types and ACP_BACKENDS_KNOWN.

    Targets 0.3.0+ which has the membership-set architecture.
    """
    from kiro_crew.acp import types as t

    if not hasattr(t, "ACP_BACKEND_OPENCODE"):
        t.ACP_BACKEND_OPENCODE = ACP_BACKEND_OPENCODE
        logger.info("opencode_provider: added ACP_BACKEND_OPENCODE to acp.types")

    known = getattr(t, "ACP_BACKENDS_KNOWN", None)
    if known is not None and ACP_BACKEND_OPENCODE not in known:
        t.ACP_BACKENDS_KNOWN = known | {ACP_BACKEND_OPENCODE}
        logger.info("opencode_provider: added opencode to ACP_BACKENDS_KNOWN")

    # NOT added to ACP_BACKENDS_SESSION_SHARING, ACP_BACKENDS_STEER,
    # ACP_BACKENDS_INTERNAL_SANDBOX, or ACP_BACKENDS_ACP_RUNTIME —
    # OpenCode is one-process-per-session like claude, not multiplexed.


def _patch_factory() -> None:
    """Patch the provider factory to inject acp_backend=opencode."""
    from kiro_crew.config.loader import KiroCrewConfig
    from kiro_crew.acp.types import ACP_BACKEND_OPENCODE

    _orig_create = KiroCrewConfig.create_provider_factory

    def create_provider_factory(self):  # type: ignore[no-untyped-def]
        factory = _orig_create(self)

        def _wrapped_factory(**kwargs):
            provider = factory(**kwargs)
            if hasattr(provider, "_client"):
                provider._client._acp_backend = ACP_BACKEND_OPENCODE
            return provider

        return _wrapped_factory

    KiroCrewConfig.create_provider_factory = create_provider_factory
    logger.info("opencode_provider: provider factory patched ✅")


def _patch_bg_sessions() -> None:
    """Patch _bg_provider_is_kiro so bg sessions route through the factory.

    Without this, auto-title / link-summary calls bypass the patched factory
    and hit kiro-cli → Anthropic, failing with quota errors when no Anthropic
    key is configured.
    """
    from kiro_crew.session import SessionManager

    def _bg_provider_is_not_kiro(self) -> bool:  # type: ignore[no-untyped-def]
        return False

    SessionManager._bg_provider_is_kiro = _bg_provider_is_not_kiro
    logger.info("opencode_provider: bg session routing patched ✅")