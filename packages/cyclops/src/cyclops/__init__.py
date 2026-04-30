"""Cyclops — structured event emission for the Callendina app fleet.

Public API:

- :func:`event` — free-form event emission
- :func:`error` — exception-aware error event
- :func:`request_received`, :func:`request_completed` — request lifecycle
- :func:`api_call` — outbound API call
- :func:`heartbeat` — periodic alive-ping
- :func:`app_started`, :func:`app_stopped` — process lifecycle
- :func:`cron_started`, :func:`cron_completed`, :func:`cron` — cron lifecycle
- :func:`init` — library-level configuration (extra forbidden field names)
- :mod:`cyclops.context` — per-request / per-task field binding
- :func:`redact_pan`, :func:`redact_email`, :func:`redact_token` — masking helpers
- :class:`~cyclops.exceptions.CyclopsError` and subclasses
- :data:`__version__`

See DESIGN.md §1–§3 for the full design.
"""

from __future__ import annotations

from collections.abc import Iterable

__version__ = "0.1.0"

from cyclops import context
from cyclops._helpers import (
    VALID_OUTCOMES,
    api_call,
    app_started,
    app_stopped,
    cron,
    cron_completed,
    cron_started,
    error,
    event,
    heartbeat,
    request_completed,
    request_received,
)
from cyclops.exceptions import (
    CyclopsConfigError,
    CyclopsError,
    CyclopsForbiddenFieldError,
    CyclopsValidationError,
)
from cyclops.redact import redact_email, redact_pan, redact_token


def init(*, extra_forbidden_fields: Iterable[str] | None = None) -> None:
    """Library-level configuration. Call once at app startup.

    Currently supports extending the forbidden field-name list. The base list
    (DESIGN.md §3) cannot be shrunk; once forbidden, always forbidden. Apps
    add fields specific to their domain (e.g. Vispay might add
    ``customer_full_pan``).

    Other knobs may land here in future minor versions; the function is
    keyword-only so additions remain non-breaking.
    """
    if extra_forbidden_fields is not None:
        from cyclops._forbidden import add_forbidden_fields

        add_forbidden_fields(extra_forbidden_fields)


__all__ = [
    "VALID_OUTCOMES",
    "CyclopsConfigError",
    "CyclopsError",
    "CyclopsForbiddenFieldError",
    "CyclopsValidationError",
    "__version__",
    "api_call",
    "app_started",
    "app_stopped",
    "context",
    "cron",
    "cron_completed",
    "cron_started",
    "error",
    "event",
    "heartbeat",
    "init",
    "redact_email",
    "redact_pan",
    "redact_token",
    "request_completed",
    "request_received",
]
