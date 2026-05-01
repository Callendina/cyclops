"""Cyclops-UI — Flask app: branded shell + Loki query API.

Routes:
- GET /health                 — liveness, no auth required
- GET /                       — landing page (app picker + recent activity)
- GET /app/<app_name>         — per-app Grafana iframe
- GET /global                 — fleet Grafana iframe
- GET /errors                 — errors Grafana iframe
- GET /about                  — service + version info
- GET /_self/events           — recent cyclops-ui events (JSON)
- GET /api/dev/errors         — recent error/critical events (JSON)
- GET /api/dev/events         — generic event search (JSON)

Cyclops env-var setup happens at the top of this module (before
`import cyclops`) so the library's config picks them up on first emission.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

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
    else os.environ.get("ENVIRONMENT", "prod"),
)
os.environ.setdefault("APP_VERSION", f"v{APP_VERSION}")
os.environ.setdefault("CYCLOPS_COMPONENT", "cyclops-ui.web")


import cyclops  # noqa: E402
from flask import Flask, abort, jsonify, render_template, request  # noqa: E402

from cyclops_ui import __version__ as cyclops_ui_version  # noqa: E402
from cyclops_ui.config import get_config  # noqa: E402
from cyclops_ui.loki_client import (  # noqa: E402
    LokiError,
    parse_since,
    query_range,
)

CONFIG = get_config()


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


@app.context_processor
def _inject_chrome() -> dict[str, Any]:
    """Make env / user / nav data available to every template."""
    return {
        "environment": CONFIG.environment,
        "known_apps": CONFIG.known_apps,
        "grafana_public_url": CONFIG.grafana_public_url,
        "user": request.headers.get("X-Gatekeeper-User", ""),
        "is_system_admin": request.headers.get("X-Gatekeeper-System-Admin", "") == "true",
    }


# --- Iframe URL helpers ---------------------------------------------------


def _grafana_url(uid: str, *, params: dict[str, str]) -> str:
    base = CONFIG.grafana_public_url
    qs = urlencode({**params, "kiosk": "tv"})
    return f"{base}/d/{uid}/{uid}?{qs}"


# --- Routes ---------------------------------------------------------------


@app.get("/health")
def health() -> tuple[dict[str, str], int]:
    return {"status": "ok"}, 200


@app.get("/")
def landing() -> str:
    """Branded landing page: app picker + recent activity."""
    recent: list[dict[str, Any]] = []
    recent_error: str | None = None
    try:
        events = query_range(
            CONFIG.loki_url,
            query='{source="cyclops"}',
            since_seconds=300,
            limit=20,
        )
        for ev in events:
            recent.append(_event_for_table(ev))
    except LokiError as exc:
        recent_error = str(exc)
        cyclops.error(
            "cyclops_ui.landing.recent",
            exception=exc,
            route="/",
        )
    return render_template("landing.html", recent=recent, recent_error=recent_error)


@app.get("/app/<app_name>")
def per_app(app_name: str) -> str:
    if app_name not in CONFIG.known_apps:
        abort(404)
    grafana_url = _grafana_url(
        "cyclops-per-app",
        params={
            "var-app": app_name,
            "from": "now-24h",
            "to": "now",
        },
    )
    rows: list[dict[str, Any]] = []
    events_error: str | None = None
    try:
        raw = query_range(
            CONFIG.loki_url,
            query=f'{{app="{app_name}", source="cyclops"}}',
            since_seconds=3600,
            limit=100,
        )
        rows = [_event_for_table(ev) for ev in raw]
    except LokiError as exc:
        events_error = f"loki query failed: {exc}"
        cyclops.error("cyclops_ui.per_app.events", exception=exc, route="/app")

    cyclops.event(
        "cyclops_ui.dashboard.viewed",
        dashboard="per-app",
        app_filter=app_name,
    )
    return render_template(
        "per_app.html",
        title=f"{app_name}",
        grafana_url=grafana_url,
        events_url=f"/events?app={app_name}&since=24h",
        events=rows,
        events_error=events_error,
    )


@app.get("/global")
def fleet() -> str:
    grafana_url = _grafana_url(
        "cyclops-fleet",
        params={"from": "now-24h", "to": "now"},
    )
    cyclops.event("cyclops_ui.dashboard.viewed", dashboard="fleet")
    return render_template(
        "iframe.html",
        title="fleet",
        grafana_url=grafana_url,
        events_url="/events?since=1h",
    )


@app.get("/errors")
def errors() -> str:
    grafana_url = _grafana_url(
        "cyclops-errors",
        params={"from": "now-6h", "to": "now"},
    )
    cyclops.event("cyclops_ui.dashboard.viewed", dashboard="errors")
    return render_template(
        "iframe.html",
        title="errors",
        grafana_url=grafana_url,
        events_url="/events?level=error&since=6h",
    )


@app.get("/auth")
def auth() -> str:
    grafana_url = _grafana_url(
        "cyclops-auth",
        params={"from": "now-6h", "to": "now"},
    )
    cyclops.event("cyclops_ui.dashboard.viewed", dashboard="auth")
    return render_template(
        "iframe.html",
        title="auth",
        grafana_url=grafana_url,
        events_url="/events?app=gatekeeper&event_type=gatekeeper.access&since=6h",
    )


@app.get("/heartbeats")
def heartbeats() -> str:
    grafana_url = _grafana_url(
        "cyclops-health",
        params={"from": "now-1h", "to": "now"},
    )
    cyclops.event("cyclops_ui.dashboard.viewed", dashboard="health")
    return render_template(
        "iframe.html",
        title="health",
        grafana_url=grafana_url,
        events_url="/events?event_type=heartbeat&since=15m",
    )


@app.get("/events")
def events_search() -> str:
    """Filterable events page with copy-json affordance per row."""
    app_filter = request.args.get("app", "").strip()
    level = request.args.get("level", "").strip()
    event_type = request.args.get("event_type", "").strip()
    since_str = request.args.get("since", "1h").strip() or "1h"
    since_seconds = parse_since(since_str, default_seconds=3600)
    try:
        limit = int(request.args.get("limit", "100") or "100")
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 500))

    label_parts = ['source="cyclops"']
    if app_filter:
        label_parts.append(f'app="{app_filter}"')
    if level:
        label_parts.append(f'level="{level}"')
    selector = "{" + ", ".join(label_parts) + "}"
    query = selector
    if event_type:
        escaped = event_type.replace('"', '\\"')
        query = f'{selector} | json | event_type="{escaped}"'

    rows: list[dict[str, Any]] = []
    events_error: str | None = None
    try:
        raw = query_range(CONFIG.loki_url, query=query, since_seconds=since_seconds, limit=limit)
        rows = [_event_for_table(ev) for ev in raw]
    except LokiError as exc:
        events_error = f"loki query failed: {exc}"
        cyclops.error("cyclops_ui.events.query", exception=exc, route="/events")

    cyclops.event(
        "cyclops_ui.events.searched",
        app_filter=app_filter,
        level_filter=level,
        event_type_filter=event_type,
        since=since_str,
        result_count=len(rows),
    )

    return render_template(
        "events.html",
        events=rows,
        events_error=events_error,
        query=query,
        filters={
            "app": app_filter,
            "level": level,
            "event_type": event_type,
            "since": since_str,
            "limit": limit,
        },
    )


@app.get("/about")
def about() -> str:
    versions = {
        "cyclops_ui": cyclops_ui_version,
        "cyclops": cyclops.__version__,
        "hostname": socket.gethostname(),
    }
    return render_template("about.html", versions=versions)


@app.get("/_self/events")
def self_events() -> tuple[Any, int]:
    """Recent events emitted by cyclops-ui itself. Useful for debugging."""
    since_seconds = parse_since(request.args.get("since"), default_seconds=3600)
    limit = min(int(request.args.get("limit", "100") or "100"), 500)
    try:
        events = query_range(
            CONFIG.loki_url,
            query='{app="cyclops-ui", source="cyclops"}',
            since_seconds=since_seconds,
            limit=limit,
        )
    except LokiError as exc:
        cyclops.error("cyclops_ui.self_events", exception=exc, route="/_self/events")
        return jsonify({"error": "loki_query_failed", "detail": str(exc)}), 502
    return jsonify(
        {
            "since_seconds": since_seconds,
            "limit": limit,
            "count": len(events),
            "events": events,
        }
    ), 200


@app.get("/api/dev/errors")
def api_errors() -> tuple[Any, int]:
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
        events = query_range(CONFIG.loki_url, query=query, since_seconds=since_seconds, limit=limit)
    except LokiError as exc:
        cyclops.error("cyclops_ui.api.errors", exception=exc, route="/api/dev/errors")
        return jsonify({"error": "loki_query_failed", "detail": str(exc)}), 502

    cyclops.event(
        "cyclops_ui.api.errors_queried",
        app_filter=app_label or "",
        since_seconds=since_seconds,
        result_count=len(events),
    )
    return jsonify(
        {
            "query": query,
            "since_seconds": since_seconds,
            "limit": limit,
            "count": len(events),
            "events": events,
        }
    ), 200


@app.get("/api/dev/events")
def api_events() -> tuple[Any, int]:
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
        events = query_range(CONFIG.loki_url, query=query, since_seconds=since_seconds, limit=limit)
    except LokiError as exc:
        cyclops.error("cyclops_ui.api.events", exception=exc, route="/api/dev/events")
        return jsonify({"error": "loki_query_failed", "detail": str(exc)}), 502

    cyclops.event(
        "cyclops_ui.api.events_queried",
        app_filter=app_label or "",
        level_filter=level or "",
        event_type_filter=event_type or "",
        since_seconds=since_seconds,
        result_count=len(events),
    )
    return jsonify(
        {
            "query": query,
            "since_seconds": since_seconds,
            "limit": limit,
            "count": len(events),
            "events": events,
        }
    ), 200


# --- Helpers --------------------------------------------------------------


def _event_for_table(ev: dict[str, Any]) -> dict[str, Any]:
    labels = ev.get("_labels") or {}
    ts_short = ""
    ts_iso = ev.get("timestamp")
    if isinstance(ts_iso, str):
        try:
            dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
            ts_short = dt.astimezone(UTC).strftime("%H:%M:%S")
        except ValueError:
            ts_short = ts_iso[-8:] if len(ts_iso) >= 8 else ts_iso
    elif ev.get("_loki_timestamp_ns"):
        try:
            ns = int(ev["_loki_timestamp_ns"])
            dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC)
            ts_short = dt.strftime("%H:%M:%S")
        except (ValueError, TypeError):
            pass
    msg = ev.get("message") or ev.get("error_class") or ""
    raw = {k: v for k, v in ev.items() if k != "_labels"}
    return {
        "timestamp_short": ts_short,
        "app": ev.get("app") or labels.get("app") or "",
        "env": ev.get("env") or labels.get("env") or "",
        "level": ev.get("level") or labels.get("level") or "",
        "event_type": ev.get("event_type") or "",
        "message": str(msg)[:200],
        "raw_json": json.dumps(raw, indent=2, default=str, sort_keys=True),
    }


# --- Lifespan: app.started / app.stopped + heartbeat ---------------------
# Flask doesn't natively have lifespan hooks like FastAPI; emit on import
# (app.started) and on the SIGTERM handler. The heartbeat thread is the
# canary for fleet absence-detection (DESIGN.md §5).

cyclops.app_started(loki_url=CONFIG.loki_url)


_HEARTBEAT_INTERVAL_SECONDS = 60.0


def _emit_heartbeat() -> None:
    # Never let a heartbeat failure crash the server. Cyclops emission is
    # fire-and-forget by design (DESIGN.md §0).
    import contextlib

    with contextlib.suppress(Exception):
        cyclops.heartbeat(
            next_heartbeat_in_seconds=_HEARTBEAT_INTERVAL_SECONDS,
            worker_pid=os.getpid(),
        )
    _schedule_heartbeat()


def _schedule_heartbeat() -> None:
    import threading

    t = threading.Timer(_HEARTBEAT_INTERVAL_SECONDS, _emit_heartbeat)
    t.daemon = True
    t.start()


# Skip the heartbeat thread when running tests — pytest imports the
# module and we don't want a dangling Timer firing during the suite.
if os.environ.get("CYCLOPS_UI_DISABLE_HEARTBEAT") != "1":
    _schedule_heartbeat()


def _on_shutdown(*_args: object) -> None:
    cyclops.app_stopped()


import atexit  # noqa: E402

atexit.register(_on_shutdown)
