"""Cyclops-UI — Flask app exposing a small JSON API over Loki.

For the Phase 4 scope check-in: this lands the API surface (so a Claude
session, or any tool, can hit
https://cyclops-staging.callendina.com/api/dev/errors with an X-API-Key
header and get back recent error events) but defers the branded landing
page / per-app dashboard iframes (DESIGN.md §10) — those come when
Phase 4 is fully resumed.

Routes:
- GET /health                 — liveness, no auth required
- GET /api/dev/errors         — recent error/critical events
- GET /api/dev/events         — generic event search

The cyclops env-var setup happens at the top of this module (before
`import cyclops`) so the library's config picks them up on first emission.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

# --- cyclops env vars (must precede `import cyclops`) ---------------------


def _cyclops_ui_version() -> int:
    """Callendina fleet convention: git commit count // 100."""
    try:
        count = int(
            subprocess.check_output(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=str(Path(__file__).resolve().parent.parent.parent.parent.parent),
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
        return count // 100
    except Exception:
        return 0


APP_VERSION = _cyclops_ui_version()

os.environ.setdefault("APP_NAME", "cyclops-ui")
os.environ.setdefault(
    "ENVIRONMENT",
    "staging"
    if (os.environ.get("CYCLOPS_UI_ENV") or "").lower().startswith("staging")
    else "prod",
)
os.environ.setdefault("APP_VERSION", f"v{APP_VERSION}")
os.environ.setdefault("CYCLOPS_COMPONENT", "cyclops-ui.web")


import cyclops  # noqa: E402

from flask import Flask, jsonify, request  # noqa: E402

from cyclops_ui import __version__ as cyclops_ui_version  # noqa: E402
from cyclops_ui.loki_client import (  # noqa: E402
    LokiError,
    parse_since,
    query_range,
)

LOKI_URL = os.environ.get("LOKI_URL", "http://loki:3100")
ENVIRONMENT = os.environ["ENVIRONMENT"]


app = Flask(__name__)


# --- Cyclops context per request -----------------------------------------


@app.before_request
def _bind_cyclops_context() -> None:
    if request.path == "/health":
        return
    fields: dict[str, object] = {}
    user = request.headers.get("X-Gatekeeper-User", "")
    if user:
        fields["user_id"] = user
    role = request.headers.get("X-Gatekeeper-Role", "")
    if role:
        fields["user_role"] = role
    group = request.headers.get("X-Gatekeeper-Group", "")
    if group:
        fields["user_group"] = group
    if request.headers.get("X-Gatekeeper-System-Admin", "") == "true":
        fields["is_system_admin"] = True
    rid = request.headers.get("X-Request-Id", "")
    if rid:
        fields["request_id"] = rid
    if fields:
        cm = cyclops.context.bind(**fields)
        cm.__enter__()
        request.environ["_cyclops_ctx"] = cm


@app.teardown_request
def _release_cyclops_context(_exc: BaseException | None) -> None:
    cm = request.environ.pop("_cyclops_ctx", None)
    if cm is not None:
        cm.__exit__(None, None, None)


# --- Routes ---------------------------------------------------------------


@app.get("/health")
def health() -> "tuple[dict, int]":
    return {"status": "ok"}, 200


@app.get("/api/dev/errors")
def api_errors() -> "tuple[dict, int]":
    """Recent error/critical events. Query params:
    - app:    filter by app label
    - since:  duration like '1h', '30m', '5m' (default 1h)
    - limit:  max events (default 100, hard cap 1000)
    """
    app_label = request.args.get("app", "").strip()
    since_seconds = parse_since(request.args.get("since"), default_seconds=3600)
    limit = min(int(request.args.get("limit", "100") or "100"), 1000)

    label_parts = ['source="cyclops"', 'level=~"error|critical"']
    if app_label:
        escaped = app_label.replace('"', '\\"')
        label_parts.append(f'app="{escaped}"')
    query = "{" + ", ".join(label_parts) + "}"

    try:
        events = query_range(
            LOKI_URL, query=query, since_seconds=since_seconds, limit=limit
        )
    except LokiError as exc:
        cyclops.error("cyclops_ui.api.errors", exception=exc, route="/api/dev/errors")
        return {"error": "loki_query_failed", "detail": str(exc)}, 502

    cyclops.event(
        "cyclops_ui.api.errors_queried",
        app_filter=app_label or "",
        since_seconds=since_seconds,
        result_count=len(events),
    )
    return {
        "query": query,
        "since_seconds": since_seconds,
        "limit": limit,
        "count": len(events),
        "events": events,
    }, 200


@app.get("/api/dev/events")
def api_events() -> "tuple[dict, int]":
    """Generic event search. Query params:
    - app:        filter by app label
    - level:      filter by level label (info|warning|error|critical|debug)
    - event_type: filter by event_type field (parsed from JSON, slower)
    - since:      duration like '1h', '30m' (default 1h)
    - limit:      max events (default 100, hard cap 1000)
    """
    app_label = request.args.get("app", "").strip()
    level = request.args.get("level", "").strip()
    event_type = request.args.get("event_type", "").strip()
    since_seconds = parse_since(request.args.get("since"), default_seconds=3600)
    limit = min(int(request.args.get("limit", "100") or "100"), 1000)

    label_parts = ['source="cyclops"']
    if app_label:
        label_parts.append(f'app="{app_label}"')
    if level:
        label_parts.append(f'level="{level}"')
    selector = "{" + ", ".join(label_parts) + "}"
    query = selector
    if event_type:
        escaped = event_type.replace('"', '\\"')
        query = f'{selector} | json | event_type="{escaped}"'

    try:
        events = query_range(
            LOKI_URL, query=query, since_seconds=since_seconds, limit=limit
        )
    except LokiError as exc:
        cyclops.error("cyclops_ui.api.events", exception=exc, route="/api/dev/events")
        return {"error": "loki_query_failed", "detail": str(exc)}, 502

    cyclops.event(
        "cyclops_ui.api.events_queried",
        app_filter=app_label or "",
        level_filter=level or "",
        event_type_filter=event_type or "",
        since_seconds=since_seconds,
        result_count=len(events),
    )
    return {
        "query": query,
        "since_seconds": since_seconds,
        "limit": limit,
        "count": len(events),
        "events": events,
    }, 200


@app.get("/")
def landing() -> "tuple[dict, int]":
    """Placeholder root page. Phase 4 will replace this with the branded
    Flask landing + app picker; for now it just points operators at
    Grafana and the corkboard."""
    return {
        "service": "cyclops-ui",
        "version": cyclops_ui_version,
        "environment": ENVIRONMENT,
        "api": {
            "errors": "/api/dev/errors?app=&since=1h&limit=100",
            "events": "/api/dev/events?app=&level=&event_type=&since=1h&limit=100",
        },
        "links": {
            "grafana": "/grafana/",
            "corkboard": "/corkboard/",
        },
        "note": "Full Phase 4 landing page (per DESIGN.md §10) is not yet built.",
    }, 200


# --- Lifespan: app.started / app.stopped ----------------------------------
# Flask doesn't natively have lifespan hooks like FastAPI; emit on import
# (app.started) and on the SIGTERM handler.

cyclops.app_started(loki_url=LOKI_URL)


def _on_shutdown(*_args: object) -> None:
    cyclops.app_stopped()


import atexit  # noqa: E402

atexit.register(_on_shutdown)
