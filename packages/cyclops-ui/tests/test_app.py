"""Smoke tests for cyclops-ui."""

from __future__ import annotations

import os
import re

import pytest


def test_imports() -> None:
    import cyclops_ui

    assert cyclops_ui is not None


def test_version_is_semver() -> None:
    import cyclops_ui

    assert re.match(r"^\d+\.\d+\.\d+", cyclops_ui.__version__), cyclops_ui.__version__


@pytest.fixture(scope="module")
def client():
    os.environ.setdefault("ENVIRONMENT", "dev")
    os.environ.setdefault("GRAFANA_PUBLIC_URL", "http://localhost:3000/grafana")
    os.environ.setdefault("KNOWN_APPS", "vispay,scout,gatekeeper,corkboard,cyclops-ui")

    from cyclops_ui.app import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_health_no_auth(client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_landing_renders(client, monkeypatch) -> None:
    # Stub out Loki so the landing page renders even without a live Loki.
    from cyclops_ui import app as app_module

    monkeypatch.setattr(app_module, "query_range", lambda *a, **kw: [])
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "cyclops" in body.lower()
    assert "vispay" in body  # picker card
    assert "scout" in body


def test_landing_event_expander(client, monkeypatch) -> None:
    """When events are present, each row must have a copy-json affordance."""
    from cyclops_ui import app as app_module

    fake_event = {
        "app": "vispay",
        "level": "error",
        "event_type": "vispay.tx.failed",
        "message": "card declined",
        "timestamp": "2026-04-30T17:00:00Z",
        "_loki_timestamp_ns": "1777520400000000000",
        "_labels": {"app": "vispay", "level": "error"},
    }
    monkeypatch.setattr(app_module, "query_range", lambda *a, **kw: [fake_event])
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "copy json" in body.lower()
    assert "ev-row" in body
    assert "ev-detail" in body
    # Full event JSON is embedded so user can copy it
    assert "vispay.tx.failed" in body
    assert "card declined" in body


def test_per_app_known(client, monkeypatch) -> None:
    from cyclops_ui import app as app_module

    monkeypatch.setattr(app_module.cyclops, "event", lambda *a, **kw: None)
    resp = client.get("/app/vispay")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "var-app=vispay" in body
    assert "cyclops-per-app" in body


def test_per_app_unknown_404(client) -> None:
    resp = client.get("/app/bogus-app")
    assert resp.status_code == 404


def test_about_renders(client) -> None:
    resp = client.get("/about")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "cyclops-ui" in body
    assert "version" in body.lower()


def test_global_emits_event(client, monkeypatch) -> None:
    from cyclops_ui import app as app_module

    captured: list[tuple[str, dict]] = []

    def _capture(name: str, **kw: object) -> None:
        captured.append((name, dict(kw)))

    monkeypatch.setattr(app_module.cyclops, "event", _capture)
    resp = client.get("/global")
    assert resp.status_code == 200
    assert any(name == "cyclops_ui.dashboard.viewed" for name, _ in captured)


def test_errors_iframe(client, monkeypatch) -> None:
    from cyclops_ui import app as app_module

    monkeypatch.setattr(app_module.cyclops, "event", lambda *a, **kw: None)
    resp = client.get("/errors")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "cyclops-errors" in body


def test_auth_iframe(client, monkeypatch) -> None:
    from cyclops_ui import app as app_module

    monkeypatch.setattr(app_module.cyclops, "event", lambda *a, **kw: None)
    resp = client.get("/auth")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "cyclops-auth" in body


def test_events_search_renders(client, monkeypatch) -> None:
    from cyclops_ui import app as app_module

    captured_query: dict[str, str] = {}

    def fake_query(*args, **kwargs):
        captured_query["query"] = kwargs["query"]
        return [
            {
                "app": "vispay",
                "level": "error",
                "event_type": "vispay.tx.failed",
                "message": "boom",
                "timestamp": "2026-04-30T17:00:00Z",
                "_loki_timestamp_ns": "1777520400000000000",
                "_labels": {"app": "vispay", "level": "error"},
            }
        ]

    monkeypatch.setattr(app_module, "query_range", fake_query)
    monkeypatch.setattr(app_module.cyclops, "event", lambda *a, **kw: None)
    resp = client.get("/events?app=vispay&level=error&event_type=vispay.tx.failed&since=1h")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "vispay.tx.failed" in body
    assert "copy json" in body.lower()
    # filter form preserves selections
    assert 'value="vispay" selected' in body or 'value="vispay"selected' in body
    # query was constructed with all filters
    assert 'app="vispay"' in captured_query["query"]
    assert 'level="error"' in captured_query["query"]
    assert 'event_type="vispay.tx.failed"' in captured_query["query"]


def test_events_search_empty(client, monkeypatch) -> None:
    from cyclops_ui import app as app_module

    monkeypatch.setattr(app_module, "query_range", lambda *a, **kw: [])
    monkeypatch.setattr(app_module.cyclops, "event", lambda *a, **kw: None)
    resp = client.get("/events")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "no events match" in body.lower()
