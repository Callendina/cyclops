"""The single write-one-line-of-JSON-to-stdout path.

Every cyclops event flows through :func:`_emit`. It composes baseline fields
(timestamp, level, event_type, app/env/host/component, cyclops_version) with
context-derived fields (request_id, user_id, workflow_id, …) and
caller-supplied fields, then writes a single JSON line to stdout.

Synchronous, no buffering, no background threads — this matters: the library
must not have its own bugs interfere with app behaviour. ``flush=True`` on
print() ensures each event is visible to a downstream agent (Docker stdout,
file tail) immediately.

Caller-supplied keys may not collide with baseline or context-derived field
names; that's a hard error at emission. Forbidden-field enforcement and
redaction land in #12.
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
from cyclops.context import _CONTEXT_KEYS, snapshot
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

# Names the library owns. Caller fields may not use any of these.
_RESERVED_KEYS: frozenset[str] = _BASELINE_KEYS | _CONTEXT_KEYS


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

    overlap = set(fields.keys()) & _RESERVED_KEYS
    if overlap:
        raise CyclopsValidationError(
            "Caller fields cannot use library-reserved key names: "
            f"{sorted(overlap)}. These are populated by the library "
            "(baseline) or via cyclops.context (context-derived)."
        )

    cfg = _load_config()
    ctx = snapshot()

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
    # Context-derived fields after baseline, in a stable order, only when set.
    for key in sorted(_CONTEXT_KEYS):
        if key in ctx:
            event[key] = ctx[key]
    # Caller fields last so callers can scan the line top-to-bottom for the
    # things that matter to *their* dashboard.
    event.update(fields)

    line = json.dumps(
        event,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    print(line, file=sys.stdout, flush=True)
