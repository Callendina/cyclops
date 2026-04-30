"""Per-emission context backed by :class:`contextvars.ContextVar`.

Cyclops separates *baseline* fields (set once per process from env) from
*context-derived* fields (per-request or per-task). Context-derived fields
flow through this module — apps and the Flask middleware bind values for the
lifetime of a request, and the emitter automatically includes them on every
event emitted within that scope.

Public API:

- :func:`bind` — context manager that adds fields for the ``with`` block.
- :func:`get` — read a single field (returns ``None`` if unset).
- :func:`snapshot` — read every currently-set field plus process defaults.

Allowlisted keys (DESIGN.md §2):

    request_id, session_id, user_id, user_role, user_group,
    is_system_admin, workflow_id, app_version

Setting an unknown key raises :class:`~cyclops.exceptions.CyclopsValidationError`.

Semantics:

- *Mutable by addition, immutable by overwrite.* If ``bind`` (or :func:`set`)
  is called with a key that already has a *different* value in the current
  context, the original value is kept and a ``cyclops.context_overwrite_attempted``
  warning event is emitted. Same-value rebinds are silent.
- *Auto-generated* ``request_id``: when ``bind`` is invoked at a scope where no
  ``request_id`` is set anywhere up the context stack and the caller hasn't
  supplied one, a UUID4 is generated and bound for that scope. This keeps
  cron-script invocations correlatable without ceremony.
- *Per-thread / per-task isolation*: contextvars handles this natively.
  ``threading.Thread`` does NOT inherit context unless you use
  ``contextvars.copy_context().run(...)`` — that's a Python footgun, not a
  cyclops concern.

Threading note: contextvars are copy-on-set-of-child-task. ``asyncio.create_task``
inherits the current context at creation time; subsequent changes in the parent
or child don't bleed across.
"""

from __future__ import annotations

import traceback
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any

from cyclops._config import _load_config
from cyclops.exceptions import CyclopsConfigError, CyclopsValidationError

_CONTEXT_KEYS: frozenset[str] = frozenset(
    {
        "request_id",
        "session_id",
        "user_id",
        "user_role",
        "user_group",
        "is_system_admin",
        "workflow_id",
        "app_version",
    }
)

_EMPTY: Mapping[str, Any] = MappingProxyType({})
_ctx: ContextVar[Mapping[str, Any]] = ContextVar("cyclops_context", default=_EMPTY)


def _validate_keys(keys: Iterable[str]) -> None:
    unknown = [k for k in keys if k not in _CONTEXT_KEYS]
    if unknown:
        raise CyclopsValidationError(
            f"Unknown context key(s): {sorted(unknown)}. Allowed: {sorted(_CONTEXT_KEYS)}."
        )


def _process_defaults() -> dict[str, Any]:
    """Process-level defaults that apply when not explicitly overridden in context."""
    cfg = _load_config()
    return {"app_version": cfg.app_version}


def _emit_overwrite_warning(key: str, depth: int) -> None:
    """Emit a self-warning event when context key overwrite is attempted.

    ``depth`` is the number of stack frames between this helper and the
    user-visible call site (so we can attribute the warning correctly).
    """
    # Lazy import: cyclops._emitter would otherwise pull in this module
    # during initial loading of cyclops.
    from cyclops._emitter import _emit

    stack = traceback.extract_stack()
    # Grab the frame depth+1 levels above this function.
    idx = max(0, len(stack) - 1 - depth)
    frame = stack[idx]
    source = f"{frame.filename}:{frame.lineno}"

    # Config not loaded yet → observability about observability is best-effort.
    with suppress(CyclopsConfigError):
        _emit(
            "cyclops.context_overwrite_attempted",
            "warning",
            {"conflicting_key": key, "source": source},
        )


def _apply(
    current: Mapping[str, Any],
    additions: Mapping[str, Any],
    *,
    warn_depth: int,
) -> dict[str, Any]:
    """Merge ``additions`` into ``current`` honouring the overwrite rule."""
    new = dict(current)
    for k, v in additions.items():
        if k in new and new[k] != v:
            _emit_overwrite_warning(k, depth=warn_depth)
            # Keep the original value; do not overwrite.
            continue
        new[k] = v
    return new


@contextmanager
def bind(**kwargs: Any) -> Iterator[None]:
    """Add the given fields to the current context for the ``with`` block.

    On enter the additions are merged in; on exit the previous state is
    restored. If no ``request_id`` is set anywhere up the context stack and
    none is supplied, a UUID4 is generated for this scope so cron-style
    invocations remain correlatable.
    """
    _validate_keys(kwargs.keys())

    current = _ctx.get()
    additions = dict(kwargs)

    if "request_id" not in additions and "request_id" not in current:
        additions["request_id"] = str(uuid.uuid4())

    new = _apply(current, additions, warn_depth=4)
    token = _ctx.set(new)
    try:
        yield
    finally:
        _ctx.reset(token)


def get(key: str) -> Any | None:
    """Return the current value of ``key``, or ``None`` if unset.

    ``app_version`` always has a value (the process default from APP_VERSION).
    Other allowlisted keys return ``None`` when not bound.
    """
    _validate_keys([key])
    return snapshot().get(key)


def snapshot() -> dict[str, Any]:
    """Return all currently-set context fields, including process defaults."""
    return {**_process_defaults(), **_ctx.get()}


def set(key: str, value: Any) -> None:  # noqa: A001 — intentional API surface
    """Set a single field in the current context.

    Subject to the overwrite rule: if the key already has a different value,
    a ``cyclops.context_overwrite_attempted`` warning is emitted and the
    original value is retained.

    Note: ``set`` modifies the *current* contextvars scope. Without a
    surrounding :func:`bind` (or framework-managed scope like the Flask
    middleware), values leak forward in the same task. Prefer ``bind`` for
    bounded scopes; reach for ``set`` only when adding to an existing scope.
    """
    _validate_keys([key])
    current = _ctx.get()
    new = _apply(current, {key: value}, warn_depth=3)
    _ctx.set(new)


def _reset_for_tests() -> None:
    """Test-only: reset the context to empty in the current task."""
    _ctx.set({})
