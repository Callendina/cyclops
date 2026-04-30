# cyclops

Structured event emission for the Callendina app fleet.

Apps import `cyclops` and emit one line of JSON to stdout per event. An agent (Grafana Alloy) tails container stdout and bare-metal log files into Loki; Grafana dashboards do the rest. The library does not know about Loki, files, agents, or transports — it writes JSON; *something else* picks it up.

## Status

Pre-release skeleton. The package builds and imports; helpers and context API arrive in subsequent todos. See [`../../DESIGN.md`](../../DESIGN.md) §1–§3.

## Install

```
pip install -e .
```

For Flask middleware support:

```
pip install -e ".[flask]"
```

## Quickstart

(Coming with the next todos. Once the helpers land, this section will show typical usage: `cyclops.event(...)`, `cyclops.context.bind(...)`, and `cyclops.flask.init_app(app)`.)

## Versioning

Strict semver. Wire format is part of the public contract; minor bumps are additive (new fields, new event types, new helpers), major bumps may rename or remove. Until 1.0 expect freer breakage between minor versions.

Released via `lib-vX.Y.Z` tags in the parent monorepo.
