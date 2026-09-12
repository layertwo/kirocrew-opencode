"""install.py — Install the OpenCode ACP backend into KiroCrew.

Entry point called before the gateway boots. Adds the ACP_BACKEND_OPENCODE
constant to acp.types, patches the provider factory to inject the opencode
backend, and delegates the ACP provider/client/dispatch patches to
``provider.py``.

Call ``install()`` BEFORE ``kirocrew gateway`` initialises its provider factory.
"""

from __future__ import annotations

import logging

from opencode_provider._config import ACP_BACKEND_OPENCODE, is_opencode_selected

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
        logger.info("opencode_provider: KIROCREW_ACP_BACKEND != 'opencode' — skipping install")
        return

    _patch_types()
    _patch_factory()
    _patch_prerequisite()

    from opencode_provider.provider import patch_client, patch_dispatch_raw_params, patch_provider

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
    # ACP_BACKENDS_INTERNAL_SANDBOX, ACP_BACKENDS_ACP_RUNTIME, or
    # ACP_BACKENDS_KIRO_IDENTITY_STORE —
    # OpenCode is one-process-per-session like claude, not multiplexed.
    #
    # Staying out of ACP_BACKENDS_ACP_RUNTIME is what routes _bg sessions
    # (auto-title, link-summary) to SessionManager._provider_backed_bg_session
    # and thus through our patched factory, instead of to kiro-cli → Anthropic.
    # The exclusion above IS that patch. Contract test asserts the sets still
    # exist and still exclude us.


def _patch_factory() -> None:
    """Patch the provider factory to inject acp_backend=opencode."""
    from kiro_crew.acp.types import ACP_BACKEND_OPENCODE
    from kiro_crew.config.loader import KiroCrewConfig

    _orig_create = KiroCrewConfig.create_provider_factory

    def create_provider_factory(self):  # type: ignore[no-untyped-def]
        factory = _orig_create(self)

        # *args, not just **kwargs: upstream's returned factory declares
        # `_acp(session_key=None, agent=None, ...)` and every one of its nine
        # call sites passes the session key POSITIONALLY (session.py 1336/1867/
        # 2176/2831/3326, cli_chat.py:273, eval/runner.py:263, eval/judge.py:58,
        # auto_improvement agent_runner.py:1341). A keyword-only wrapper takes
        # the whole gateway down with "takes 0 positional arguments but 1 was
        # given" — background sessions, the warm pool, and every cold start.
        def _wrapped_factory(*args, **kwargs):
            provider = factory(*args, **kwargs)
            if hasattr(provider, "_client"):
                provider._client._acp_backend = ACP_BACKEND_OPENCODE
            return provider

        return _wrapped_factory

    KiroCrewConfig.create_provider_factory = create_provider_factory
    logger.info("opencode_provider: provider factory patched ✅")


def _patch_prerequisite() -> None:
    """Answer the kiro-cli readiness gate as satisfied — there is no kiro-cli.

    Upstream derives readiness from the kiro-cli binary, which an opencode image
    deliberately does not have, so the probe can never pass. On a fresh data home
    that gates a container working exactly as designed:

    * the dashboard SPA branches on ``ready`` / ``initial_setup_complete`` and
      renders its "install kiro-cli" first-run gate instead of the app;
    * ``dashboard.kiro_readiness.reject_if_kiro_unverified`` answers 503 for
      ``/api/models``, the destructive reruns (regenerate, edit-resend, rewind)
      and ``POST /v1/chat/completions`` — the OpenAI-compatible endpoint, i.e.
      the headline use case for this container;
    * the agent-spec overlay reports a repair gate for specs this home never had.

    Upstream's own ``assume_ready`` is the switch for it: the service reports an
    established, authenticated install without probing, and every other consumer
    of the flag short-circuits to a no-op (no identity probe, no session
    retirement on identity change, no ``kiro-cli update`` run). Forcing it at
    construction covers every construction site — the two in
    ``dashboard/server.py`` and the Slack gateway's — because all of them go
    through this ``__init__``.

    Scoped by ``install()``: this only ever applies when the backend IS opencode,
    so a kiro-cli gateway keeps its real fail-closed readiness probe.
    """
    from kiro_crew.kiro_prerequisite import KiroPrerequisiteService

    _orig_init = KiroPrerequisiteService.__init__

    def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
        # Upstream's __init__ is keyword-only and every call site passes
        # assume_ready as a keyword (dashboard/server.py:3389, 4506), so this
        # override is the whole patch. Signature-compatible by **kwargs; the
        # contract test pins that upstream stays keyword-only.
        kwargs["assume_ready"] = True
        _orig_init(self, **kwargs)

    KiroPrerequisiteService.__init__ = __init__
    logger.info("opencode_provider: kiro-cli readiness gate bypassed ✅")
