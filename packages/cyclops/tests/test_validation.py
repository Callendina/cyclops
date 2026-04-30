"""Tests for cyclops._validation — event_type format and level enum."""

from __future__ import annotations

import pytest
from cyclops._validation import _validate_event_type, _validate_level
from cyclops.exceptions import CyclopsValidationError


@pytest.mark.parametrize(
    "event_type",
    [
        "heartbeat",
        "request.received",
        "request.completed",
        "vispay.simulation.run",
        "scout.report.generated",
        "a.b.c.d.e.f",
        "snake_case.with_underscore",
        "a1.b2.c3",
        "a_b_c",
    ],
)
def test_valid_event_types(event_type: str) -> None:
    _validate_event_type(event_type)


@pytest.mark.parametrize(
    "event_type",
    [
        "",
        ".",
        "foo.",
        ".foo",
        "foo..bar",
        "Foo",
        "foo.Bar",
        "foo-bar",
        "foo bar",
        "1foo",
        "foo.1bar",
        "foo/bar",
        "_foo",
        "foo.",
        "FOO",
    ],
)
def test_invalid_event_types(event_type: str) -> None:
    with pytest.raises(CyclopsValidationError, match="event_type"):
        _validate_event_type(event_type)


def test_event_type_must_be_string() -> None:
    with pytest.raises(CyclopsValidationError):
        _validate_event_type(123)  # type: ignore[arg-type]


@pytest.mark.parametrize("level", ["debug", "info", "warning", "error", "critical"])
def test_valid_levels(level: str) -> None:
    _validate_level(level)


@pytest.mark.parametrize(
    "level",
    [
        "",
        "INFO",
        "Info",
        "warn",
        "trace",
        "fatal",
        "verbose",
        "notice",
    ],
)
def test_invalid_levels(level: str) -> None:
    with pytest.raises(CyclopsValidationError, match="level"):
        _validate_level(level)
