"""Validators for ``event_type`` format and ``level`` enum.

``event_type`` is one or more dot-separated segments, each lowercase
``[a-z][a-z0-9_]*``. Examples: ``"request.completed"``,
``"vispay.simulation.run"``, ``"heartbeat"``.

``level`` is one of ``debug | info | warning | error | critical`` (lowercase).
"""

from __future__ import annotations

import re

from cyclops.exceptions import CyclopsValidationError

_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")

VALID_LEVELS: frozenset[str] = frozenset({"debug", "info", "warning", "error", "critical"})


def _validate_event_type(event_type: str) -> None:
    if not isinstance(event_type, str) or not _EVENT_TYPE_RE.match(event_type):
        raise CyclopsValidationError(
            f"Invalid event_type {event_type!r}: expected one or more "
            "snake_case segments separated by '.', e.g. 'request.completed'."
        )


def _validate_level(level: str) -> None:
    if level not in VALID_LEVELS:
        raise CyclopsValidationError(
            f"Invalid level {level!r}: expected one of {sorted(VALID_LEVELS)}."
        )
