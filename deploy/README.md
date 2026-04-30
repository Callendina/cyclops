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

## Staging (Linode)

The staging deployment runs the same stack but reads from `journald`
instead of file tails — gatekeeper and corkboard log via systemd, and
vispay's docker-compose uses the `journald` log driver, so all three
land in the same place.

```sh
docker-compose -f docker-compose.yml -f docker-compose.staging.yml up -d
```

Differences vs dev (encoded in `docker-compose.staging.yml`):

- Alloy uses `alloy/staging.alloy` (journald source) and bind-mounts
  `/var/log/journal`, `/run/log/journal`, `/etc/machine-id` read-only.
- Alloy runs with GID 101 (`systemd-journal`) so it can read the
  journal.
- Grafana serves under `/grafana` (subpath) and trusts `X-Gatekeeper-User`
  via `auth.proxy`. Caddy in front does the actual auth.

The Caddy block on the Linode adds a `handle /grafana/*` that
`forward_auth`s to Gatekeeper as system-admin, then `reverse_proxy`s
to grafana:3000. Loki and Alloy stay internal to the cyclops Docker
network.

## What this isn't yet

- **No provisioned dashboards yet**: Phase 3 lands the per-app, fleet,
  errors, auth, and Caddy dashboards. Until then, use Grafana Explore.
- **Stack runs on the same Linode as the apps**: that's deliberate at
  this scale (DESIGN.md §11). Production scale would split.

See [`../DESIGN.md`](../DESIGN.md) §7–§9 for the design rationale.
