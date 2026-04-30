"""Process-level configuration loaded from environment variables.

The library reads four required environment variables on first emission:

- ``APP_NAME``         — the app slug (e.g. ``"vispay"``); becomes baseline ``app``
- ``ENVIRONMENT``      — typically ``"staging"`` or ``"prod"``; becomes baseline ``env``
- ``APP_VERSION``      — the app's release version; becomes the default
                          ``app_version`` context field
- ``CYCLOPS_COMPONENT``— the per-process component identifier (e.g. ``"vispay.web"``,
                          ``"scout.daily_ingest"``); becomes baseline ``component``

Missing any of these raises :class:`~cyclops.exceptions.CyclopsConfigError`. The
result is cached for the lifetime of the process; tests reset it via
:func:`_reset_for_tests`.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass

from cyclops.exceptions import CyclopsConfigError

_REQUIRED_ENV_VARS: tuple[str, ...] = (
    "APP_NAME",
    "ENVIRONMENT",
    "APP_VERSION",
    "CYCLOPS_COMPONENT",
)


@dataclass(frozen=True, slots=True)
class _Config:
    app: str
    env: str
    app_version: str
    component: str
    host: str


_config: _Config | None = None


def _load_config() -> _Config:
    """Load (and cache) configuration from the process environment."""
    global _config
    if _config is not None:
        return _config

    missing = [name for name in _REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise CyclopsConfigError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set APP_NAME, ENVIRONMENT, APP_VERSION, and CYCLOPS_COMPONENT "
            "before emitting events."
        )

    _config = _Config(
        app=os.environ["APP_NAME"],
        env=os.environ["ENVIRONMENT"],
        app_version=os.environ["APP_VERSION"],
        component=os.environ["CYCLOPS_COMPONENT"],
        host=socket.gethostname(),
    )
    return _config


def _reset_for_tests() -> None:
    """Test-only: drop the cached config so the next call re-reads the environment."""
    global _config
    _config = None
