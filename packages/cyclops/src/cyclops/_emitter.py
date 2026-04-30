"""The single write-one-line-of-JSON-to-stdout path.

Every cyclops event flows through :func:`_emit`. It composes baseline fields
(timestamp, level, event_type, app/env/host/component, cyclops_version) with
caller-supplied fields and writes a single JSON line to stdout.

Synchronous, no buffering, no background threads — this matters: the library
must not have its own bugs interfere with app behaviour. ``flush=True`` on
print() ensures each event is visible to a downstream agent (Docker stdout,
file tail) immediately.

Caller-supplied keys may not collide with baseline field names; that's a hard
error at emission. Forbidden-field enforcement and redaction land in #12.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from cyclops import __version__
from cyclops._config import _load_config
from cyclops._validation import _validate_event_type, _validate_level
from cyclops.exceptions import CyclopsValidationError

_BASELINE_KEYS: frozenset[str] = frozenset(
    {
        "timestamp",
        "level",
        "event_type",
        "app",
        "env",
        "host",
        "component",
        "cyclops_version",
    }
)


def _now_iso_utc() -> str:
    """ISO 8601 UTC timestamp with microsecond precision and ``Z`` suffix."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _emit(event_type: str, level: str, fields: Mapping[str, Any]) -> None:
    """Validate, compose, and write a single event as one JSON line to stdout.

    Internal API. The public :func:`cyclops.event` and typed helpers (#13) call
    through here.
    """
    _validate_event_type(event_type)
    _validate_level(level)

    overlap = set(fields.keys()) & _BASELINE_KEYS
    if overlap:
        raise CyclopsValidationError(
            "Caller fields cannot use baseline key names: "
            f"{sorted(overlap)}. These are populated by the library."
        )

    cfg = _load_config()

    event: dict[str, Any] = {
        "timestamp": _now_iso_utc(),
        "level": level,
        "event_type": event_type,
        "app": cfg.app,
        "env": cfg.env,
        "host": cfg.host,
        "component": cfg.component,
        "cyclops_version": __version__,
    }
    # Caller fields appended after baseline so the most-significant fields lead
    # each line for human readability when tailing.
    event.update(fields)

    line = json.dumps(
        event,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    print(line, file=sys.stdout, flush=True)
