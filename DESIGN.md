# Cyclops — Design Document

This document covers the v1 design of Cyclops: an observability stack for the Callendina app fleet. It assumes the constraints already settled in the project brief (schema decisions, library shape, contextvars-based context, single-tenant Loki per env, Grafana-via-iframe, etc.) and explores the genuine design questions that remain.

For each topic: the question, the options worth weighing, the tradeoffs, a recommendation, and (where relevant) what would change at production scale.

The fleet that informs this design: user-facing Flask apps (Vispay and Scout as the initial candidates), shared services (Gatekeeper, Corkboard, Cyclops itself), and a collection of OS-cron-fired Python/bash scripts that feed Scout's database. Mostly single-user; occasionally 2–3 concurrent. Mixed Docker and bare-metal Python. Two environments (staging, prod), each on its own Linode.

---

## 0. Cross-cutting framing

Two framings shape almost every decision below; worth stating up-front so they don't have to be re-derived in each section.

**Stdout-JSON is the canonical wire format.** Every Cyclops emission, regardless of helper or app, is one line of JSON on stdout. The library does not know about Loki, files, agents, or transports. It writes JSON; *something else* picks it up. This matters because:

- Docker captures stdout for free; the agent tails it via Docker socket.
- Bare-metal Python scripts can have stdout redirected to a file (`>> /var/log/cyclops/<app>/<component>.jsonl`); the agent tails that directory. Same wire format, different transport.
- The library is trivially testable: capture stdout, assert on JSON.
- If the agent is broken, `docker logs <container>` and `tail -f /var/log/cyclops/...` are the fallback. Logs are never lost because of a transport bug.

**The library's only structural job is shape and context, not transport.** Helpers exist to make the *common* shapes ergonomic (request lifecycle, errors, cron, API calls). Free-form `cyclops.event()` is the escape hatch. No buffering, no async, no background threads in the library. This is partly philosophical — observability infrastructure that has its own bugs is worse than no observability — and partly pragmatic at this scale: stdout writes are fast enough.

---

## 1. Library API surface

**The question.** What should the typed-helper API look like, and where is the line between typed helpers and free-form `event()`?

### Helper categories

Three reasons to add a typed helper:

1. **The shape is universal and deserves canonical field names.** Request lifecycle, cron jobs, outbound API calls, errors — every app has these and field-name drift is the enemy of useful dashboards.
2. **The helper enforces something.** E.g. `error()` requires a captured exception or explicit traceback string; `request_completed` requires `http_status_code` and `duration_ms`.
3. **The helper picks defaults intelligently.** `level` derivation from outcome when an outcome is present. `outcome` itself is never inferred — see below.

If a helper does none of those, it's just a synonym for `event()` and shouldn't exist.

### Recommended helper set

**Tier 1 — canonical (library-emitted, names are baseline):**
- `cyclops.event()` — the free-form base case
- `cyclops.error()` — captures exception, fills top-frame fields + traceback
- `cyclops.request_received()` / `cyclops.request_completed()` — Flask middleware uses these
- `cyclops.api_call()` — outbound HTTP/RPC; caller may pass `outcome` if it has a business view of success
- `cyclops.heartbeat()` — long-running services emit "I am alive" pings
- `cyclops.app_started()` / `cyclops.app_stopped()` — app lifecycle markers
- `cyclops.cron_started()` / `cyclops.cron_completed()` — short-lived script wrappers

