"""Cyclops — structured event emission for the Callendina app fleet.

This module is the public API surface. The skeleton currently exposes only
``__version__``; helpers (``event``, ``error``, request/cron/heartbeat helpers),
the context API, and forbidden-field/redaction utilities arrive in subsequent
todos. See DESIGN.md §1–§3.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
