"""Minimal Loki HTTP query client.

Wraps Loki's /loki/api/v1/query_range endpoint. Returns parsed events
(each line of the log stream JSON-parsed back into a dict) so route
handlers can reshape into JSON responses easily.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import urllib.request
import urllib.parse


class LokiError(RuntimeError):
    """Raised for non-2xx Loki responses."""


def _to_ns(seconds_ago: float) -> int:
    return int((time.time() - seconds_ago) * 1_000_000_000)


def query_range(
    base_url: str,
    *,
    query: str,
    since_seconds: float = 3600,
    limit: int = 100,
    direction: str = "backward",
    timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    """Run a LogQL query, return events newest-first as a list of dicts.

    Each returned dict is the parsed JSON of the original log line, with
    Loki's stream labels merged into a `_labels` key.
    """
    end_ns = _to_ns(0)
    start_ns = _to_ns(since_seconds)
    params = {
        "query": query,
        "start": str(start_ns),
        "end": str(end_ns),
        "limit": str(limit),
        "direction": direction,
    }
    url = f"{base_url}/loki/api/v1/query_range?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = resp.read()
            payload = json.loads(body)
    except urllib.error.HTTPError as e:
        raise LokiError(f"Loki returned {e.code}: {e.read()[:500]!r}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise LokiError(f"Loki query failed: {e}") from e

    return _flatten_events(payload)


def _flatten_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for stream in payload.get("data", {}).get("result", []):
        labels = stream.get("stream", {})
        for ts_ns, line in stream.get("values", []):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {"_raw": line}
            event.setdefault("_labels", labels)
            event.setdefault("_loki_timestamp_ns", ts_ns)
            out.append(event)
    out.sort(key=lambda e: e.get("_loki_timestamp_ns", "0"), reverse=True)
    return out


def parse_since(value: str | None, default_seconds: float = 3600) -> float:
    """Parse a duration like '1h', '30m', '6h', '5s' into seconds. Bare
    numbers are treated as seconds. Empty/None returns the default."""
    if not value:
        return default_seconds
    s = value.strip().lower()
    if not s:
        return default_seconds
    unit_map = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if s[-1] in unit_map:
        try:
            n = float(s[:-1])
        except ValueError:
            return default_seconds
        return n * unit_map[s[-1]]
    try:
        return float(s)
    except ValueError:
        return default_seconds


def iter_label_filter(label: str, value: str) -> Iterator[str]:
    """Yield a LogQL fragment to match a label=value, escaping the value."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    yield f'{label}="{escaped}"'
