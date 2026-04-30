"""Cyclops — structured event emission for the Callendina app fleet.

This module is the public API surface. The skeleton currently exposes the
package version and the exception hierarchy. The internal emitter
(``cyclops._emitter._emit``) is in place but is not yet bound to a public
``event()`` function — that lands in #13 alongside the typed helpers.

See DESIGN.md §1–§3 for the full design.
"""

__version__ = "0.1.0"

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
]
