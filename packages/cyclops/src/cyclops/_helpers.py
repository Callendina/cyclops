"""Public helper functions: ``event`` and the Tier 1 typed helpers.

All helpers funnel through :func:`cyclops._emitter._emit`, which composes
baseline + context-derived + caller fields into one JSON line on stdout.
The helpers exist for two reasons (DESIGN.md §1):

1. **Canonical shape.** Cross-fleet patterns (request lifecycle, errors,
   cron, API calls) deserve consistent field names so dashboards work
   uniformly across apps.
2. **Smart defaults.** ``level`` derives from ``outcome`` when one is
   passed; helpers fill auto-fields like exception traceback or duration.
   Outcome itself is *never* library-inferred — apps decide what success
   means (DESIGN.md §1, "outcome and status codes are at different layers").

The canonical event_types emitted by helpers are unprefixed (DESIGN.md
schema decision) so dashboards can filter without app-specific prefixes:

============================  =====================================
Helper                        event_type
============================  =====================================
:func:`event`                 caller-supplied
:func:`error`                 caller-supplied (defaults to ``error``)
:func:`request_received`      ``request.received``
:func:`request_completed`     ``request.completed``
:func:`api_call`              ``api.call``
:func:`heartbeat`             ``heartbeat``
:func:`app_started`           ``app.started``
:func:`app_stopped`           ``app.stopped``
:func:`cron_started`          ``cron.started``
:func:`cron_completed`        ``cron.completed``
============================  =====================================
"""

from __future__ import annotations

import sys
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from cyclops._emitter import _emit
from cyclops.exceptions import CyclopsValidationError

VALID_OUTCOMES: frozenset[str] = frozenset(
    {"success", "failure", "partial", "skipped", "aborted", "timeout"}
)


def _validate_outcome(outcome: str | None) -> None:
    if outcome is not None and outcome not in VALID_OUTCOMES:
        raise CyclopsValidationError(
            f"Invalid outcome {outcome!r}: expected one of {sorted(VALID_OUTCOMES)}."
        )


def _level_from_outcome(outcome: str | None) -> str:
    """Derive ``level`` when not explicitly specified.

    Per DESIGN.md §1: success/skipped → info, partial → warning,
    failure/timeout/aborted → error, no outcome → info.
    """
    if outcome is None or outcome in {"success", "skipped"}:
        return "info"
    if outcome == "partial":
        return "warning"
    return "error"


# ---------------------------------------------------------------------------
# Free-form
# ---------------------------------------------------------------------------


def event(event_type: str, *, level: str = "info", **fields: Any) -> None:
    """Emit a free-form event.

    The escape hatch when no typed helper fits. ``event_type`` is required and
    must be a dot-separated snake_case string (validated at emission). All
    keyword arguments become event fields.
    """
    _emit(event_type, level, fields)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def error(
    event_type: str = "error",
    *,
    exception: BaseException | None = None,
    level: str = "error",
    **fields: Any,
) -> None:
    """Emit an error event with auto-captured exception details.

    Called from inside an ``except`` block, the helper grabs the live
    exception via :func:`sys.exc_info` automatically. Otherwise pass
    ``exception=...`` explicitly. Without either, raises
    :class:`~cyclops.exceptions.CyclopsValidationError` — the helper isn't
    useful without an exception to describe.

    Fields populated automatically (caller fields take precedence on collision):
    ``error_type``, ``error_module``, ``error_message``, ``traceback``,
    and top-frame attribution: ``error_file``, ``error_line``, ``error_function``.

    ``outcome="failure"`` is always set; passing ``outcome=`` is rejected. By
    helper identity rather than inference — calling ``error()`` *is* the
    declaration that something failed.
    """
    if "outcome" in fields:
        raise CyclopsValidationError(
            "outcome is implicit on error() (always 'failure'); do not pass it."
        )
    if level not in {"error", "critical"}:
        raise CyclopsValidationError(f"error() level must be 'error' or 'critical'; got {level!r}.")

    exc = exception
    if exc is None:
        _, exc_value, _ = sys.exc_info()
        if exc_value is None:
            raise CyclopsValidationError(
                "error() called outside an except block and without "
                "exception=; nothing to describe."
            )
        exc = exc_value

    error_fields: dict[str, Any] = {
        "outcome": "failure",
        "error_type": exc.__class__.__name__,
        "error_module": exc.__class__.__module__,
        "error_message": str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }

    tb = exc.__traceback__
    if tb is not None:
        # Walk to the deepest frame — where the exception actually originated.
        while tb.tb_next:
            tb = tb.tb_next
        error_fields["error_file"] = tb.tb_frame.f_code.co_filename
        error_fields["error_line"] = tb.tb_lineno
        error_fields["error_function"] = tb.tb_frame.f_code.co_name

    # Caller fields override auto-populated ones — the app may want to add
    # context (e.g. request_id is auto from context, but error_message could
    # be replaced with a sanitised version).
    error_fields.update(fields)
    _emit(event_type, level, error_fields)


# ---------------------------------------------------------------------------
# Request lifecycle
# ---------------------------------------------------------------------------