**Tier 2 — recommended patterns (typed because they're cross-cutting and we want consistent dashboards across the fleet):**
- `cyclops.rate_limit_exceeded()`
- `cyclops.admin_action()` — for the "admin user did a thing" audit trail; takes `target` and `action`
- `cyclops.authz_denied()` — for "user attempted X but role didn't permit"

**Tier 3 — explicitly NOT typed in v1, use `cyclops.event()`:**
- App-specific business events (`vispay.simulation.run`, `scout.report.generated`)
- Workflow lifecycle (see §6 — workflow is more conceptual than the library can usefully enforce)
- Job lifecycle for *in-process* background work (this is what `heartbeat` covers; "did this run" is what `cron_*` covers)

The brief mentioned `cyclops.job_started/completed/failed`. I'd push back on that: in the actual fleet, "jobs" are either OS cron scripts (covered by `cron_*`) or in-process loops (covered by `heartbeat` + app-specific events). Adding a third "job" concept is naming-overlap that creates more dashboard confusion than it removes.

### Argument shape

Three concerns: which fields are explicit args, which come from context, and how `**extra` composes.

**Recommendation:**
- Explicit kwargs for the *defining* fields of the helper (e.g. `request_completed(http_status_code, duration_ms)` is required; `outcome` is an optional kwarg the caller may add). The "defining" fields are the ones without which the helper doesn't make sense; everything else, including `outcome`, is optional and never library-inferred.
- All baseline fields (`app`, `env`, `host`, `component`, `cyclops_version`, `timestamp`) are filled by the library at emission, never passed by callers.
- All context-derived fields (`request_id`, `session_id`, `user_id`, `user_role`, `user_group`, `is_system_admin`, `workflow_id`, `app_version`) are auto-injected from `cyclops.context` at emission.
- `**extra` is accepted by every helper for ad-hoc fields. It is *merged into* the event after the typed fields. Caller-supplied keys cannot collide with baseline or context-derived field names — that's a hard error at emission (we own those names).
- `level` is a kwarg on every helper with a sensible default per helper; explicit override is allowed.

Why hard-error on baseline collisions: the schema is the contract. If someone passes `app="other-app"` we want that to fail loud, not silently overwrite `host`-derived data.

### `outcome` and status codes are at different layers

Status codes (HTTP, AS2805, ISO 8583, etc.) are protocol-layer facts. They describe what happened on the wire. `outcome` is a business-layer judgement — the app's claim about whether something succeeded in the sense that matters to the app. They are independent: an event can carry a status code without an outcome, an outcome without a status code, both, or neither.

The library's stance:

- The library never infers `outcome` from any status code. A 401 on a public marketing route and a 401 on a privileged-action attempt are wire-identical but mean different things; only the app knows which. Same for 4xx vs 5xx, ISO 8583 response codes, and so on.
- `outcome` is an optional kwarg on every helper that accepts it. If the app has a meaningful business view, it passes one; if it doesn't, it doesn't.
- Status-code fields are also optional (where the helper supports them) and may appear *without* outcome, or outcome may appear *without* a status code.
- `cyclops.event()` (free-form): outcome is optional. No coupling to status code presence.

This means a single user request can produce two events at different layers:

- The middleware-emitted `request.completed` records `http_status_code=200`, `duration_ms=...` and stops there — Flask's middleware sees only the HTTP layer and won't claim a business outcome it can't know.
- An app-emitted `vispay.payment.authorised` event records `outcome=failure`, `as2805_response_code="05"` (and no `http_status_code`) — the app sees the business layer and reports its own judgement against its own protocol.

Both events share the same `request_id`, so dashboards correlate them when needed. Crucially neither event has to fake the other layer's vocabulary.

Two consequences for helpers:

- `error()`: the helper itself implies `outcome=failure` (calling it is the app's declaration of failure). The helper sets `outcome=failure` automatically — this is *helper identity*, not status-code inference, so the principle holds.
- `cron_completed`: outcome is optional on the helper. In practice nearly every caller will pass one (a cron script that doesn't know whether it succeeded is barely emitting a useful event), but the library doesn't enforce it.

### `level` defaults

- `event()`: defaults to `info`.
- `request_received`: `info`.
- `request_completed`, `api_call`, `cron_completed`: `info` if no outcome is passed; if outcome is passed, derive — `success`/`skipped` → `info`, `partial` → `warning`, `failure`/`timeout`/`aborted` → `error`. The middleware (which emits `request.completed` without outcome) therefore lands at `info` regardless of HTTP status code; apps that want 5xx-as-error in their dashboards filter on `http_status_code` directly.
- `error()`: `error` (or `critical` if caller passes `level=critical`).
- `heartbeat`: `debug`. (Heartbeats are noisy by design; debug keeps them out of dashboards filtered to info+.)
- `cron_started` / `cron_completed(success)`: `info`. `cron_completed(failure)`: `error`.
- `app_started/stopped`: `info`.
- `admin_action`: `info` (audit trail, not a problem).
- `authz_denied`: `warning`.
- `rate_limit_exceeded`: `warning`.

### Where to draw the typed-vs-freeform line going forward

A new typed helper earns its place when (a) it's emitted from at least two apps with the same shape and (b) we want a fleet-wide dashboard that filters on it. Until both conditions hold, `cyclops.event()` is the right answer. This is a discipline question — easy to over-grow the typed surface.

### Redaction helpers

Two viable shapes:

**A. Pure functions:** `cyclops.redact_pan("4111111111111111")` returns `"4111********1111"`. Caller passes the redacted string into the event.

**B. Wrapper type:** `cyclops.event("...", pan=cyclops.RedactedPAN("4111..."))` — the library calls `.redact()` at emission.

Recommendation: **A (pure functions).** The wrapper type adds a class for no real win at this scale, and pure functions compose trivially (you can `print()` them, log them with stdlib `logging`, etc.). Provide at minimum:
- `redact_pan(s)` — keep first 6 + last 4, mask middle (industry convention)
- `redact_email(s)` — keep first char + domain, mask local-part
- `redact_token(s)` — keep last 4, mask everything else

These are *helpers*, not enforcement. Enforcement is the forbidden-fields list (§3).

### Production-scale note

At production scale, the library would need to grow: async-friendly helpers (using `contextvars.copy_context` for task spawning), batched emission to a local UDP socket, and probably a sampling/throttling layer. None of that is needed at this scale and adding it now would muddy the simple model.

---

## 2. Context API

**The question.** Exactly how does `cyclops.context` work, and what's its lifecycle in different process shapes (Flask request, OS cron script, long-running worker)?

### `set` / `get` semantics

Two conventions to choose between:

**A. Single key-value store with explicit field names:** `cyclops.context.set("user_id", "x@y.com")`, `cyclops.context.get("user_id")`. Library knows about a fixed allowlist of context fields.

**B. Bag-of-fields with optional schema:** `cyclops.context.set(user_id="x@y.com", workflow_id="...")`. Anything goes; schema enforced at emission.

Recommendation: **A**, with the allowlist matching the brief's context-derived fields exactly: `request_id`, `session_id`, `user_id`, `user_role`, `user_group`, `is_system_admin`, `workflow_id`, `app_version`. Setting an unknown context key is a `ValueError`. This forces the schema to be a real schema; it makes typos failures rather than silent no-ops; and it makes "what's the full possible context shape?" answerable by reading one constant.

If we later need ad-hoc context (rare in practice), add `cyclops.context.set_extra(**kwargs)` that lives in a separate `extra` namespace and only auto-injects under an opt-in flag. I'd defer this until we feel the pain.

### Threading and async

`contextvars.ContextVar` is the right primitive. Per-thread-and-per-task isolation comes for free. Spawned threads / `asyncio.create_task` calls inherit the parent's context at spawn time, which is the intuitive behaviour.

The one footgun: bare `threading.Thread(target=...)` does *not* inherit `contextvars` automatically; you have to use `contextvars.copy_context().run(target)` manually. Worth calling out in the docs but not worth special-casing in the library.

### The "mutable by addition, immutable by overwrite" rule

What "violation" should mean has three plausible answers:

- **Hard error:** `ValueError` on overwrite. Loud, but breaks legitimate retries and middleware re-runs.
- **Warn-and-keep-original:** emit a `cyclops` self-warning event, keep original value.
- **Warn-and-overwrite:** emit a self-warning, take the new value (last-write-wins).

Recommendation: **warn-and-keep-original.** It preserves the invariant that a request_id never changes mid-request (which is the whole point), surfaces the bug, and doesn't crash production code over an observability concern. The self-warning event names the conflicting key and source location so the bug is fixable.

The warning event itself is a `cyclops.context_overwrite_attempted` event at `warning` level — emitted to the same stdout stream so it shows up in dashboards.

### Lifecycle by process shape

Three cases the library has to handle:

**Flask request:** middleware (§4) calls `cyclops.context.bind(...)` in `before_request` with a fresh context, populated from headers. `teardown_request` clears it. The contextvars `Token` returned by `bind` is stashed on `flask.g` so teardown can call `reset(token)`. Result: each request runs in its own isolated context, no leakage between requests.

**OS cron script:** the script entry point calls `cyclops.context.bind(component="<name>", workflow_id=os.environ.get("WORKFLOW_ID"))`. Since the process exits when the script ends, no teardown is needed. `request_id` is auto-generated as a UUID at bind time if not supplied (so cron events still correlate within a single invocation).

**Long-running worker (e.g. APScheduler in-process):** worker loop calls `cyclops.context.bind(component="...", workflow_id=...)` at the start of each unit of work, and `cyclops.context.clear()` at the end. Pattern looks like Flask middleware but the user wires it themselves.

To make all three uniform, the library should provide one primitive — `cyclops.context.bind(**kwargs)` returning a contextmanager — and document the patterns. Apps in pattern 3 wrap each task in `with cyclops.context.bind(...): ...`.

### `request_id` for non-request contexts

Cron scripts and workers should still have a `request_id` — it's the correlation key for "all events from this invocation." Auto-generate one if not bound. Naming-wise I'd consider `execution_id` to be clearer than re-using `request_id` for non-request invocations, but the brief settled on `request_id` and the value of one consistent name across all contexts probably outweighs the slight semantic stretch.

### Production-scale note

At scale we'd want OpenTelemetry trace context interop (`traceparent` header parsing, propagation into context). Designing now in a way that doesn't preclude this: keep the context fields decoupled from any specific tracing format, treat OTel as a future shim that *populates* `request_id` (or its own `trace_id` field) from incoming headers.

---

## 3. Forbidden fields and redaction

**The question.** How do we prevent secrets, PANs, etc. from being logged, and what's the right behaviour when someone tries?

### The forbidden list

The brief's proposed list is good. I'd add: `client_secret`, `refresh_token`, `access_token`, `bearer`, `authorization` (the header value, not the concept), `card_data`, `cardholder_name` (debatable — PCI scope question; safer to forbid), `service_code`, `pvv`, `cavv`, `auth_code` (payments), `national_id`, `ssn`, `tax_id`. Trim or expand based on what your apps actually handle.

Compile to a `frozenset` of lowercase strings; comparison is case-insensitive on the field name.

### Where the check happens

Three points of enforcement:

1. **Field names in `**extra`** to any helper or `cyclops.event()`: lowercased and checked against the forbidden set.
2. **Field names in the typed kwargs** of helpers — these are library-owned and curated, so this is enforced by code review, not runtime.
3. **Nested values:** dict/list values passed in `**extra` get checked recursively for forbidden *keys*.

Forbidden *values* (a token that looks like a PAN appearing in a non-forbidden field like `notes`) are not detected by name-matching. Recommend a separate, optional value-pattern check for known formats (PAN Luhn check, JWT shape) that emits a *warning event* but doesn't block — it's much higher false-positive risk and shouldn't gate emission.

### What "block" means

Hard-fail at emission: `raise CyclopsForbiddenFieldError("field_name")`. Not a warning, not a scrub-and-continue. The whole point is to catch the bug at the call site. Scrubbing creates the illusion of safety — the developer thinks the value was logged when it wasn't.

The exception's message includes the field name and a pointer to the redaction helpers. This is the most-likely error to hit during integration; the message should be friendly.

### Configurability

Apps can *add* to the forbidden list via `cyclops.init(extra_forbidden_fields=[...])`. They cannot *remove*. If Vispay handles a field-name like `customer_full_pan` that's specific to it, it can add that without affecting other apps.

The base list lives in the library, version-locked. Removing a name from the base list would be a breaking change.

### Recursive depth

Recursive into `dict` and `list` values, with a max depth of (say) 10 to prevent runaway recursion on cyclic structures. At depth limit, emit a self-warning event ("forbidden check truncated at depth=10 for field X") and continue. A request body deep enough to hit this is suspect anyway.

### "Log the fact, not the value"

The brief asks whether there's a way to record "we know secrets *could* be here" without logging values. Yes: a marker pattern.

`cyclops.event("...", redacted_fields=["password", "card_number"])` — caller passes a list of field *names* that were intentionally suppressed at the call site. This is a regular `extra` field (not redacted-the-verb, just metadata about what was scrubbed). Useful for: the API-call helper, where the response body might contain secrets — caller passes `redacted_fields=["body"]` rather than the body itself.

Lightweight, no special library support needed beyond convention.

### Production-scale note

At scale, layered enforcement: library does the call-site check; agent does a regex sweep on the way to Loki as defence-in-depth; periodic offline scan of stored logs for pattern matches. None of that is needed for v1; flagging only because it's the natural growth path.

---

## 4. Flask middleware (`cyclops.flask`)

**The question.** What does `cyclops.flask.init_app(app)` actually do, and how does it compose with the rest of Flask?

### What `init_app` does

Five concrete things:

1. Registers a `before_request` hook that:
   - Reads `X-Request-Id`, `X-Gatekeeper-User`, `X-Gatekeeper-Role`, `X-Gatekeeper-Group`, `X-Gatekeeper-System-Admin` headers.
   - Reads the `gk_session` cookie, hashes it (SHA-256, hex), takes first 16 chars → `session_id`.
   - Calls `cyclops.context.bind(...)` with what it found; stashes the contextvars Token on `flask.g._cyclops_token`.
   - Emits a `request.received` event with `http.method`, `http.path`, `http.user_agent`, `http.remote_addr`.
   - Records start time on `flask.g._cyclops_start_time`.

2. Registers an `after_request` hook that emits `request.completed` with `http_status_code`, `duration_ms`, response size. The middleware does not set `outcome` — it only records what it observes at the HTTP layer. Apps that want a business outcome attached to a request emit their own event from the view (see "HTTP layer vs business layer" below).

3. Registers a `teardown_request` hook that calls `cyclops.context.reset(token)` to clean up. Uses `teardown` (not `after_request`) so it runs even on unhandled exceptions.

4. Registers an error handler for uncaught exceptions: emits `error` event with full exception fields and traceback, then re-raises (does NOT swallow). Flask's existing error handling continues; we're only *observing*. This is critical — Cyclops must not change app error semantics.

5. Reads `APP_NAME`, `ENVIRONMENT`, `APP_VERSION` from env vars, validates they're set, and exposes them so the library can populate `app`/`env`/`app_version` baseline fields. Hard-fail at app startup if missing.

### HTTP layer vs business layer

The middleware operates at the HTTP layer. It records what it can see — status code, duration, response size — and nothing else. It does not set `outcome` because it has no business view of what success means for any given route. A 401 might be a routine "please log in" or a serious "someone tried to do something they shouldn't"; a 200 might still represent a refused payment whose business outcome is `failure`. Only the app's view function knows.

Apps that care about business outcome for a given route emit their own event from the view, with whatever status-code fields and outcome the domain calls for:

- `cyclops.event("vispay.payment.authorised", outcome="failure", as2805_response_code="05", amount_cents=12345)` — no HTTP status code, because at the business layer the wire encoding doesn't matter.
- `cyclops.event("scout.report.generated", outcome="success", report_id=...)` — pure business event, correlates to the request via `request_id` from context.

Both kinds of event share `request_id` (auto-injected from context), so dashboards that need to combine the HTTP and business layers can join on it. Most dashboards will live at one layer or the other; the few that span both can do so explicitly.

This means the cyclops Flask middleware is *thin* — it instruments the request lifecycle and does no business interpretation. That's the right shape: the middleware can ship in v1 with no per-app configuration, and apps add business-layer events at their own pace.

The middleware's uncaught-exception handler is one place where outcome *is* set: it emits `cyclops.error()`, and `error()` itself implies `outcome=failure` as part of helper identity (per §1). No inference involved.

### Composition with other middleware

Flask runs `before_request` hooks in registration order. Cyclops should be registered **first** so that:
- Other middleware can call `cyclops.context.set(...)` and have it work (e.g. an auth middleware adding extra context).
- Other middleware's emissions go out with the request_id already bound.

Document this clearly: "register cyclops first."

`after_request` runs in *reverse* order (Flask convention). So Cyclops's after-request fires last, capturing the final response. Good.

### Configuration knobs

Two reasonable opt-outs:

- `cyclops.flask.init_app(app, auto_request_events=False)` — skip the auto request_received/completed events. Caller must emit their own. Useful if an app wants finer control (e.g. excluding healthcheck routes).
- Per-route opt-out via a decorator: `@cyclops.flask.skip_request_events`. Lighter touch than the global flag. Recommend providing both.

I'd resist any further knobs in v1. "Make it configurable" is the path to a complicated middleware that's hard to reason about.

### Healthcheck noise

Healthcheck endpoints (Caddy or Gatekeeper poking `/health`) will dominate request volume. The decorator above is the answer. Document the pattern; don't auto-skip routes by URL pattern (too magical).

### Missing-headers degradation

Three modes the middleware needs to handle:

1. **All Gatekeeper headers present:** populate everything.
2. **All Gatekeeper headers absent:** treat as anonymous public request (e.g. dev-mode running outside Gatekeeper). Populate `request_id` from header or generate. Leave user_*/session_id unset.
3. **Some present, some absent:** partial mode. Populate what's there.

Mode 2 is legitimate (dev), so the middleware doesn't warn at request time. Instead: at app startup, if `CYCLOPS_GATEKEEPER_REQUIRED=true` env var is set, the middleware tracks header presence over the first N requests and emits a startup warning if no Gatekeeper headers appear (suggesting misconfiguration). This is the "warn at app startup if expected headers seem to be missing" behaviour from the brief, made concrete.

`X-Request-Id` should always be present per the answers (Caddy injects it). If absent: middleware generates a UUID and adds a one-time warning event. Don't fail.

### Production-scale note

At scale: integrate with Werkzeug's exception hooks for cleaner uncaught-exception handling; consider OTel auto-instrumentation as an alternative path; structured slow-request alerting baked into the middleware.

---

## 5. Heartbeat and absence-of-events alerting

**The question.** How do we know when something stops running?

This is where the answer about cron usage really matters. The fleet has two distinct shapes:

- **Long-running processes:** Flask apps (gunicorn workers), and any in-process schedulers if they exist. These can emit periodic heartbeats.
- **Short-lived cron scripts:** OS cron fires Python or bash. These cannot emit heartbeats — they don't live long enough. Their "I ran" signal is the existence of a `cron.started` event on schedule.

The library should support both, but they're different mechanisms.

### Heartbeats for long-running processes

`cyclops.heartbeat()` emits a `heartbeat` event with `next_heartbeat_in_seconds` (so the absence-detection query knows the expected gap). Apps wire their own emission cadence — there's no library-managed thread.

Two reasons not to provide a library-managed thread:

1. The library's "no background threads" rule (from the brief). Holding the line keeps the model simple.
2. Threading semantics interact with gunicorn worker lifecycle, signals, and graceful shutdown in non-obvious ways. The cost of getting it right is high; the value is small.

Recommend a documented pattern: a Flask `before_first_request` hook (or app factory startup hook) that uses `threading.Timer` or APScheduler if the app already uses one. Keep it as user-code, not library code.

### Cron event pairs

`cyclops.cron_started(component)` at the top of the script. `cyclops.cron_completed(component, outcome, duration_seconds)` at the end. The component name should be stable across invocations of the same script.

A common pitfall: the script crashes between `started` and `completed`. We get a `started` and never see `completed`. That's actually useful signal for absence-detection (orphan starteds = "did the script crash?"). Pattern: wrap the script body in `try/except: cron_completed(outcome=failure); raise`. Provide a context manager: `with cyclops.cron("backfill_games"): ...`.

The context manager:
- Emits `cron_started` on enter.
- Emits `cron_completed(outcome=failure, exception=...)` if the body raises, then re-raises.
- Emits `cron_completed(outcome=success)` on clean exit.
- Captures duration automatically.

This is the right shape for the basketball-pipeline cron scripts.

### Absence detection in Loki

LogQL has `absent_over_time(...)`-style support for ranges; the standard pattern is:

```
sum by (app, component) (
  count_over_time({app="scout", source="cyclops"} | json | event_type="cron.started" [1h])
) == 0
```

This identifies cron scripts that *should* have run in the last hour but didn't. The query is per-script; we'd parameterise it in Grafana with an `$expected_cadence` variable.

For heartbeats, similar: `count_over_time({...} | json | event_type="heartbeat" [5m]) == 0` for a process expected to heartbeat every minute.

The hard part isn't the query — it's the configuration of "what should be running, and how often?" Two options:

**A. Implicit:** discover the set of components that have ever emitted heartbeat/cron_started events, expect them to keep doing so. Simple, but new components silently auto-register, and decommissioned components alert forever.

**B. Explicit:** a config file (in cyclops-ui) that declares "scout.daily_ingest should cron every 24h, vispay/gunicorn should heartbeat every 60s."

Recommendation: **B**, but defer to phase 8 (operations polish). For v1, build dashboards that visualise heartbeat/cron presence and let the human eye notice gaps. The explicit-config approach earns its place once we want actual alerting.

### Production-scale note

Production: declarative SLO config drives both alerting and dashboards. A separate metrics path (Prometheus) for these health signals rather than counting log events. For v1 we accept the simpler "count log events" approach.

---

## 6. Workflow tracking

**The question.** How does `workflow_id` work, especially across process boundaries (basketball pipeline = sequence of cron-fired scripts, each its own process)?

This is the topic where I most want pushback, because the right answer depends on what "workflow" means to you.

### Two interpretations

**A. workflow = a logical user-facing operation** that can span multiple HTTP requests, multiple cron invocations, multiple background jobs. E.g. "ingest game GSW vs LAL on 2026-04-12 and produce its report" — touches 4 cron scripts and a Scout request.

**B. workflow = a single execution unit** that may have steps but lives in one process. E.g. one cron script doing 5 phases.

For Cyclops v1 I'd treat **A** as the goal but implement only the bare primitives. Specifically:

- `workflow_id` is a context field. It's just a string. Library doesn't validate format. Apps can set it from anywhere.
- It propagates within a process via contextvars (free, automatic).
- Across processes, the *application* is responsible for passing it. Two patterns:
  - **Env var:** orchestrator script sets `WORKFLOW_ID=...` before invoking the next script; the next script reads it and binds to context.
  - **DB-state-derived:** if scripts are coordinated by DB state (script B looks up "what got ingested by script A"), pull `workflow_id` from the DB row that script A wrote.

The library doesn't try to do cross-process propagation magic. That's a rabbit hole.

### Why not richer workflow primitives in v1

Tempting helpers: `cyclops.workflow_started(workflow_id, type="basketball.game_ingest")`, `cyclops.workflow_step("phase_2_pbp_parse")`, `cyclops.workflow_completed(...)`. These add value when you want a "workflow timeline" dashboard.

But:
- They commit us to a workflow ontology before we've used the data
- The information is reconstructible from the contextvars-tagged events alone (`event_type:* AND workflow_id:X` ordered by time = the timeline)
- Adding helpers later is non-breaking; removing them is a wire-format break

Defer until we want a specific workflow dashboard and feel the pain of reconstructing it from base events. That's a phase 8+ concern.

### Workflow vs request relationship

They're orthogonal:
- A workflow can span 0..N requests (a cron-only workflow has 0 requests; a workflow that includes user actions has 1+).
- A request can spawn 0..N workflows (most requests don't trigger any; a "kick off ingest" admin request triggers 1).

The library encodes this by treating `request_id` and `workflow_id` as independent context fields. Same event can have both. Filtering by either gets you the relevant slice.

### What the library does not provide (deliberately)

- No parent-workflow / child-workflow nesting in v1. If we need it, add `parent_workflow_id` later.
- No workflow-step enum. `event_type` already gives you steps via dotted naming (`basketball.ingest.parse_pbp`).
- No workflow status field beyond `outcome` on individual events.

### Production-scale note

OpenTelemetry's trace/span model is the same shape as workflow/step. At scale, replace `workflow_id` semantics with OTel `trace_id` and step events with `span` events. Designing now in a way that doesn't preclude this: keep `workflow_id` opaque (don't constrain its format), don't build helpers that assume specific step semantics.

---

## 7. Agent

**The question.** Which agent (Alloy, Promtail, Vector), and how is it configured?

### Agent choice

- **Promtail** is the original Loki agent but is being deprecated in favour of Alloy. Don't pick a deprecated tool for a learning exercise.
- **Vector** is excellent and more general-purpose, but isn't Grafana-stack-native — config style is different, integration with Loki is fine but not first-class, and the wider docs/ecosystem-around-Loki assume Promtail/Alloy.
- **Grafana Alloy** is the current Grafana-stack choice. Same deployment model as Promtail (single binary, stateful tail position file), config language is HCL-ish, native Loki output, native journald input, native Docker input.

Recommendation: **Alloy.** Aligned with "learn current best practice" and Grafana-native. Vector is a fine alternative if you ever outgrow Alloy or want to ship to multiple destinations.

### Inputs

The agent reads from at minimum:

1. **Docker container stdout** via Docker socket. This is the path for all containerised apps (Cyclops events, plus Caddy access logs, plus other container output).
2. **Bare-metal log files** (`/var/log/cyclops/*/*.jsonl`) for cron-fired Python scripts and any non-containerised processes.
3. **journald** for system logs (sshd, kernel, systemd unit lifecycle).
4. **Caddy access logs** specifically — these are JSON if Caddy is configured for JSON output (recommend), and may come either via Docker stdout (if Caddy is in a container) or via a file (if bare-metal). Configured as a separate input either way so its parsing pipeline is distinct.

### Source detection

The brief's rule is good: presence of `cyclops_version` field in JSON → it's a Cyclops event, label `source=cyclops`. Otherwise:
- JSON with Caddy-shaped fields → `source=caddy`
- journald → `source=system`
- Anything else (raw stdout from a container that's not using Cyclops) → `source=app_stdout`

Implement this as one of:
- Per-input static labelling: each input pipeline assigns its source label statically based on where it's reading from. Simpler.
- Content-based detection: parse JSON, look for marker fields, label dynamically. More robust to misconfigured apps but more complex.

Recommendation: **per-input static labelling for Docker (label by container name pattern), plus content-based detection for `source=cyclops` vs `source=app_stdout` within the Docker input.** Cyclops events are valuable enough to dashboard separately even when they come from the same container as raw print() output.

### Labels (low-cardinality only)

Labels in Loki should be low cardinality. Each unique label combination creates a separate stream; high-cardinality labels destroy ingestion performance.

Recommended labels:
- `app` — bounded set (Vispay, Scout, Gatekeeper, etc.). ~10.
- `env` — `staging` or `prod`. 2.
- `host` — Linode hostname. 2 (one per env).
- `source` — `cyclops`, `caddy`, `system`, `app_stdout`. 4.
- `level` — 5 values.
- `component` — bounded per-app (e.g. `vispay.gunicorn`, `vispay.daily_settle`, `scout.web`, `scout.daily_ingest`). Tens to low hundreds across the fleet.

Total label cardinality: ~10 × 2 × 2 × 4 × 5 × ~50 ≈ 40,000 streams worst case. Loki's default limits are 100k streams per tenant. We're fine.

**Not labels** (stay in log body, queryable via `| json`):
- `request_id`, `session_id`, `workflow_id` — high cardinality (one per request/workflow).
- `user_id`, `user_role`, `user_group` — could be high cardinality (depends on user count, but principle holds).
- `event_type` — bounded but several hundred eventually; better as a parsed field.
- `outcome` — bounded (5 values) but adds a dimension that explodes streams when crossed with `level`.
- `http_status_code` — bounded but high.

If we really want fast filtering on `event_type` (likely), we use Loki's *structured metadata* (a newer feature, between labels and body) or rely on `| json | event_type="..."` parsing in queries. Recommend: queries use parsing for v1; revisit structured metadata if performance suffers.

### Caddy access logs

Configure Caddy for JSON access logs. Fields to include: `request_id` (Caddy generates it; needs to be propagated into the log line), `method`, `host`, `uri`, `status`, `duration`, `bytes`, `request.headers.User-Agent`, `request.remote_ip`. Caddy's `log` directive supports JSON output with field selection.

The agent labels these `source=caddy` and parses to extract `app` from the `host` field (mapping host → app via Alloy's static config).

### journald integration

Alloy has a `loki.source.journal` input. Subscribe to all units; filter (or label) by unit name. Critical fields: `_SYSTEMD_UNIT`, `MESSAGE`, `PRIORITY`. Map `PRIORITY` to Cyclops `level` enum at the agent level so dashboards work uniformly.

### Agent positioning

Single Alloy instance per host (not per-app sidecar). Reads Docker socket (one mount), tails files (one bind mount of `/var/log/cyclops`), reads journald (one socket). Simpler than sidecars and totally adequate at this scale.

Security implication of Docker socket mount: Alloy can do anything Docker can. Mitigation: read-only mount where possible (`/var/run/docker.sock:/var/run/docker.sock:ro`), accept the residual risk, document it. At this scale and on a host you control completely, this is fine. At production scale we'd use a Docker socket proxy (Tecnativa's, etc.) to expose only the read endpoints.

### Production-scale note

Scale-out path: Alloy per-host writing to a centralised Loki cluster, with `tenant_id` label, S3-backed chunks, microservices-mode Loki. None of that for v1.

---

## 8. Loki

**The question.** What configuration is right for hobby-scale single-host Loki?

Single-binary mode (formerly "monolithic mode"). One binary, one process, all Loki components in-process. Recommended for under 100GB ingestion/day; we'll be under 1GB/day.

### Storage

- Filesystem storage. Chunks at `/loki/chunks`, index at `/loki/index`. Both on the same disk; no point splitting at this scale.
- Use TSDB index format (current default). Don't use boltdb-shipper (older).
- Single schema config; no schema versioning needed for v1.

### Retention

30 days globally. Configured via `compactor.retention_enabled: true` and `limits_config.retention_period: 30d`. The compactor handles deletion.

If you want per-stream retention later (e.g. keep `level=error` longer), Loki supports it via `retention_stream` config. Defer.

### Resource and rate limits

Defensive limits to prevent a misbehaving app from filling disk:

- `ingestion_rate_mb: 4` — 4 MB/s per tenant.
- `ingestion_burst_size_mb: 8` — small bursts allowed.
- `max_streams_per_user: 10000` — well above our calculated need.
- `max_line_size: 256kb` — caps any single absurd log line.
- `max_query_series: 5000` — protects Grafana from runaway queries.
- `retention_period: 720h` — 30 days.

These are sized for our scale; would scale up by orders of magnitude in production.

Disk-fullness handling: Loki doesn't gracefully handle disk-full. The right defence is a host-level disk-usage alert (separate path) at 80% used. For v1 this is "I'll remember to check `df -h` occasionally"; phase 8 it becomes a real alert.

### Compaction

Compactor runs in-process in single-binary mode. Default settings (compaction every 10m, retention checks every 10m) are fine.

### Auth

`auth_enabled: false`. Single-tenant. No `X-Scope-OrgID` plumbing. Documented and locked in the config; revisit if we add a second consuming environment that should be isolated.

### Production-scale note

Microservices-mode Loki, S3 (or similar) chunk storage, BoltDB-shipper or TSDB shared via S3, Memcached for query results. Multi-tenant with proper auth. None of that for v1.

---

## 9. Grafana

**The question.** Configuration, auth, embedding, and the v1 dashboard set.

### Grafana version and provisioning

OSS Grafana, latest stable. All configuration via:
- `grafana.ini` for server config
- Provisioning YAML files for datasources and dashboards
- Dashboard JSON files committed to the repo

Provisioning paths: `/etc/grafana/provisioning/datasources/`, `/etc/grafana/provisioning/dashboards/`, with the dashboard provider pointing at `/var/lib/grafana/dashboards/` (where the JSON files are bind-mounted from the repo).

### Datasource

One Loki datasource per Grafana instance, pointing at the local Loki on the same Docker network. URL: `http://loki:3100`. No auth.

Set as default.

### Auth

Three options:

**A. `auth.proxy`** — trust an upstream-injected header (e.g. `X-Gatekeeper-User`) as the authenticated user. Grafana auto-creates users on first sight. This is the right pattern given Gatekeeper sits in front.

**B. `auth.anonymous`** — anyone who reaches Grafana sees everything. Works because Gatekeeper is the moat, but loses per-user attribution in Grafana audit logs.

**C. Real Grafana login** — independent username/password store. Defeats the SSO point of Gatekeeper.

Recommendation: **A (`auth.proxy`).** Configure with `header_name = X-Gatekeeper-User`, `header_property = email`, `auto_sign_up = true`, `whitelist = <Cyclops-UI container IP or network>` so only requests proxied through cyclops-ui (which is itself behind Gatekeeper) are trusted.

The `whitelist` is critical: without it, any request reaching Grafana with a forged `X-Gatekeeper-User` header is auto-trusted. The whitelist constrains trust to requests originating from inside the Cyclops Docker network.

All Grafana users get the `Editor` role automatically (via `auth.proxy.auto_sign_up_role` or similar). For v1 there's no user-level permission differentiation — the brief settled this.

### Embedding configuration

In `grafana.ini`:
- `[security] allow_embedding = true`
- `[security] cookie_samesite = none` (so the iframe cookie works cross-origin from cyclops-ui)
- `[security] cookie_secure = true` (we're behind HTTPS at Caddy)
- Strip / override Grafana's `X-Frame-Options: deny` default (Caddy can do this on the response if Grafana's allow_embedding doesn't fully drop it, depending on version).

CSP: Grafana sets a default CSP; need to either add cyclops-ui's origin to `frame-ancestors` or disable Grafana's CSP and rely on Caddy. Disabling Grafana CSP is simpler at this scale.

### Dashboards-as-code

JSON dashboards in `cyclops-ui/dashboards/` (or a top-level `dashboards/`) committed to the repo, bind-mounted into Grafana, picked up by the file-based dashboard provider on Grafana startup. The provider can be configured with `allowUiUpdates: false` to prevent drift between repo and runtime — UI edits get overwritten on next provisioning sync.

For v1 I'd actually recommend `allowUiUpdates: true` so you can iterate in the UI; export to JSON when happy and check in. Tighten to `false` once dashboards stabilise.

### v1 dashboard set

Five dashboards is the right size for v1. More than five and they start cannibalising each other's clarity.

1. **Per-app dashboard.** Variables: `$app` (required), `$env` (auto from datasource or var), `$time_range`. Panels:
   - Request rate by status code (stacked area)
   - Request latency p50/p95/p99 (line)
   - Error rate (line, separate panel for visibility)
   - Top event_types by count (table)
   - Recent errors (logs panel)
   - Active workflows (table, distinct workflow_ids in window)

2. **Global / fleet dashboard.** No `$app` variable. Panels:
   - Request rate by app (stacked)
   - Error rate by app (line)
   - Active hosts (single-stat per host with last-event-time)
   - Heartbeat status (cron + worker presence grid)

3. **Errors dashboard.** Filter `level >= warning`. Panels:
   - Error timeline by app
   - Top error event_types
   - Recent errors with traceback (logs panel, expanded)
   - Error rate by component

4. **Auth and security dashboard.** Filter on Gatekeeper events + `authz_denied` + `rate_limit_exceeded` + system sshd events. Panels:
   - Login attempts (success/failure)
   - MFA failures
   - sshd login attempts (successful + failed)
   - Authz denials by app and user
   - Rate-limit hits

5. **Caddy traffic dashboard.** Source-filtered to `caddy`. Panels:
   - Request rate by host (the Caddy-level view, useful for traffic that doesn't reach app-level Cyclops events — e.g. static assets)
   - Latency at the Caddy layer
   - Status code distribution
   - Top URIs

This matches the phase-3 deliverable. Phase 6+ might add per-app-specific dashboards (e.g. "Vispay simulation runs", "Scout pipeline").

### Variable conventions

- `$app` — datasource-driven (`label_values(app)`). Default: `All`.
- `$env` — generally fixed per-Grafana-instance (each env has its own Grafana). If we want a single Grafana later, this becomes a real variable.
- `$component` — secondary filter, datasource-driven, defaults to `All`.
- `$time_range` — built-in.

### Naming

- Dashboard names: `<area> — <scope>` e.g. `Cyclops — Per-App`, `Cyclops — Errors`.
- Panel names: short, descriptive verb phrases. "Request rate", not "Number of requests over time".
- Tags: `cyclops`, plus area tags (`auth`, `traffic`, `errors`).

### Production-scale note

SSO via OIDC/OAuth (real Grafana auth backend) replaces `auth.proxy`. Per-user dashboard permissions. Probably folder hierarchies. None for v1.

---

## 10. Cyclops-UI design

**The question.** What does the Flask app actually do, and how is it structured?

### Routes

```
/                — landing page with app picker and recent activity
/app/<app_name>  — per-app view (iframe Grafana per-app dashboard scoped to ?var-app=<app_name>)
/global          — global fleet view (iframe Grafana fleet dashboard)
/errors          — errors dashboard iframe
/auth            — auth/security dashboard iframe
/caddy           — Caddy traffic dashboard iframe
/about           — versions of cyclops library, cyclops-ui, Grafana, Loki, agent; environment info
/health          — liveness probe (always 200; no Gatekeeper needed)
/_self/events    — JSON list of recent events emitted by cyclops-ui itself (debug)

# Reserved for future, not implemented in v1:
/events          — schema browser (phase 9+)
/alerts          — alert config UI (phase 9+)
/admin           — operator actions (phase 9+)
```

`/health` should be excluded from Gatekeeper's protection at the Caddy layer (Caddy's `forward_auth` directive matchers can skip it), so Caddy itself can probe liveness without bouncing through Gatekeeper.

### Structure

Simple Flask layout — no need for blueprints in v1 (we'll have <10 routes). Single `app.py` with route handlers, `templates/` for Jinja, `static/` for CSS. Add blueprints when the app grows beyond ~15 routes.

```
packages/cyclops-ui/
  pyproject.toml
  cyclops_ui/
    __init__.py
    app.py            # Flask app factory, routes
    config.py         # env var parsing
    templates/
      base.html
      landing.html
      iframe.html     # generic iframe wrapper (per-app, errors, auth, etc. all reuse)
      about.html
    static/
      css/main.css
  dashboards/         # Grafana dashboard JSON, bind-mounted into Grafana
    per-app.json
    global.json
    errors.json
    auth.json
    caddy.json
  Dockerfile
```

### Configuration

Env vars only, parsed at startup:
- `APP_NAME=cyclops-ui` (used by cyclops library)
- `ENVIRONMENT=staging|prod`
- `APP_VERSION=...` (injected at build/deploy time)
- `GRAFANA_URL=http://grafana.cyclops-internal.local` (the URL apps in iframes load)
- `GRAFANA_PUBLIC_URL=https://cyclops.staging.callendina.com/grafana` — the URL the *browser* loads from (could differ from internal)
- `LOKI_URL` — reserved for future direct-query features; not used in v1.
- `KNOWN_APPS=vispay,scout,gatekeeper,corkboard,bb_tool` — the picker shows these. Could be auto-discovered from Loki labels but explicit list is simpler and matches the "operators define what's expected" principle.

Hard-fail at startup if required vars are missing.

### Auth integration

Cyclops-ui sits behind Gatekeeper like any other app. It reads `X-Gatekeeper-*` headers for context binding, but doesn't do per-user authorisation in v1 — any authenticated user gets full access. The `is_system_admin` header is captured into context (so audit events show admin status) but doesn't gate any route.

### Talking to Grafana

V1: only iframe URLs. Cyclops-ui constructs a URL like `<GRAFANA_PUBLIC_URL>/d/per-app/per-app?var-app=vispay&from=now-24h&to=now&kiosk=tv` and renders an iframe. `kiosk=tv` strips Grafana's chrome, leaving panels.

The browser loads the iframe directly from Grafana — it's not proxied through cyclops-ui. The `auth.proxy` flow works because Caddy still injects `X-Gatekeeper-User` on the iframe's HTTP request (the browser sends its session cookie; Gatekeeper validates; Caddy forwards to Grafana with the header).

V1 has no direct Grafana API calls from cyclops-ui. Future features (e.g. embedded panels with explicit time ranges, search box that proxies LogQL) would justify adding API calls; deferring keeps v1 simple.

### Self-emission

Cyclops-ui uses `cyclops` like any other app. Events to emit:
- `cyclops_ui.dashboard.viewed` — when a user loads `/app/<x>` or any iframe page; includes the dashboard name and filters applied.
- `cyclops_ui.app.selected` — picker selections.
- `request.received` / `request.completed` — auto from Flask middleware.
- `app_started` — at startup.

### Visual design

Minimal. Page chrome: header with environment badge (`STAGING` red, `PROD` green; visible at all times so you don't fat-finger), nav links, user identity from header, sign-out (link to Gatekeeper logout). Body: the iframe or the landing page. CSS: hand-rolled, ~100 lines. No JS framework. Keep it small enough that "view source" is readable.

### Production-scale note

Grow into a real app: real RBAC, dashboard search, query history, embedded LogQL editor. None for v1.

---

## 11. Deployment as docker-compose

**The question.** What's the v1 docker-compose topology, and how do bare-metal pieces integrate?

### Per-environment topology

Each env (staging, prod) runs an identical compose file on its Linode. No cross-host networking.

Services:
- `loki` — single-binary Loki
- `grafana` — Grafana OSS
- `alloy` — the agent
- `cyclops-ui` — the Flask wrapper

External dependencies (already running on the host, not part of the Cyclops compose file):
- `caddy` — reverse proxy (system service or its own compose)
- `gatekeeper` — auth service (its own compose)
- The other apps (Vispay, Scout, Corkboard, bb_tool) — their own composes
- The bare-metal Python cron scripts — system cron + filesystem

### Network layout

One Docker network `cyclops-net` for the Cyclops services to talk to each other (Alloy → Loki, Grafana → Loki, cyclops-ui → Grafana via internal name).

A second network `caddy-net` (or whatever Caddy's network is called) shared with cyclops-ui only, so Caddy can reach cyclops-ui. Cyclops-ui is the only service on both networks. Loki is *not* exposed beyond `cyclops-net`.

Caddy reaches Grafana through cyclops-ui? No — Caddy needs to reach Grafana directly because the browser iframe loads Grafana URLs. So Grafana is also on `caddy-net`, exposed at a path like `/grafana` behind Caddy + Gatekeeper.

Final: cyclops-ui and grafana both on `caddy-net`; loki and alloy only on `cyclops-net`. Loki is unreachable from outside Docker.

### Volume mounts

```
loki:
  - ./loki/data:/loki         # chunks + index
  - ./loki/config.yaml:/etc/loki/local-config.yaml:ro

grafana:
  - ./grafana/data:/var/lib/grafana                       # Grafana state DB
  - ./grafana/dashboards:/var/lib/grafana/dashboards:ro   # dashboards JSON (committed)
  - ./grafana/provisioning:/etc/grafana/provisioning:ro   # datasource + dashboard provider config
  - ./grafana/grafana.ini:/etc/grafana/grafana.ini:ro

alloy:
  - /var/run/docker.sock:/var/run/docker.sock:ro          # for Docker container log capture
  - /var/log/cyclops:/var/log/cyclops:ro                  # bare-metal Python script log files
  - /var/log/journal:/var/log/journal:ro                  # journald
  - ./alloy/config.alloy:/etc/alloy/config.alloy:ro
  - ./alloy/data:/var/lib/alloy                           # tail position state

cyclops-ui:
  (no mounts; stateless)
```

### Bare-metal cron script integration

The cron scripts run on the host (not in containers), invoked by system crontab. Their stdout is redirected to `/var/log/cyclops/<app>/<component>.jsonl`:

```
# /etc/crontab snippet
0 2 * * * jonno /opt/scout/bin/run-daily-ingest.sh >> /var/log/cyclops/scout/daily_ingest.jsonl 2>&1
```

The script does whatever it does; its Python invocation imports `cyclops` and emits events to stdout, which the redirect appends to the file. Alloy is mounted on `/var/log/cyclops` and tails the directory.

Log rotation handled by `logrotate` on the host (system service). Alloy handles file rotation gracefully (re-opens on rotation).

The `/var/log/cyclops/<app>/<component>.jsonl` path convention encodes `app` and `component` into the filename, which Alloy parses out into labels.

### Caddy snippet

```caddy
# Cyclops-UI behind Gatekeeper, with /health bypassing auth
cyclops.staging.callendina.com {
    @health path /health
    handle @health {
        reverse_proxy cyclops-ui:8000
    }

    handle /grafana* {
        forward_auth gatekeeper:8080 {
            uri /verify
            copy_headers X-Gatekeeper-User X-Gatekeeper-Role X-Gatekeeper-Group X-Gatekeeper-System-Admin
        }
        reverse_proxy grafana:3000
    }

    handle {
        forward_auth gatekeeper:8080 {
            uri /verify
            copy_headers X-Gatekeeper-User X-Gatekeeper-Role X-Gatekeeper-Group X-Gatekeeper-System-Admin
        }
        reverse_proxy cyclops-ui:8000
    }

    log {
        output file /var/log/caddy/cyclops.json
        format json
    }
}
```

(Schematic — actual Caddyfile requires more care with header forwarding, X-Forwarded-* setup, request_id propagation. Will refine in implementation.)

### Secrets

V1 secrets are minimal: Grafana admin password (only used for initial setup, since auth.proxy takes over). Stored in `.env` file, gitignored. Loki has no auth, no secret. Alloy has no secret.

### Production-scale note

At scale: Docker-compose becomes a Kubernetes/Nomad deployment, secrets via a vault, persistent volumes managed externally, multi-host networking. None for v1.

---

## 12. Self-audit and recursive logging

**The question.** What does Cyclops emit about itself, and what happens when its own infrastructure fails?

### What Cyclops-ui emits

- App lifecycle (`app_started`, `app_stopped`)
- Per-request events (auto from Flask middleware)
- Per-user-action events (`cyclops_ui.dashboard.viewed`, `cyclops_ui.app.selected`)
- Periodic heartbeat from the Flask process (every 60s, via in-process timer)

### What the agent emits about itself

Alloy has its own metrics endpoint and self-logging. Configure Alloy to emit its own logs to Loki (recursive but useful) tagged `source=cyclops`, `app=alloy`, `component=ingest`. If Loki is down, Alloy's local stdout is the fallback (visible via `docker logs alloy`).

### What Loki emits

Loki has its own logs (to stdout). Not ingested into itself in v1 (would create a recursion that's not interesting). Available via `docker logs loki` for triage.

### What Grafana emits

Same — `docker logs grafana`.

### Recursive failure scenarios

**Loki down:** Alloy buffers locally up to its `wal` size, then drops. Apps continue running unaffected (their stdout is captured by Docker regardless of Loki state). Recovery: restart Loki, Alloy resumes from tail position. Lost data = whatever was in flight when Loki went down + whatever exceeded WAL during the outage.

**Alloy down:** Apps' stdout still goes to Docker logs (and bare-metal script logs to file). On Alloy restart, it re-reads from its tail position file and catches up. Caveat: Docker log driver has its own buffering; long Alloy outages may exceed Docker's default log size and drop oldest. Mitigate by setting Docker log driver's `max-size` and `max-file` generously on critical containers.

**Grafana down:** No data loss. Logs continue to be ingested. Cyclops-ui's iframes show error state. Direct workaround: ssh + LogCLI (`logcli` is Loki's CLI client) for ad-hoc queries.

**Cyclops-ui down:** No effect on logging. Only affects the UI. Direct workaround: ssh + `docker logs <container>` for raw events; LogCLI for queries.

**Gatekeeper down:** Cyclops-ui (and Grafana behind it) become unreachable to browsers. Logging continues; observability is intact, just not viewable through the UI. Fallback: ssh + tail. The brief settled this.

The general principle: log capture, ingestion, and storage are independent of the UI layer. Any one component failing degrades a slice but doesn't lose data behind it.

### Self-monitoring

V1 self-monitoring is "human looks at dashboards." Phase 8 introduces a "Cyclops health" dashboard panel that watches:
- Last heartbeat from each cyclops-emitting app (cyclops-ui's own heartbeat = Cyclops-ui health)
- Loki ingestion rate (if it's zero, something's wrong)
- Alloy file-tail lag (Alloy exposes this as a metric)
- Disk usage on the Linode (out of scope for Loki itself; needs node-exporter or similar)

That last point — disk monitoring — is the genuinely missing piece in v1. Loki silently degrades when its disk is full. Mitigation: cron script (irony noted) that runs daily, checks `df`, emits a `cyclops.disk_check` event with usage percentages. That same script can run from the host's bare-metal cron, written in bash.

---

## 13. Versioning and release process

**The question.** How are the library and UI versioned, what compatibility guarantees does the wire format provide, and how do mixed-version fleets behave?

### Library versioning

Strict semver. Wire format is the public API surface alongside the function signatures.

- **Major bump (1.x → 2.x):** removes or renames a baseline field; changes a field's type; changes the meaning of an enum value; removes a typed helper.
- **Minor bump (0.3 → 0.4):** adds a new typed helper; adds a new optional field; adds a value to an enum. Old consumers must continue to ignore unknown fields gracefully (which they do — Loki + Grafana don't care).
- **Patch (0.3.0 → 0.3.1):** bug fixes, performance, internal refactors; no observable wire-format change.

While in 0.x, allow backward-incompatible changes more freely (semver convention). Move to 1.0 when the schema feels stable enough that we'd be embarrassed to break it. Honest target: 1.0 after phases 1–6 are done and we've used the data for a couple of months.

### Wire-format compatibility

Specifically for dashboards:
- Dashboards filter by `event_type`. New event_types are additive (don't break old dashboards). Renamed or removed event_types break dashboards.
- Dashboards parse JSON fields. New fields are additive. Removed/renamed fields break.

The contract: from minor version N to N+1, a dashboard built for N continues to work. From major N to N+1, dashboards may need updates (we'd document migration).

### Schema version probing

Every event has `cyclops_version` (from the brief). Dashboards can filter by version range if they need to: `cyclops_version=~"^0\\.3\\.(\d+)$"`. Recommend dashboards *don't* filter by version unless they have to — they should be schema-version-agnostic. If a dashboard breaks across versions, the right fix is usually to update the dashboard, not to filter to old data only.

### Mixed-version fleet during rolling upgrade

Realistic scenario: upgrade Vispay's library to v0.4.0 while Scout still runs v0.3.5 for a week. Both emit events. Loki ingests both. Dashboards see a mix.

This works as long as v0.4.0 is wire-compatible with v0.3.5 (i.e. minor bump, no removals). If v0.4.0 is a major bump (renamed something), dashboards can either:
- Be updated to handle both old and new (`field_v0_3 OR field_v0_4`), painful.
- Be flipped at the upgrade boundary, accepting a brief gap.

This pressure is a design forcing function to keep major bumps rare. For v1 we won't bump major in the first six months unless something is genuinely broken.

### Tag scheme

`lib-vX.Y.Z` and `ui-vX.Y.Z` as separate tag namespaces in the monorepo. CI parses the tag prefix to know what to release. This works fine in git.

PyPI: `cyclops` and `cyclops-ui` are independent packages, each releasing on their own tag.

### Production-scale note

At scale: a JSON Schema definition committed to the repo, validated in CI, published with each release. A schema registry. Wire-compat tests that take old schema + new schema and assert backward compatibility. None for v1.

---

## 14. Documentation deliverables

**The question.** What docs need to exist, and where?

### v1 doc set

Six documents, all in the monorepo:

1. **`packages/cyclops/README.md`** — library quickstart, install, the typed-helpers table, link to API reference.
2. **`packages/cyclops/API.md`** — full helper signatures, every kwarg, every default.
3. **`docs/integration-guide.md`** — "how to add cyclops to a new app." Step-by-step: install, env vars, Flask init, first event, verifying it shows up.
4. **`docs/conventions.md`** — naming standards. The dotted format for `event_type`. The `<app>.<domain>.<action>` pattern. The `<protocol>_<kind>_code` pattern. Outcome enum semantics. When to add a typed helper vs use `event()`.
5. **`docs/dashboards.md`** — LogQL patterns, label vs field cardinality, recommended query shapes, how to use the `app` variable, how dashboard JSON is provisioned.
6. **`docs/operations.md`** — deployment, Linode setup, troubleshooting (Loki disk fills, agent not picking up logs, dashboards showing no data, request_id missing).

Phase 8+ adds:
- `docs/migration-0.x-to-1.x.md` (when it happens)
- `docs/alerting.md` (when alerting ships)

### What goes where

- Library-internal stuff (helper signatures, redaction APIs) lives next to the library code.
- Cross-cutting stuff (conventions, integration, dashboards, operations) lives in `docs/` at the repo root.
- Cyclops-UI gets a smaller `packages/cyclops-ui/README.md` with its own quickstart.

### Audience framing

Each document opens with "this is for X" — e.g. operations.md is for the human running the staging Linode at 10pm when something is broken. Conventions.md is for someone adding a new event type. Integration.md is for someone adding cyclops to a new app for the first time. Different framings, different tones.

### Production-scale note

Hosted docs site (mkdocs, hugo, etc.) replacing flat markdown. Examples and recipes section. Video walkthroughs. None for v1.

---

## 15. What's deliberately deferred

For each deferred item: what would be needed to add it later.

### Alerting
**Add later by:** configuring Grafana unified alerting against existing Loki queries. Defines alert rules per-condition (e.g. error rate > X), notification channels (email, Slack, PagerDuty). No library changes needed; no app changes needed; no agent changes needed. Pure Grafana config + a config repo for alert rules.

### Multi-tenant Loki
**Add later by:** flipping `auth_enabled: true`, adding `X-Scope-OrgID` propagation in the agent and Grafana. Library and apps unaffected.

### S3 storage
**Add later by:** Loki schema change to point chunks at S3-compatible storage (Linode Object Storage). One-time migration of existing chunks. Library, agent, apps unaffected.

### Per-app authz in cyclops-ui
**Add later by:** consulting `X-Gatekeeper-Role` and `X-Gatekeeper-Group` in cyclops-ui route handlers; possibly adding a config that maps users to allowed apps. Cyclops-ui code changes only.

### Distributed tracing / OpenTelemetry
**Add later by:** library shim that reads/writes `traceparent` headers, populates `request_id` (or new `trace_id` field) from upstream traces. Helpers grow span concepts. Wire format adds optional fields (no break). Apps adopt tracing libraries alongside cyclops. Significant library work; medium app work; agent unchanged.

### Cross-environment federation
**Add later by:** adding a third Loki ("global") that scrapes from staging and prod Lokis via Loki's own remote-write, or using Grafana's multiple-datasource capability with one datasource per env. Topology change; no library impact.

### Schema browser / alert config UI / fleet status in cyclops-ui
**Add later by:** adding routes (`/events`, `/alerts`, `/fleet`) that the v1 design has reserved. API calls from cyclops-ui to Loki and Grafana. Cyclops-ui code changes only.

### Django/FastAPI integrations
**Add later by:** packages/cyclops-django, packages/cyclops-fastapi parallel to cyclops-flask. Same patterns (init_app analogue, middleware reads headers, populates context). The library's core (`cyclops.event`, context, helpers) is framework-agnostic by design.

### Metrics-shaped events / Prometheus
**Add later by:** library exposes a `cyclops.metrics` namespace that emits events tagged `metric=true` and the agent (or a new sidecar) translates these to Prometheus exposition. Or: skip the library and run a Prometheus server alongside, scraping app `/metrics` endpoints directly. The latter is more conventional. Library impact small either way.

### Frontend JS observability
**Add later by:** adding a JS SDK that POSTs events to a cyclops-ui endpoint (`/_ingest`), which writes to stdout in the same JSON shape. The agent picks up cyclops-ui's stdout already. Adds a route + a JS package. App impact: include the JS bundle.

### Log search UI in cyclops-ui
**Already covered by Grafana Explore** — the iframe approach gives users this for free. Adding a custom search UI is only worth it if Explore feels insufficient.

### Schema migration tooling
**Add later by:** offline batch tool that reads Loki, applies a transformation, writes back. Or accept that old data stays old-shape and dashboards-against-old-data is the exception (which it is — 30-day retention means schema transitions only matter for ~30 days).

### Confidence check: are we painting into corners?

The shape of cyclops's wire format (timestamped JSON with named fields, `cyclops_version` marker) doesn't preclude any of the deferred items. The shape of the agent (Alloy, file/socket inputs, label-based labelling) doesn't preclude them either. The shape of cyclops-ui (Flask app with route namespace reserved for futures) doesn't either.

The one place we *could* paint into a corner is workflow tracking — if we hardcode a workflow ontology in v1, OTel adoption later means double-work. The recommendation in §6 (keep workflow_id opaque, no helpers in v1) avoids this.

---

## 16. Phasing

Each phase produces something demonstrable and testable. 1–3 weekends each, mostly toward the lower end at this scale.

### Phase 1 — Library skeleton
**Deliverable:** `packages/cyclops/` with the typed helpers from §1 (Tier 1), context API from §2, forbidden-fields enforcement from §3, redaction helpers, JSON-to-stdout emission. Adopt in Vispay (replace narrative `logger.info` calls with structured `cyclops.event` calls in a few endpoints). Verify events appear in `docker logs vispay`.

**Done when:** Vispay emits well-formed cyclops events on every request and a few business events; calling a forbidden field raises; redaction helpers behave correctly; tests pass.

### Phase 2 — Local Loki + Grafana stack
**Deliverable:** `docker-compose.yml` running Loki + Grafana + Alloy on a dev machine. Alloy picks up Vispay's container stdout. Loki ingests. Grafana shows raw logs in Explore.

**Done when:** Querying `{app="vispay"}` in Grafana Explore shows recent events. `{app="vispay",source="cyclops"} | json | event_type="request.completed"` works.

### Phase 3 — First dashboards
**Deliverable:** Per-app and global dashboards (JSON, committed). Provisioned via Grafana's file-based provider. Validated against real Vispay traffic.

**Done when:** Dashboards render, panels show data, the `$app` variable filters correctly. Caddy access logs are also flowing and a Caddy panel exists.

### Phase 4 — Cyclops-UI v1
**Deliverable:** `packages/cyclops-ui/` Flask app with the v1 routes from §10. Iframes Grafana. Emits its own self-events.

**Done when:** Visiting `/`, picking Vispay, seeing the per-app dashboard work. `/about` shows correct versions. cyclops-ui's own events appear in Grafana.

### Phase 5 — Deploy to staging Linode
**Deliverable:** Full Cyclops stack deployed on the staging Linode via the docker-compose from §11. Behind Caddy + Gatekeeper. Reachable at `cyclops.staging.callendina.com`.

**Done when:** Authenticated browser session shows the staging dashboards with real Vispay-staging data. Caddy routing correct. Cyclops-ui logs visible in its own dashboard (recursive sanity check).

### Phase 6 — Adopt in remaining apps (in staging)
**Deliverable:** `cyclops` library integrated into Gatekeeper, Scout (web), Corkboard, bb_tool. The basketball-pipeline cron scripts use `cyclops.cron` context manager. Bare-metal log file path convention enforced (`/var/log/cyclops/<app>/<component>.jsonl`). Adoption order is roughly: Scout (web), then the basketball-pipeline cron scripts, then Gatekeeper, then Corkboard and bb_tool. Each app is its own mini-integration; treat them as independent sub-phases that can run in parallel where convenient.

**Done when:** Every app in the fleet emits cyclops events in staging. The global dashboard shows traffic from all apps. Cron-script absence detection query works for at least one expected-cadence script.

**Carve-out for early prod.** If, partway through Phase 6, one app (most likely Vispay, possibly Scout) is well-integrated and its operator value would benefit from prod observability before the others are ready, it's fine to bring Phase 7 forward for *that one app's environment* and leave the rest of the fleet still being integrated in staging. The constraint is per-environment topology, not per-app, so deploying the Cyclops stack to prod doesn't force every app to be Cyclops-emitting on day one — apps that haven't been integrated yet simply don't appear in prod dashboards until they are. Treat this as a judgement call, not a default.

### Phase 7 — Deploy to prod Linode
**Deliverable:** Same stack on prod. Same dashboards. Same Caddy/Gatekeeper integration. May happen with full fleet (the planned path) or with a partial fleet via the Phase 6 carve-out.

**Done when:** Production Cyclops is reachable, ingesting events from at least one prod app, and dashboards work end-to-end. "All prod apps emitting" becomes a follow-up checkpoint rather than a Phase 7 gate, in line with the carve-out.

### Phase 8 — Operations polish
**Deliverable:** Cyclops-health dashboard (heartbeats, ingestion rate, file-tail lag, disk usage). Disk-check cron script. Refined Caddy access log fields. Documented LogQL recipes for common questions. Loki retention enforcement verified.

**Done when:** When something is wrong with the fleet, Cyclops tells you within 30 seconds of looking. A quarterly disk-cleanup procedure is documented (or unnecessary because retention works).

### Phase 9+ — From the deferred list
Alerting, schema browser, etc. — pick whichever earns its place first based on what hurts most by then.

---

## Open questions for you (push back here)

These are the points where I most want disagreement:

1. **§1 — `job_*` helpers.** I dropped them. If you have a mental model where "job" is a real first-class thing distinct from cron and worker loops, push back; I may be over-collapsing.

2. **§5 — heartbeats are user-wired, not library-managed.** This is a design choice in service of "no background threads." If you'd rather have `cyclops.start_heartbeat(every=60)` that just works, I can be talked into it; the cost is the library owning a thread.

3. **§6 — workflow tracking is intentionally bare.** No `workflow_started/step/completed` helpers in v1. This is a "wait until we feel the pain" call. If you already feel the pain, build the helpers in phase 1.

4. **§9 — `auth.proxy` whitelist trusts the Cyclops Docker network.** This is the standard pattern but the security model relies on no other container being able to reach Grafana on `cyclops-net` directly. Worth sanity-checking against your Caddy / Docker network setup.

5. **§10 — Grafana exposed at `/grafana` rather than its own subdomain.** Subdomain (`grafana.cyclops.staging.callendina.com`) is cleaner but adds a cert and a Caddy block. Path-based is simpler but Grafana's `root_url` config has to match exactly. If you have a strong preference, say so before phase 4.

6. **§11 — Alloy on the same host as everything else, with Docker socket mounted RO.** At hobby scale this is fine; it's one additional read-anything-on-Docker capability to the agent. If you'd rather use a Docker socket proxy from day one (slightly more setup), say so.

7. **§13 — staying on 0.x for ~6 months.** This means freer breakage during initial use. If you'd rather lock to 1.0 sooner (forcing more careful schema choices early), the constraint is yours to set.

8. **§16 phasing — Phase 6 (fleet adoption) vs Phase 7 (prod deployment).** *Resolved:* fleet-first is the planned path, with a Phase 6 carve-out that lets a single well-integrated app (likely Vispay or Scout) drag Phase 7 forward in its own environment if its operator value warrants it before the rest of the fleet is ready. The constraint is per-environment topology, not per-app, so partial-fleet prod deployment is mechanically straightforward.

Anything else where my recommendation feels off, flag it now and we'll iterate before turning this into an implementation plan.
