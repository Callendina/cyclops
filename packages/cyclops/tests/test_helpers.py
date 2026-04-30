"""Tests for the public helpers (event, error, request_*, api_call, heartbeat,
app_*, cron_*, cron CM).

Each helper has its own section below; the integration with context, baseline
fields, and forbidden-name enforcement is exercised by their respective tests.
Here we focus on helper-level concerns: shape of emitted event, level
defaulting, outcome handling, exception capture.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import cyclops
import pytest
from cyclops.exceptions import CyclopsValidationError


def _read_event(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    out = capsys.readouterr().out.strip()
    return json.loads(out)


def _read_events(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line]


# ===========================================================================
# event() — free-form
# ===========================================================================


def test_event_emits_with_caller_supplied_event_type(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    cyclops.event("vispay.simulation.run", simulation_id="abc", duration_ms=42)
    e = _read_event(capsys)
    assert e["event_type"] == "vispay.simulation.run"
    assert e["simulation_id"] == "abc"
    assert e["duration_ms"] == 42
    assert e["level"] == "info"


def test_event_level_override(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    cyclops.event("foo.bar", level="warning")
    assert _read_event(capsys)["level"] == "warning"


def test_event_invalid_event_type_raises(configured_env: None) -> None:
    with pytest.raises(CyclopsValidationError, match="event_type"):
        cyclops.event("Bad-Event-Type")


def test_event_forbidden_field_raises(configured_env: None) -> None:
    from cyclops.exceptions import CyclopsForbiddenFieldError

    with pytest.raises(CyclopsForbiddenFieldError):
        cyclops.event("foo.bar", password="x")


# ===========================================================================
# error()
# ===========================================================================


def test_error_inside_except_block_captures_exception(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        cyclops.error("payment.failed", payment_id="p-1")

    e = _read_event(capsys)
    assert e["event_type"] == "payment.failed"
    assert e["level"] == "error"
    assert e["outcome"] == "failure"
    assert e["error_type"] == "ValueError"
    assert e["error_message"] == "boom"
    assert "Traceback" in e["traceback"]  # type: ignore[operator]
    assert e["error_file"].endswith("test_helpers.py")  # type: ignore[union-attr]
    assert isinstance(e["error_line"], int)
    assert e["error_function"] == "test_error_inside_except_block_captures_exception"
    assert e["payment_id"] == "p-1"


def test_error_with_explicit_exception(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    try:
        raise RuntimeError("explicit")
    except RuntimeError as exc:
        captured = exc

    cyclops.error("worker.failed", exception=captured)
    e = _read_event(capsys)
    assert e["error_type"] == "RuntimeError"
    assert e["error_message"] == "explicit"


def test_error_outside_except_block_without_exception_raises(
    configured_env: None,
) -> None:
    with pytest.raises(CyclopsValidationError, match="except block"):
        cyclops.error("foo.bar")


def test_error_rejects_outcome_kwarg(configured_env: None) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        with pytest.raises(CyclopsValidationError, match="outcome is implicit"):
            cyclops.error("payment.failed", outcome="success")


def test_error_critical_level_allowed(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        cyclops.error("payment.failed", level="critical")
    assert _read_event(capsys)["level"] == "critical"


def test_error_invalid_level_rejected(configured_env: None) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        with pytest.raises(CyclopsValidationError, match="level"):
            cyclops.error("payment.failed", level="warning")


def test_error_walks_to_deepest_frame(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """error_line should attribute to where the exception was raised, not
    where it was caught."""

    def deepest() -> None:
        raise ValueError("from deepest")

    def middle() -> None:
        deepest()

    try:
        middle()
    except ValueError:
        cyclops.error("test.error")

    e = _read_event(capsys)
    assert e["error_function"] == "deepest"


# ===========================================================================
# request_received / request_completed
# ===========================================================================


def test_request_received(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    cyclops.request_received("GET", "/api/users/42", http_user_agent="curl/8.0")
    e = _read_event(capsys)
    assert e["event_type"] == "request.received"
    assert e["http_method"] == "GET"
    assert e["http_path"] == "/api/users/42"
    assert e["http_user_agent"] == "curl/8.0"
    assert e["level"] == "info"


def test_request_completed_no_outcome_defaults_info(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    cyclops.request_completed(http_status_code=200, duration_ms=12.5)
    e = _read_event(capsys)
    assert e["event_type"] == "request.completed"
    assert e["http_status_code"] == 200
    assert e["duration_ms"] == 12.5
    assert e["level"] == "info"
    assert "outcome" not in e  # no inference


@pytest.mark.parametrize(
    ("outcome", "expected_level"),
    [
        ("success", "info"),
        ("skipped", "info"),
        ("partial", "warning"),
        ("failure", "error"),
        ("timeout", "error"),
        ("aborted", "error"),
    ],
)
def test_request_completed_level_derives_from_outcome(
    configured_env: None,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
    expected_level: str,
) -> None:
    cyclops.request_completed(http_status_code=500, duration_ms=10, outcome=outcome)
    e = _read_event(capsys)
    assert e["outcome"] == outcome
    assert e["level"] == expected_level


def test_request_completed_invalid_outcome_rejected(configured_env: None) -> None:
    with pytest.raises(CyclopsValidationError, match="outcome"):
        cyclops.request_completed(
            http_status_code=200, duration_ms=10, outcome="not-a-real-outcome"
        )


def test_request_completed_explicit_level_overrides_derivation(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    cyclops.request_completed(
        http_status_code=500, duration_ms=10, outcome="failure", level="critical"
    )
    assert _read_event(capsys)["level"] == "critical"


# ===========================================================================
# api_call
# ===========================================================================


def test_api_call_minimal(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    cyclops.api_call("stripe.com/v1/charges")
    e = _read_event(capsys)
    assert e["event_type"] == "api.call"
    assert e["target"] == "stripe.com/v1/charges"
    assert e["level"] == "info"
    assert "http_status_code" not in e
    assert "duration_ms" not in e
    assert "outcome" not in e


def test_api_call_full(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    cyclops.api_call(
        "stripe.com/v1/charges",
        http_status_code=402,
        duration_ms=345.7,
        outcome="failure",
        attempt=2,
    )
    e = _read_event(capsys)
    assert e["http_status_code"] == 402
    assert e["duration_ms"] == 345.7
    assert e["outcome"] == "failure"
    assert e["attempt"] == 2
    assert e["level"] == "error"  # derived from outcome=failure


# ===========================================================================
# heartbeat
# ===========================================================================


def test_heartbeat_default_level_is_debug(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    cyclops.heartbeat()
    e = _read_event(capsys)
    assert e["event_type"] == "heartbeat"
    assert e["level"] == "debug"


def test_heartbeat_with_next_in_seconds(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    cyclops.heartbeat(next_heartbeat_in_seconds=60)
    e = _read_event(capsys)
    assert e["next_heartbeat_in_seconds"] == 60


def test_heartbeat_extra_fields_pass_through(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    cyclops.heartbeat(active_workers=4, queue_depth=12)
    e = _read_event(capsys)
    assert e["active_workers"] == 4
    assert e["queue_depth"] == 12


# ===========================================================================
# app lifecycle
# ===========================================================================


def test_app_started(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    cyclops.app_started(pid=12345, hostname="vispay-1")
    e = _read_event(capsys)
    assert e["event_type"] == "app.started"
    assert e["level"] == "info"
    assert e["pid"] == 12345


def test_app_stopped(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    cyclops.app_stopped(graceful=True)
    e = _read_event(capsys)
    assert e["event_type"] == "app.stopped"
    assert e["graceful"] is True


# ===========================================================================
# cron lifecycle (functions)
# ===========================================================================


def test_cron_started_minimal(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    cyclops.cron_started()
    e = _read_event(capsys)
    assert e["event_type"] == "cron.started"
    assert e["level"] == "info"


def test_cron_started_with_task_name(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    cyclops.cron_started(task_name="backfill_games")
    e = _read_event(capsys)
    assert e["task_name"] == "backfill_games"


def test_cron_completed_success(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    cyclops.cron_completed(outcome="success", duration_seconds=12.3)
    e = _read_event(capsys)
    assert e["event_type"] == "cron.completed"
    assert e["outcome"] == "success"
    assert e["duration_seconds"] == 12.3
    assert e["level"] == "info"


def test_cron_completed_failure_level_is_error(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    cyclops.cron_completed(outcome="failure")
    assert _read_event(capsys)["level"] == "error"


# ===========================================================================
# cron context manager
# ===========================================================================


def test_cron_cm_clean_exit_emits_started_then_completed_success(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with cyclops.cron("backfill_games"):
        pass

    events = _read_events(capsys)
    assert [e["event_type"] for e in events] == [
        "cron.started",
        "cron.completed",
    ]
    started, completed = events
    assert started["task_name"] == "backfill_games"
    assert completed["task_name"] == "backfill_games"
    assert completed["outcome"] == "success"
    assert "duration_seconds" in completed
    assert isinstance(completed["duration_seconds"], (int, float))


def test_cron_cm_exception_emits_failure_then_reraises(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(ValueError, match="cron-broke"):
        with cyclops.cron("daily_ingest"):
            raise ValueError("cron-broke")

    events = _read_events(capsys)
    assert [e["event_type"] for e in events] == [
        "cron.started",
        "cron.completed",
    ]
    completed = events[1]
    assert completed["outcome"] == "failure"
    assert completed["error_type"] == "ValueError"
    assert completed["error_message"] == "cron-broke"
    assert completed["task_name"] == "daily_ingest"
    assert completed["level"] == "error"


def test_cron_cm_without_task_name(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with cyclops.cron():
        pass
    events = _read_events(capsys)
    assert all("task_name" not in e for e in events)


def test_cron_cm_extra_fields_attach_to_both_events(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with cyclops.cron("daily_ingest", schedule_time="02:00"):
        pass
    events = _read_events(capsys)
    for e in events:
        assert e["schedule_time"] == "02:00"


def test_cron_cm_keyboard_interrupt_records_failure_and_propagates(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """KeyboardInterrupt and SystemExit must still produce a cron.completed
    event so absence-detection isn't fooled."""
    with pytest.raises(KeyboardInterrupt):
        with cyclops.cron("foo"):
            raise KeyboardInterrupt

    events = _read_events(capsys)
    assert events[-1]["outcome"] == "failure"
    assert events[-1]["error_type"] == "KeyboardInterrupt"


