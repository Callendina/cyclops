"""Tests for cyclops._emitter — JSON-line emission and baseline composition."""

from __future__ import annotations

import json
import re
import socket

import cyclops
import pytest
from cyclops._emitter import _emit, _now_iso_utc
from cyclops.exceptions import CyclopsValidationError


def _capture_event(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    out = capsys.readouterr().out
    assert out.endswith("\n"), "emitter must terminate the line"
    payload, _, trailing = out.rstrip("\n").partition("\n")
    assert trailing == "", "emitter must produce a single line"
    return json.loads(payload)


def test_emits_baseline_fields(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    _emit("request.received", "info", {})
    event = _capture_event(capsys)

    assert event["app"] == "test-app"
    assert event["env"] == "test"
    assert event["host"] == socket.gethostname()
    assert event["component"] == "test-app.unit"
    assert event["cyclops_version"] == cyclops.__version__
    assert event["event_type"] == "request.received"
    assert event["level"] == "info"
    assert "timestamp" in event


def test_timestamp_is_iso_utc_with_microseconds(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _emit("foo.bar", "info", {})
    event = _capture_event(capsys)
    ts = event["timestamp"]
    assert isinstance(ts, str)
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$", ts), ts


def test_now_iso_utc_format() -> None:
    ts = _now_iso_utc()
    assert ts.endswith("Z")
    assert "+00:00" not in ts
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$", ts)


def test_caller_fields_appear(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    _emit(
        "vispay.simulation.run",
        "info",
        {"simulation_id": "abc-123", "duration_ms": 42},
    )
    event = _capture_event(capsys)
    assert event["simulation_id"] == "abc-123"
    assert event["duration_ms"] == 42


def test_baseline_keys_lead_caller_keys(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """For human readability tailing logs — baseline fields appear first."""
    _emit("foo.bar", "info", {"caller_field": "x"})
    event = _capture_event(capsys)
    keys = list(event.keys())
    assert keys[0] == "timestamp"
    assert keys[1] == "level"
    assert keys[2] == "event_type"
    assert "caller_field" in keys[8:]


def test_caller_cannot_override_baseline_keys(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(CyclopsValidationError, match="baseline key"):
        _emit("foo.bar", "info", {"app": "imposter"})

    # Nothing should have been written to stdout.
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "bad_key",
    ["timestamp", "level", "event_type", "host", "env", "component", "cyclops_version"],
)
def test_each_baseline_key_is_protected(
    configured_env: None,
    capsys: pytest.CaptureFixture[str],
    bad_key: str,
) -> None:
    with pytest.raises(CyclopsValidationError, match=bad_key):
        _emit("foo.bar", "info", {bad_key: "imposter"})


def test_invalid_event_type_raises_before_emission(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(CyclopsValidationError, match="event_type"):
        _emit("Bad-Event-Type", "info", {})
    assert capsys.readouterr().out == ""


def test_invalid_level_raises_before_emission(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(CyclopsValidationError, match="level"):
        _emit("foo.bar", "INFO", {})
    assert capsys.readouterr().out == ""


def test_unicode_preserved(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    _emit("foo.bar", "info", {"note": "héllo wörld 🌍"})
    event = _capture_event(capsys)
    assert event["note"] == "héllo wörld 🌍"


def test_non_serialisable_falls_back_to_str(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Best-effort emit — exotic types shouldn't crash a logging call."""

    class Custom:
        def __str__(self) -> str:
            return "custom-repr"

    _emit("foo.bar", "info", {"obj": Custom()})
    event = _capture_event(capsys)
    assert event["obj"] == "custom-repr"


def test_each_emit_is_one_line(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    _emit("a.b", "info", {})
    _emit("c.d", "warning", {"k": 1})
    _emit("e.f", "error", {})

    out = capsys.readouterr().out
    lines = [line for line in out.split("\n") if line]
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["event_type"] == "a.b"
    assert parsed[1]["event_type"] == "c.d"
    assert parsed[2]["event_type"] == "e.f"


def test_compact_json_format(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    """No whitespace between separators — keeps lines tight for log shipping."""
    _emit("foo.bar", "info", {"k": "v"})
    out = capsys.readouterr().out.rstrip("\n")
    assert ", " not in out
    assert ": " not in out


def test_missing_config_raises_on_emit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When required env vars are absent, emission fails fast."""
    from cyclops.exceptions import CyclopsConfigError

    with pytest.raises(CyclopsConfigError):
        _emit("foo.bar", "info", {})
    assert capsys.readouterr().out == ""
