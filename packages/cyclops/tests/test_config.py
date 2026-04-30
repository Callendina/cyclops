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
    assert cfg.app_version == "0.0.0-test"
    assert cfg.component == "test-app.unit"
    assert cfg.host == socket.gethostname()


@pytest.mark.parametrize(
    "missing_var",
    ["APP_NAME", "ENVIRONMENT", "APP_VERSION", "CYCLOPS_COMPONENT"],
)
def test_missing_any_required_var_raises(monkeypatch: pytest.MonkeyPatch, missing_var: str) -> None:
    all_vars = {
        "APP_NAME": "test-app",
        "ENVIRONMENT": "test",
        "APP_VERSION": "0.0.0-test",
        "CYCLOPS_COMPONENT": "test-app.unit",
    }
    del all_vars[missing_var]
    for k, v in all_vars.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(CyclopsConfigError, match=missing_var):
        _config._load_config()


def test_all_missing_listed_in_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CyclopsConfigError) as exc_info:
        _config._load_config()
    msg = str(exc_info.value)
    assert "APP_NAME" in msg
    assert "ENVIRONMENT" in msg
    assert "APP_VERSION" in msg
    assert "CYCLOPS_COMPONENT" in msg


def test_empty_string_treated_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("APP_VERSION", "0.0.0-test")
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
