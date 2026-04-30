# Cyclops stack — Loki + Grafana + Alloy

Compose for the observability stack: Alloy tails app stdout, Loki stores the events, Grafana renders them.

## Local dev

From this directory:

```sh
docker-compose up -d
```

Then point a cyclops-instrumented app's stdout at `./logs/<app>/<component>.jsonl`. Alloy auto-discovers any `*.jsonl` file under `./logs/`.

Example, running Vispay locally:

```sh
mkdir -p logs/vispay
cd ../../vispay && \
    .venv/bin/uvicorn app:app --port 18888 \
    > /path/to/cyclops/deploy/logs/vispay/web.jsonl 2>/dev/null &
```

Hit a few endpoints, then visit Grafana at http://localhost:3000/explore. Pick the **Loki** datasource and try:

- `{app="vispay"}` — every event from Vispay
- `{app="vispay", source="cyclops"} | json | event_type="request.completed"` — typed structured filter
- `{level="error"}` — fleet-wide errors
- `{source="cyclops"} | json | __error__=""` — only well-formed cyclops events

## Layout

```
deploy/
├── docker-compose.yml      # the three-service stack
├── alloy/config.alloy      # ingest pipeline (file → JSON parse → labels → Loki)
├── grafana/grafana.ini     # anonymous editor access (dev only)
├── grafana/provisioning/   # auto-provisioned Loki datasource
├── loki/config.yaml        # single-binary, filesystem storage, 30-day retention
└── logs/                   # gitignored bind mount; populate with app stdout
```

## Labels

Alloy promotes only low-cardinality fields to Loki labels:

- `app`, `env`, `host`, `component`, `level`, `source`

Everything else (`request_id`, `user_id`, `workflow_id`, `http_status_code`, `outcome`, business fields) stays in the log body and is queried via `| json | <field>=…` in LogQL.

## Versions

- Loki 3.4.1
- Grafana 11.6.1
- Alloy v1.7.5

Pinned tags so dev and staging match. Bump deliberately, in lockstep with any config changes the new version requires.

## What this isn't yet

- **Not behind Gatekeeper**: Grafana is wide open at `localhost:3000` for dev. The staging deployment (Phase 5) replaces `auth.anonymous` with `auth.proxy` reading `X-Gatekeeper-User`.
- **Not provisioning dashboards yet**: Phase 3 lands the per-app, fleet, errors, auth, and Caddy dashboards.
- **Not pulling from Docker socket / journald**: that's a Linode concern (Phase 5). The dev path is file-tail, which mirrors the bare-metal-app deployment shape.

See [`../DESIGN.md`](../DESIGN.md) §7–§9 for the design rationale.
