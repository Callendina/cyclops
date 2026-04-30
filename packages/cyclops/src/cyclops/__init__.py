"""Cyclops — structured event emission for the Callendina app fleet.

Public API (so far):

- :data:`__version__` — package version
- :func:`init` — library-level configuration (extra forbidden field names)
- :mod:`cyclops.context` — per-request / per-task field binding
- :func:`redact_pan`, :func:`redact_email`, :func:`redact_token` — masking helpers
- :class:`~cyclops.exceptions.CyclopsError` and subclasses

The free-form ``event()`` and typed helpers (``cyclops.error``, lifecycle
helpers) land in #13. See DESIGN.md §1–§3.
"""

from __future__ import annotations

from collections.abc import Iterable

__version__ = "0.1.0"

from cyclops import context
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
    "CyclopsConfigError",
    "CyclopsError",
    "CyclopsForbiddenFieldError",
    "CyclopsValidationError",
    "__version__",
    "context",
    "init",
    "redact_email",
    "redact_pan",
    "redact_token",
]
