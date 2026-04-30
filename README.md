# Cyclops

Observability stack for the Callendina app fleet. Apps emit structured events via a Python library; events flow through an agent into Loki; Grafana dashboards visualise them; a thin Flask wrapper (cyclops-ui) provides a branded entry point behind Gatekeeper.

For the full design see [`DESIGN.md`](./DESIGN.md). Roadmap and todos live in the [cyclops-staging corkboard](https://cyclops-staging.callendina.com/corkboard/).

## Repo layout

This is a monorepo containing two pip-installable packages:

```
packages/
├── cyclops/        # the event-emission library
└── cyclops-ui/     # the Flask wrapper around Grafana
```

Each package versions and releases independently.

## Versioning and release tags

Strict semver per package. Tags in this repo are namespaced by package:

| Tag prefix | Package        | Example         |
|------------|----------------|-----------------|
| `lib-vX.Y.Z` | `cyclops`     | `lib-v0.1.0`    |
| `ui-vX.Y.Z`  | `cyclops-ui`  | `ui-v0.1.0`     |

A release of one package does not require a release of the other. Until either reaches 1.0, expect freer breakage between minor versions (semver convention for 0.x).

Wire-format compatibility for the `cyclops` library: minor bumps are additive (new fields, new event types, new helpers); major bumps may rename or remove. See [`DESIGN.md`](./DESIGN.md) §13 for detail.

## Development

Each package has its own `pyproject.toml`. From the repo root:

```
pip install -e packages/cyclops
pip install -e packages/cyclops-ui
pip install ruff pytest mypy
```

Common commands:

```
ruff check .            # lint
ruff format .           # format
mypy packages/cyclops/src packages/cyclops-ui/src
pytest                  # runs all tests across packages
```

Python 3.11+ required; tested against 3.11 and 3.12.
