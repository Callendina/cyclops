"""Env-var parsing for cyclops-ui.

Read once at startup; route handlers receive the resulting `Config`
object via `get_config()`. Required vars hard-fail rather than silently
defaulting.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_KNOWN_APPS = "vispay,scout,gatekeeper,corkboard,cyclops-ui"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    environment: str
    grafana_public_url: str
    loki_url: str
    known_apps: tuple[str, ...]

    @property
    def is_staging(self) -> bool:
        return self.environment == "staging"

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"


_cached: Config | None = None


def load() -> Config:
    env = os.environ.get("ENVIRONMENT", "").strip()
    if env not in ("staging", "prod", "dev"):
        raise ConfigError(
            f"ENVIRONMENT must be one of staging|prod|dev, got {env!r}"
        )

    grafana_public_url = os.environ.get("GRAFANA_PUBLIC_URL", "").strip().rstrip("/")
    if not grafana_public_url:
        raise ConfigError("GRAFANA_PUBLIC_URL is required")

    loki_url = os.environ.get("LOKI_URL", "http://loki:3100").strip().rstrip("/")

    raw_apps = os.environ.get("KNOWN_APPS", _DEFAULT_KNOWN_APPS)
    known_apps = tuple(a.strip() for a in raw_apps.split(",") if a.strip())
    if not known_apps:
        raise ConfigError("KNOWN_APPS resolved to an empty list")

    return Config(
        environment=env,
        grafana_public_url=grafana_public_url,
        loki_url=loki_url,
        known_apps=known_apps,
    )


def get_config() -> Config:
    global _cached
    if _cached is None:
        _cached = load()
    return _cached
