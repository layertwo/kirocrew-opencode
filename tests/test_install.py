"""Tests for opencode_provider.install orchestration logic.

Tests the install() entry point, _patch_types, _patch_factory, and
_patch_bg_sessions using mock modules. The provider.py patches (AcpClient,
AcpProvider, _dispatch) require a real kiro_crew install and are tested
separately in test_dispatch.py.
"""

import sys
import types

import pytest

from opencode_provider import install, is_installed


@pytest.fixture(autouse=True)
def reset_state():
    """Reset the _installed flag before AND after each test."""
    import importlib
    mod = importlib.import_module("opencode_provider.install")
    mod._installed = False
    yield
    mod._installed = False


@pytest.fixture
def mock_kiro_crew(request):
    """Inject a mock kiro_crew package into sys.modules with enough stubs
    for the install() orchestration + provider patches to succeed."""
    # Build mock module tree
    modules = {}

    kiro_crew = types.ModuleType("kiro_crew")
    kiro_crew_acp = types.ModuleType("kiro_crew.acp")
    kiro_crew_acp_types = types.ModuleType("kiro_crew.acp.types")
    kiro_crew_acp_types.ACP_BACKEND_CLAUDE = "claude"
    kiro_crew_acp_types.ACP_BACKENDS_KNOWN = frozenset({"claude", ""})
    kiro_crew_acp_types.ACP_BACKENDS_SESSION_SHARING = frozenset({""})
    kiro_crew_acp_types.ACP_BACKENDS_STEER = frozenset({"", "kas"})

    kiro_crew_acp_client = types.ModuleType("kiro_crew.acp.client")

    class AcpError(Exception):
        pass

    class AcpClient:
        backend = ""
        _spawn = staticmethod(lambda self: None)
        _initialize_session = staticmethod(lambda self: None)
        _extract_tool_call_refinement = staticmethod(lambda self, msg: None)
        supports_steer = property(lambda self: False)
        _reject_unknown_server_request = staticmethod(lambda self, msg: None)
        send_command = staticmethod(lambda self, cmd, args=None: "")
        stream_command = staticmethod(lambda self, cmd, timeout=60.0: iter([]))

    kiro_crew_acp_client.AcpClient = AcpClient
    kiro_crew_acp_client.AcpError = AcpError
    kiro_crew_acp_client.wrap_argv = lambda argv, **kw: (argv, None)

    kiro_crew_providers = types.ModuleType("kiro_crew.providers")
    kiro_crew_providers_acp = types.ModuleType("kiro_crew.providers.acp")

    class AcpProvider:
        _client = types.SimpleNamespace(backend="")
        start = staticmethod(lambda self: None)
        _apply_effort_overlay = staticmethod(lambda self: None)
        _apply_tool_search_overlay = staticmethod(lambda self: None)
        stream_command = staticmethod(lambda self, cmd: iter([]))
        _to_llm_event = staticmethod(lambda self, e: e)

    kiro_crew_providers_acp.AcpProvider = AcpProvider

    kiro_crew_acp_dispatch = types.ModuleType("kiro_crew.acp._dispatch")
    kiro_crew_acp_dispatch._build_tool_refinement_event = lambda *a, **kw: None
    kiro_crew_acp_dispatch.parse_session_update = lambda *a, **kw: []

    kiro_crew_config = types.ModuleType("kiro_crew.config")
    kiro_crew_config_loader = types.ModuleType("kiro_crew.config.loader")

    class MockKiroCrewConfig:
        def create_provider_factory(self):
            def factory(**kwargs):
                return types.SimpleNamespace()
            return factory

    kiro_crew_config_loader.KiroCrewConfig = MockKiroCrewConfig

    kiro_crew_session = types.ModuleType("kiro_crew.session")

    class MockSessionManager:
        def _bg_provider_is_kiro(self):
            return True

    kiro_crew_session.SessionManager = MockSessionManager

    # Wire the tree
    kiro_crew.acp = kiro_crew_acp
    kiro_crew.config = kiro_crew_config
    kiro_crew.session = kiro_crew_session
    kiro_crew_acp.types = kiro_crew_acp_types
    kiro_crew_acp.client = kiro_crew_acp_client
    kiro_crew_acp._dispatch = kiro_crew_acp_dispatch
    kiro_crew_providers.acp = kiro_crew_providers_acp

    # Save and install all
    names = [
        "kiro_crew", "kiro_crew.acp", "kiro_crew.acp.types",
        "kiro_crew.acp.client", "kiro_crew.acp._dispatch",
        "kiro_crew.config", "kiro_crew.config.loader",
        "kiro_crew.session",
        "kiro_crew.providers", "kiro_crew.providers.acp",
    ]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules["kiro_crew"] = kiro_crew
    sys.modules["kiro_crew.acp"] = kiro_crew_acp
    sys.modules["kiro_crew.acp.types"] = kiro_crew_acp_types
    sys.modules["kiro_crew.acp.client"] = kiro_crew_acp_client
    sys.modules["kiro_crew.acp._dispatch"] = kiro_crew_acp_dispatch
    sys.modules["kiro_crew.config"] = kiro_crew_config
    sys.modules["kiro_crew.config.loader"] = kiro_crew_config_loader
    sys.modules["kiro_crew.session"] = kiro_crew_session
    sys.modules["kiro_crew.providers"] = kiro_crew_providers
    sys.modules["kiro_crew.providers.acp"] = kiro_crew_providers_acp

    yield kiro_crew

    for name, mod in saved.items():
        if mod is not None:
            sys.modules[name] = mod
        else:
            sys.modules.pop(name, None)


