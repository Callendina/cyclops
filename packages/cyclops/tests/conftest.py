"""Shared pytest fixtures.

The cyclops library caches process-level config on first read. Tests must
reset the cache (and clear the relevant env vars) before and after each test
so they don't leak state into one another.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from cyclops import _config, context

_CYCLOPS_ENV_VARS: tuple[str, ...] = (
    "APP_NAME",
    "ENVIRONMENT",
    "APP_VERSION",
    "CYCLOPS_COMPONENT",
)


@pytest.fixture(autouse=True)
def _isolate_cyclops_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in _CYCLOPS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    _config._reset_for_tests()
    context._reset_for_tests()
    yield
    _config._reset_for_tests()
    context._reset_for_tests()


@pytest.fixture
def configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the required env vars to test-friendly values."""
    monkeypatch.setenv("APP_NAME", "test-app")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("APP_VERSION", "0.0.0-test")
    monkeypatch.setenv("CYCLOPS_COMPONENT", "test-app.unit")
