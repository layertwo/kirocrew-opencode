"""Tests for the _dispatch raw_params_cache fix in provider.py.

The root-cause fix for deny-by-default: parse_session_update is patched to
refresh raw_params_cache from tool_call_update events, which the original
does not do.
"""

import sys
import types

import pytest

from opencode_provider._config import ACP_BACKEND_OPENCODE


@pytest.fixture
def mock_dispatch():
    """Create a mock _dispatch module with the 0.3.0 parse_session_update shape."""
    dispatch = types.ModuleType("kiro_crew.acp._dispatch")

    def _orig_build_tool_refinement_event(update, tool_input_cache=None, shell_cache=None):
        """Mock: returns a dummy event if it's a tool_call_update."""
        if update.get("sessionUpdate") != "tool_call_update":
            return None
        if not update.get("rawInput"):
            return None
        return {"kind": "tool_call_update", "tool_call_id": update.get("toolCallId", "")}

    def _orig_parse_session_update(
        update,
        *,
        tool_input_cache=None,
        shell_cache=None,
        raw_params_cache=None,
        mcp_server_name_cache=None,
        tool_name_cache=None,
    ):
        """Mock: the ORIGINAL does NOT pass raw_params_cache to _build_tool_refinement_event."""
        events = []
        if update.get("sessionUpdate") == "tool_call_update":
            refine = _orig_build_tool_refinement_event(update, tool_input_cache, shell_cache)
            if refine:
                events.append(refine)
        return events

    dispatch._build_tool_refinement_event = _orig_build_tool_refinement_event
    dispatch.parse_session_update = _orig_parse_session_update

    # Install into sys.modules
    kiro_crew = sys.modules.get("kiro_crew") or types.ModuleType("kiro_crew")
    kiro_crew_acp = sys.modules.get("kiro_crew.acp") or types.ModuleType("kiro_crew.acp")
    saved_dispatch = sys.modules.get("kiro_crew.acp._dispatch")

    sys.modules["kiro_crew"] = kiro_crew
    sys.modules["kiro_crew.acp"] = kiro_crew_acp
    sys.modules["kiro_crew.acp._dispatch"] = dispatch

    yield dispatch

    if saved_dispatch is not None:
        sys.modules["kiro_crew.acp._dispatch"] = saved_dispatch
    else:
        sys.modules.pop("kiro_crew.acp._dispatch", None)


def test_raw_params_cache_refreshed_on_tool_call_update(mock_dispatch):
    """The core fix: raw_params_cache gets the rawInput from a tool_call_update."""
    from opencode_provider.provider import patch_dispatch_raw_params

    patch_dispatch_raw_params()

    raw_params_cache: dict = {}
    update = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tc_123",
        "rawInput": {"command": "ls -la"},
    }

    events = mock_dispatch.parse_session_update(
        update,
        tool_input_cache={},
        shell_cache={},
        raw_params_cache=raw_params_cache,
    )

    assert "tc_123" in raw_params_cache
    assert raw_params_cache["tc_123"] == {"command": "ls -la"}
    assert len(events) == 1


def test_raw_params_cache_not_refreshed_when_none(mock_dispatch):
    """When raw_params_cache is None, the patch should not crash."""
    from opencode_provider.provider import patch_dispatch_raw_params

    patch_dispatch_raw_params()

    update = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tc_456",
        "rawInput": {"command": "echo hi"},
    }

    # Should not raise
    events = mock_dispatch.parse_session_update(update)
    assert len(events) == 1


def test_raw_params_cache_not_refreshed_for_non_update(mock_dispatch):
    """Non-tool_call_update events should not touch raw_params_cache."""
    from opencode_provider.provider import patch_dispatch_raw_params

    patch_dispatch_raw_params()

    raw_params_cache: dict = {}
    update = {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "hello"},
    }

    mock_dispatch.parse_session_update(
        update,
        raw_params_cache=raw_params_cache,
    )

    assert raw_params_cache == {}


def test_raw_params_cache_not_refreshed_when_rawinput_empty(mock_dispatch):
    """A tool_call_update with no rawInput should not write to the cache."""
    from opencode_provider.provider import patch_dispatch_raw_params

    patch_dispatch_raw_params()

    raw_params_cache: dict = {}
    update = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tc_789",
        "rawInput": None,
    }

    mock_dispatch.parse_session_update(
        update,
        raw_params_cache=raw_params_cache,
    )

    assert raw_params_cache == {}


def test_raw_params_cache_not_refreshed_when_rawinput_not_dict(mock_dispatch):
    """rawInput as a string should not be cached (only dicts are cached)."""
    from opencode_provider.provider import patch_dispatch_raw_params

    patch_dispatch_raw_params()

    raw_params_cache: dict = {}
    update = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tc_str",
        "rawInput": "some string",
    }

    mock_dispatch.parse_session_update(
        update,
        raw_params_cache=raw_params_cache,
    )

    assert raw_params_cache == {}