def test_install_skips_when_not_selected(monkeypatch):
    monkeypatch.delenv("KIROCREW_ACP_BACKEND", raising=False)
    install()
    assert is_installed() is False


def test_install_patches_types(mock_kiro_crew, monkeypatch):
    monkeypatch.setenv("KIROCREW_ACP_BACKEND", "opencode")
    install()

    types_mod = sys.modules["kiro_crew.acp.types"]
    assert hasattr(types_mod, "ACP_BACKEND_OPENCODE")
    assert types_mod.ACP_BACKEND_OPENCODE == "opencode"
    assert "opencode" in types_mod.ACP_BACKENDS_KNOWN
    assert is_installed() is True


def test_install_is_idempotent(mock_kiro_crew, monkeypatch):
    monkeypatch.setenv("KIROCREW_ACP_BACKEND", "opencode")
    install()
    install()
    assert is_installed() is True


def test_install_patches_bg_sessions(mock_kiro_crew, monkeypatch):
    monkeypatch.setenv("KIROCREW_ACP_BACKEND", "opencode")
    assert not is_installed(), "_installed should be False before install()"
    install()
    assert is_installed(), "_installed should be True after install()"

    session_mod = sys.modules["kiro_crew.session"]
    SessionManager = session_mod.SessionManager
    assert SessionManager.__name__ == "MockSessionManager", f"got {SessionManager}"
    mgr = SessionManager()
    result = mgr._bg_provider_is_kiro()
    assert result is False, f"expected False, got {result}"


def test_install_patches_factory(mock_kiro_crew, monkeypatch):
    monkeypatch.setenv("KIROCREW_ACP_BACKEND", "opencode")
    install()

    loader_mod = sys.modules["kiro_crew.config.loader"]
    cfg = loader_mod.KiroCrewConfig()
    factory = cfg.create_provider_factory()
    result = factory()
    assert result is not None


def test_opencode_not_in_sharing_set(mock_kiro_crew, monkeypatch):
    """OpenCode must NOT be in ACP_BACKENDS_SESSION_SHARING or STEER."""
    monkeypatch.setenv("KIROCREW_ACP_BACKEND", "opencode")
    install()

    types_mod = sys.modules["kiro_crew.acp.types"]
    assert "opencode" not in types_mod.ACP_BACKENDS_SESSION_SHARING
    assert "opencode" not in types_mod.ACP_BACKENDS_STEER