def request_received(
    http_method: str,
    http_path: str,
    *,
    level: str = "info",
    **fields: Any,
) -> None:
    """Emit ``request.received`` — typically called from the Flask middleware
    (#4) but available for direct use.
    """
    _emit(
        "request.received",
        level,
        {"http_method": http_method, "http_path": http_path, **fields},
    )


def request_completed(
    http_status_code: int,
    duration_ms: float,
    *,
    outcome: str | None = None,
    level: str | None = None,
    **fields: Any,
) -> None:
    """Emit ``request.completed`` with HTTP-layer facts.

    The middleware emits this without ``outcome`` — only the app's view
    function knows the business-layer judgement (DESIGN.md §1, "HTTP layer
    vs business layer"). Apps that emit it directly may pass an outcome.
    """
    _validate_outcome(outcome)
    if level is None:
        level = _level_from_outcome(outcome)
    payload: dict[str, Any] = {
        "http_status_code": http_status_code,
        "duration_ms": duration_ms,
        **fields,
    }
    if outcome is not None:
        payload["outcome"] = outcome
    _emit("request.completed", level, payload)


# ---------------------------------------------------------------------------
# Outbound API calls
# ---------------------------------------------------------------------------


def api_call(
    target: str,
    *,
    http_status_code: int | None = None,
    duration_ms: float | None = None,
    outcome: str | None = None,
    level: str | None = None,
    **fields: Any,
) -> None:
    """Emit ``api.call`` for an outbound HTTP/RPC operation.

    ``target`` is the API being called (e.g. ``"stripe.com/v1/charges"``).
    Status code, duration, and outcome are all optional — pass what you know.
    """
    _validate_outcome(outcome)
    if level is None:
        level = _level_from_outcome(outcome)
    payload: dict[str, Any] = {"target": target, **fields}
    if http_status_code is not None:
        payload["http_status_code"] = http_status_code
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if outcome is not None:
        payload["outcome"] = outcome
    _emit("api.call", level, payload)


# ---------------------------------------------------------------------------
# Heartbeats
# ---------------------------------------------------------------------------


def heartbeat(
    *,
    next_heartbeat_in_seconds: float | None = None,
    level: str = "debug",
    **fields: Any,
) -> None:
    """Emit ``heartbeat`` — long-running processes ping periodically so
    absence-detection queries can identify silent failures (DESIGN.md §5).

    Defaults to ``debug`` level since heartbeats are noisy by design;
    dashboards filtering at info+ will exclude them.
    """
    payload: dict[str, Any] = dict(fields)
    if next_heartbeat_in_seconds is not None:
        payload["next_heartbeat_in_seconds"] = next_heartbeat_in_seconds
    _emit("heartbeat", level, payload)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


def app_started(*, level: str = "info", **fields: Any) -> None:
    """Emit ``app.started`` — call once at app startup."""
    _emit("app.started", level, fields)


def app_stopped(*, level: str = "info", **fields: Any) -> None:
    """Emit ``app.stopped`` — call once at app shutdown (where possible)."""
    _emit("app.stopped", level, fields)


# ---------------------------------------------------------------------------
# Cron lifecycle
# ---------------------------------------------------------------------------


def cron_started(
    *,
    task_name: str | None = None,
    level: str = "info",
    **fields: Any,
) -> None:
    """Emit ``cron.started`` at the top of a cron-fired script.

    Prefer the :func:`cron` context manager when you can — it handles
    ``cron.completed`` and exception-handling for you.
    """
    payload = dict(fields)
    if task_name is not None:
        payload["task_name"] = task_name
    _emit("cron.started", level, payload)


def cron_completed(
    *,
    outcome: str | None = None,
    duration_seconds: float | None = None,
    task_name: str | None = None,
    level: str | None = None,
    **fields: Any,
) -> None:
    """Emit ``cron.completed`` at the end of a cron-fired script.

    Prefer the :func:`cron` context manager when you can.
    """
    _validate_outcome(outcome)
    if level is None:
        level = _level_from_outcome(outcome)
    payload = dict(fields)
    if outcome is not None:
        payload["outcome"] = outcome
    if duration_seconds is not None:
        payload["duration_seconds"] = duration_seconds
    if task_name is not None:
        payload["task_name"] = task_name
    _emit("cron.completed", level, payload)


@contextmanager
def cron(task_name: str | None = None, **fields: Any) -> Iterator[None]:
    """Context manager wrapping a cron-script body.

    Emits ``cron.started`` on enter and ``cron.completed`` on exit. On a
    clean exit, ``outcome="success"``. On any exception, ``outcome="failure"``
    plus ``error_type`` / ``error_message`` are included before the
    exception is re-raised. Duration is captured automatically.

    Typical use::

        if __name__ == "__main__":
            with cyclops.cron("backfill_games"):
                run_backfill()
    """
    extras = dict(fields)
    if task_name is not None:
        extras["task_name"] = task_name

    cron_started(**extras)
    start = time.monotonic()
    try:
        yield
    except BaseException as exc:
        # BaseException so KeyboardInterrupt / SystemExit are still recorded.
        cron_completed(
            outcome="failure",
            duration_seconds=time.monotonic() - start,
            error_type=exc.__class__.__name__,
            error_message=str(exc),
            **extras,
        )
        raise
    else:
        cron_completed(
            outcome="success",
            duration_seconds=time.monotonic() - start,
            **extras,
        )
