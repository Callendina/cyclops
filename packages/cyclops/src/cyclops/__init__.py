"""Cyclops — structured event emission for the Callendina app fleet.

Public API (so far):

- :data:`__version__` — package version
- :mod:`cyclops.context` — per-request / per-task field binding
- :class:`~cyclops.exceptions.CyclopsError` and subclasses

The free-form ``event()`` and typed helpers (``cyclops.error``, lifecycle
helpers) land in #13 once forbidden-field enforcement is in place. See
DESIGN.md §1–§3.
"""

__version__ = "0.1.0"

from cyclops import context
from cyclops.exceptions import (
    CyclopsConfigError,
    CyclopsError,
    CyclopsValidationError,
)

__all__ = [
    "CyclopsConfigError",
    "CyclopsError",
    "CyclopsValidationError",
    "__version__",
    "context",
]
