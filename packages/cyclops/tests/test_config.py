"""Tests for cyclops._config — env var loading and caching."""

from __future__ import annotations

import socket

import pytest
from cyclops import _config
from cyclops.exceptions import CyclopsConfigError


def test_loads_required_env_vars(configured_env: None) -> None:
    cfg = _config._load_config()
    assert cfg.app == "test-app"
    assert cfg.env == "test"
    assert cfg.component == "test-app.unit"
    assert cfg.host == socket.gethostname()


def test_missing_app_name_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CYCLOPS_COMPONENT", "test-app.unit")
    with pytest.raises(CyclopsConfigError, match="APP_NAME"):
        _config._load_config()


def test_missing_environment_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "test-app")
    monkeypatch.setenv("CYCLOPS_COMPONENT", "test-app.unit")
    with pytest.raises(CyclopsConfigError, match="ENVIRONMENT"):
        _config._load_config()


def test_missing_component_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "test-app")
    monkeypatch.setenv("ENVIRONMENT", "test")
    with pytest.raises(CyclopsConfigError, match="CYCLOPS_COMPONENT"):
        _config._load_config()


def test_all_missing_listed_in_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CyclopsConfigError) as exc_info:
        _config._load_config()
    msg = str(exc_info.value)
    assert "APP_NAME" in msg
    assert "ENVIRONMENT" in msg
    assert "CYCLOPS_COMPONENT" in msg


def test_empty_string_treated_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CYCLOPS_COMPONENT", "test-app.unit")
    with pytest.raises(CyclopsConfigError, match="APP_NAME"):
        _config._load_config()


def test_config_is_cached(monkeypatch: pytest.MonkeyPatch, configured_env: None) -> None:
    """A second call returns the same instance — env changes don't re-read."""
    first = _config._load_config()
    monkeypatch.setenv("APP_NAME", "different")
    second = _config._load_config()
    assert first is second
    assert second.app == "test-app"


def test_reset_clears_cache(monkeypatch: pytest.MonkeyPatch, configured_env: None) -> None:
    first = _config._load_config()
    monkeypatch.setenv("APP_NAME", "different")
    _config._reset_for_tests()
    second = _config._load_config()
    assert first is not second
    assert second.app == "different"
