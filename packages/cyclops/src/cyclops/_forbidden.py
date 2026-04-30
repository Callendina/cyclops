"""Forbidden field-name enforcement.

The forbidden list catches accidental leakage of secrets, PANs, OTPs, and
similar sensitive values *by their field name*. Any attempt to emit an event
that contains a field named anything on this list — at any depth in nested
dicts/lists, comparison case-insensitive — raises
:class:`~cyclops.exceptions.CyclopsForbiddenFieldError` *before* the event is
written.

The check is on names, not values. A token shaped like a JWT in a field
called ``notes`` slips through. (Pattern-based value scrubbing is intentionally
not in v1; see DESIGN.md §3.) Apps should pair this enforcement with the
redaction helpers in :mod:`cyclops.redact`.

Apps can extend the list via :func:`cyclops.init` (``extra_forbidden_fields=...``).
The base list cannot be shrunk; once forbidden, always forbidden.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import suppress
from typing import Any

from cyclops.exceptions import CyclopsConfigError, CyclopsForbiddenFieldError

# Base set — DESIGN.md §3 plus payments/identity extras.
_BASE_FORBIDDEN: frozenset[str] = frozenset(
    {
        # Secrets / credentials
        "password",
        "passwd",
        "secret",
        "api_key",
        "token",
        "client_secret",
        "refresh_token",
        "access_token",
        "bearer",
        "authorization",
        # Payment card data
        "pan",
        "card_number",
        "cardholder_name",
        "cvv",
        "cvv2",
        "cvc",
        "track1",
        "track2",
        "magstripe",
        "service_code",
        "pvv",
        "cavv",
        "auth_code",
        # Authentication factors
        "otp_code",
        "otp_secret",
        "totp_secret",
        "pin",
        "pin_block",
        # Cryptographic material
        "private_key",
        "mnemonic",
        "seed",
        # Identity numbers
        "national_id",
        "ssn",
        "tax_id",
    }
)

_MAX_RECURSION_DEPTH = 10

# Active set is the base + anything added at runtime via cyclops.init().
# Replaced atomically; never mutated in place. Reads are safe without a lock.
_active_forbidden: frozenset[str] = _BASE_FORBIDDEN


def add_forbidden_fields(names: Iterable[str]) -> None:
    """Extend the forbidden set. Idempotent; call from app startup.

    Names are lowercased before storage; comparison is case-insensitive.
    """
    global _active_forbidden
    extra = {name.lower() for name in names if name}
    _active_forbidden = _active_forbidden | frozenset(extra)


def get_forbidden_fields() -> frozenset[str]:
    """Return the current active forbidden set (base ∪ runtime additions)."""
    return _active_forbidden


def _emit_truncation_warning(path: str) -> None:
    """Emit a self-warning when recursion hit the depth limit."""
    # Lazy import: cyclops._emitter pulls in this module too.
    from cyclops._emitter import _emit

    with suppress(CyclopsConfigError):
        _emit(
            "cyclops.forbidden_check_depth_truncated",
            "warning",
            {"path": path, "max_depth": _MAX_RECURSION_DEPTH},
        )


def _check_node(node: Any, *, depth: int, path: str) -> None:
    if depth > _MAX_RECURSION_DEPTH:
        _emit_truncation_warning(path)
        return

    if isinstance(node, Mapping):
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in _active_forbidden:
                child_path = f"{path}.{key}" if path else key
                raise CyclopsForbiddenFieldError(key, path=child_path)
            child_path = f"{path}.{key}" if path else str(key)
            _check_node(value, depth=depth + 1, path=child_path)
    elif isinstance(node, list | tuple) and not isinstance(node, (str, bytes)):
        for i, item in enumerate(node):
            child_path = f"{path}[{i}]"
            _check_node(item, depth=depth + 1, path=child_path)
    # Other types (str, int, bytes, …) are leaves — no recursion.


def check_forbidden(fields: Mapping[str, Any]) -> None:
    """Walk ``fields`` recursively for forbidden key names. Raises on hit."""
    for key, value in fields.items():
        if isinstance(key, str) and key.lower() in _active_forbidden:
            raise CyclopsForbiddenFieldError(key, path=key)
        _check_node(value, depth=1, path=key if isinstance(key, str) else str(key))


def _reset_for_tests() -> None:
    """Test-only: restore the active set to the base list."""
    global _active_forbidden
    _active_forbidden = _BASE_FORBIDDEN
