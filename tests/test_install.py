"""Tests for opencode_provider.install orchestration logic.

Tests the install() entry point, _patch_types and _patch_factory using mock
modules. The provider.py patches (AcpClient, AcpProvider) require a real
kiro_crew install and are covered by test_kirocrew_contract.py.

Because the mocks are fabricated here, nothing in this file can fail on an
upstream change — that is test_kirocrew_contract.py's job.
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

    kiro_crew = types.ModuleType("kiro_crew")
    kiro_crew_acp = types.ModuleType("kiro_crew.acp")
    kiro_crew_acp_types = types.ModuleType("kiro_crew.acp.types")
    kiro_crew_acp_types.ACP_BACKEND_CLAUDE = "claude"
    kiro_crew_acp_types.ACP_BACKENDS_KNOWN = frozenset({"claude", ""})
    kiro_crew_acp_types.ACP_BACKENDS_SESSION_SHARING = frozenset({""})
    kiro_crew_acp_types.ACP_BACKENDS_STEER = frozenset({"", "kas"})

    kiro_crew_acp_client = types.ModuleType("kiro_crew.acp.client")

    # Canonical home of ACP_BACKENDS_KNOWN; acp.types re-exports it. The real
    # register_selectable_backend rejects an id outside that set, which is why
    # _patch_types() has to patch the canonical copy before _patch_bg_backend()
    # registers — so the mock rejects it too, rather than accepting anything.
    kiro_crew_acp_backends = types.ModuleType("kiro_crew.acp_backends")
    kiro_crew_acp_backends.ACP_BACKENDS_KNOWN = kiro_crew_acp_types.ACP_BACKENDS_KNOWN

    def register_selectable_backend(backend):
        if backend not in kiro_crew_acp_backends.ACP_BACKENDS_KNOWN:
            raise ValueError(
                f"cannot register unknown ACP backend {backend!r}; "
                f"known: {sorted(kiro_crew_acp_backends.ACP_BACKENDS_KNOWN)}"
            )

    kiro_crew_acp_backends.register_selectable_backend = register_selectable_backend

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

    async def wrap_argv_async(argv, **kwargs):
        return argv, None

    kiro_crew_acp_client.wrap_argv_async = wrap_argv_async

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

    kiro_crew_prerequisite = types.ModuleType("kiro_crew.kiro_prerequisite")

    class KiroPrerequisiteService:
        # Keyword-only, like upstream's — install.py's patch relies on that and
        # the contract test pins it against the real class.
        def __init__(self, *, assume_ready: bool = False, **kwargs):
            self.assume_ready = assume_ready

    kiro_crew_prerequisite.KiroPrerequisiteService = KiroPrerequisiteService

    kiro_crew_config = types.ModuleType("kiro_crew.config")
    kiro_crew_config_loader = types.ModuleType("kiro_crew.config.loader")

    class MockKiroCrewConfig:
        def __init__(self):
            # `_patch_bg_backend` sets cfg.agent.acp_backend on the loaded
            # object; `""` is the kiro spelling upstream normalizes an absent
            # value to.
            self.agent = types.SimpleNamespace(acp_backend="")

        @classmethod
        def load(cls):
            return cls()

        def create_provider_factory(self):
            # Mirrors upstream's `_acp(session_key=None, agent=None, ...)`. A
            # `**kwargs`-only mock here is what let a keyword-only wrapper ship:
            # it encoded the same mistake as the code and then confirmed it.
            def factory(session_key=None, agent=None, cwd=None, **kwargs):
                return types.SimpleNamespace()

            return factory

    kiro_crew_config_loader.KiroCrewConfig = MockKiroCrewConfig

    # Wire the tree
    kiro_crew.acp = kiro_crew_acp
    kiro_crew.acp_backends = kiro_crew_acp_backends
    kiro_crew.config = kiro_crew_config
    kiro_crew.kiro_prerequisite = kiro_crew_prerequisite
    kiro_crew_acp.types = kiro_crew_acp_types
    kiro_crew_acp.client = kiro_crew_acp_client
    kiro_crew_providers.acp = kiro_crew_providers_acp

    # Save and install all
    names = [
        "kiro_crew",
        "kiro_crew.acp",
        "kiro_crew.acp.types",
        "kiro_crew.acp.client",
        "kiro_crew.acp_backends",
        "kiro_crew.config",
        "kiro_crew.config.loader",
        "kiro_crew.kiro_prerequisite",
        "kiro_crew.providers",
        "kiro_crew.providers.acp",
    ]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules["kiro_crew"] = kiro_crew
    sys.modules["kiro_crew.acp"] = kiro_crew_acp
    sys.modules["kiro_crew.acp.types"] = kiro_crew_acp_types
    sys.modules["kiro_crew.acp.client"] = kiro_crew_acp_client
    sys.modules["kiro_crew.acp_backends"] = kiro_crew_acp_backends
    sys.modules["kiro_crew.config"] = kiro_crew_config
    sys.modules["kiro_crew.kiro_prerequisite"] = kiro_crew_prerequisite
    sys.modules["kiro_crew.config.loader"] = kiro_crew_config_loader
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


def test_install_names_the_backend_in_the_loaded_config(mock_kiro_crew, monkeypatch):
    """_bg sessions read cfg.agent.acp_backend, so it has to carry our backend.

    The mock's register_selectable_backend rejects an id outside
    ACP_BACKENDS_KNOWN exactly as upstream does, so this also pins the ordering:
    _patch_types() must run first, or registration raises.
    """
    monkeypatch.setenv("KIROCREW_ACP_BACKEND", "opencode")
    install()

    loader_mod = sys.modules["kiro_crew.config.loader"]
    assert loader_mod.KiroCrewConfig.load().agent.acp_backend == "opencode"

    # Canonical set, not just the acp.types re-export.
    backends_mod = sys.modules["kiro_crew.acp_backends"]
    assert "opencode" in backends_mod.ACP_BACKENDS_KNOWN
    assert "opencode" in sys.modules["kiro_crew.acp.types"].ACP_BACKENDS_KNOWN


def test_install_patches_factory(mock_kiro_crew, monkeypatch):
    monkeypatch.setenv("KIROCREW_ACP_BACKEND", "opencode")
    install()

    loader_mod = sys.modules["kiro_crew.config.loader"]
    cfg = loader_mod.KiroCrewConfig()
    factory = cfg.create_provider_factory()
    assert factory() is not None
    # Upstream passes the session key POSITIONALLY at all nine call sites
    # (session.py:1336/1867/2176/2831/3326, cli_chat.py:273, eval/runner.py:263,
    # eval/judge.py:58, auto_improvement agent_runner.py:1341).
    assert factory("bg-session-key", agent="kiro") is not None
    assert factory("", agent=None, cwd=None) is not None


def test_upstream_serves_opencode_is_false_without_native_support(mock_kiro_crew):
    """The guard must NOT retire our patches on a build lacking the backend.

    A spurious True here is the dangerous direction: it skips the client and
    provider patches, so nothing can spawn opencode at all — while every log
    line still reports a successful install.
    """
    from opencode_provider.install import _upstream_serves_opencode

    assert _upstream_serves_opencode() is False


def test_upstream_serves_opencode_needs_all_three_facts(mock_kiro_crew):
    """Registry membership alone is not enough: a build can name the id without
    the client and provider support behind it."""
    from opencode_provider.install import _upstream_serves_opencode

    sys.modules["kiro_crew.acp_backends"].ACP_BACKENDS_KNOWN = frozenset({"", "opencode"})
    assert _upstream_serves_opencode() is False

    sys.modules["kiro_crew.acp.client"].AcpClient._is_opencode = property(lambda self: True)
    assert _upstream_serves_opencode() is False

    sys.modules["kiro_crew.providers.acp"].AcpProvider.is_opencode_backend = property(
        lambda self: True
    )
    assert _upstream_serves_opencode() is True


def test_upstream_serves_opencode_falls_back_to_patching(mock_kiro_crew, monkeypatch):
    """An unreadable probe must degrade to the old behaviour, not raise.

    install() runs before `from kiro_crew.cli import main`, so an exception here
    takes the gateway down with no fallback to kiro-cli.
    """
    from opencode_provider.install import _upstream_serves_opencode

    monkeypatch.delitem(sys.modules, "kiro_crew.providers.acp")

    assert _upstream_serves_opencode() is False


def test_install_applies_client_patches_on_a_non_native_build(mock_kiro_crew, monkeypatch):
    monkeypatch.setenv("KIROCREW_ACP_BACKEND", "opencode")
    client = sys.modules["kiro_crew.acp.client"].AcpClient
    before = client.__dict__["_spawn"]

    install()

    assert client.__dict__["_spawn"] is not before


def test_install_retires_clobbering_patches_on_native_build(mock_kiro_crew, monkeypatch):
    """On a kirocrew that serves opencode itself, ours must not overwrite it.

    The failure this prevents is silent: our replacements are the older
    protocol, every name they touch still exists, so nothing raises and the
    suites stay green while sessions run the wrong implementation.
    """
    monkeypatch.setenv("KIROCREW_ACP_BACKEND", "opencode")
    client = sys.modules["kiro_crew.acp.client"].AcpClient
    provider = sys.modules["kiro_crew.providers.acp"].AcpProvider
    loader_mod = sys.modules["kiro_crew.config.loader"]

    # Pose as a native build: id registered, and both classes own their support.
    sys.modules["kiro_crew.acp_backends"].ACP_BACKENDS_KNOWN = frozenset({"", "opencode"})
    client._is_opencode = property(lambda self: True)
    provider.is_opencode_backend = property(lambda self: True)

    spawn_before = client.__dict__["_spawn"]
    factory_before = loader_mod.KiroCrewConfig.create_provider_factory

    install()

    assert is_installed() is True
    # Ours stayed out of the way...
    assert client.__dict__["_spawn"] is spawn_before
    assert loader_mod.KiroCrewConfig.create_provider_factory is factory_before
    # ...while the patches that are still ours applied anyway.
    assert loader_mod.KiroCrewConfig.load().agent.acp_backend == "opencode"
