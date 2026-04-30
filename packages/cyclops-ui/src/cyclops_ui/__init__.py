"""Cyclops-UI — Flask wrapper around Grafana for the Cyclops observability stack.

Provides a branded landing page and per-app drilldowns, with Grafana
dashboards embedded as iframes. Sits behind Caddy + Gatekeeper like any
other app in the fleet, and emits its own cyclops events for self-audit
(DESIGN.md §10, §12).
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
