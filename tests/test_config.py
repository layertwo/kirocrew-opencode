"""Tests for opencode_provider._config."""

import os

from opencode_provider._config import (
    ACP_BACKEND_OPENCODE,
    PROTOCOL_VERSION_OPENCODE,
    OPENCODE_BIN_DEFAULT,
    OPENCODE_SUBCMD,
    is_opencode_selected,
    resolve_opencode_bin,
)


def test_constants():
    assert ACP_BACKEND_OPENCODE == "opencode"
    assert PROTOCOL_VERSION_OPENCODE == 1
    assert OPENCODE_BIN_DEFAULT == "opencode"
    assert OPENCODE_SUBCMD == "acp"


def test_is_opencode_selected_true(monkeypatch):
    monkeypatch.setenv("KIROCREW_ACP_BACKEND", "opencode")
    assert is_opencode_selected() is True


def test_is_opencode_selected_case_insensitive(monkeypatch):
    monkeypatch.setenv("KIROCREW_ACP_BACKEND", "OpenCode")
    assert is_opencode_selected() is True


def test_is_opencode_selected_false_empty(monkeypatch):
    monkeypatch.delenv("KIROCREW_ACP_BACKEND", raising=False)
    assert is_opencode_selected() is False


def test_is_opencode_selected_false_other(monkeypatch):
    monkeypatch.setenv("KIROCREW_ACP_BACKEND", "kiro")
    assert is_opencode_selected() is False


def test_resolve_opencode_bin_override(monkeypatch):
    monkeypatch.setenv("OPENCODE_BIN", "/custom/path/opencode")
    assert resolve_opencode_bin() == "/custom/path/opencode"


def test_resolve_opencode_bin_path_lookup(monkeypatch):
    monkeypatch.delenv("OPENCODE_BIN", raising=False)
    # Should return a string (the binary) or None — depends on the environment
    result = resolve_opencode_bin()
    assert result is None or isinstance(result, str)