# ===========================================================================
# Top-level imports
# ===========================================================================


def test_helpers_importable_from_cyclops_namespace() -> None:
    """All Tier 1 helpers exposed at the top-level cyclops module."""
    expected = {
        "event",
        "error",
        "request_received",
        "request_completed",
        "api_call",
        "heartbeat",
        "app_started",
        "app_stopped",
        "cron_started",
        "cron_completed",
        "cron",
    }
    for name in expected:
        assert hasattr(cyclops, name), f"cyclops.{name} should be importable"


def test_valid_outcomes_exposed() -> None:
    assert "success" in cyclops.VALID_OUTCOMES
    assert "failure" in cyclops.VALID_OUTCOMES
    assert "partial" in cyclops.VALID_OUTCOMES
    assert "skipped" in cyclops.VALID_OUTCOMES
    assert "aborted" in cyclops.VALID_OUTCOMES
    assert "timeout" in cyclops.VALID_OUTCOMES


# ===========================================================================
# Integration with context
# ===========================================================================


def test_helper_event_includes_context_fields(
    configured_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """A helper called inside a bind() picks up context fields automatically."""
    with cyclops.context.bind(user_id="alice@example.com", workflow_id="w-1"):
        cyclops.event("vispay.simulation.run", simulation_id="abc")

    e = _read_event(capsys)
    assert e["user_id"] == "alice@example.com"
    assert e["workflow_id"] == "w-1"
    assert e["simulation_id"] == "abc"


# ===========================================================================
# Iterator import sanity (cron is a contextmanager; ensure its return type
# behaves as a generator-based CM and supports nesting)
# ===========================================================================


def test_cron_cm_supports_nesting(configured_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    with cyclops.cron("outer"):
        with cyclops.cron("inner"):
            pass

    events = _read_events(capsys)
    assert [e["event_type"] for e in events] == [
        "cron.started",  # outer
        "cron.started",  # inner
        "cron.completed",  # inner
        "cron.completed",  # outer
    ]
    assert events[0]["task_name"] == "outer"
    assert events[1]["task_name"] == "inner"
    assert events[2]["task_name"] == "inner"
    assert events[3]["task_name"] == "outer"


# Sanity: Iterator import unused warning suppression.
_ = Iterator
