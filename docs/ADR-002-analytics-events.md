# ADR-002 — Analytics event vocabulary as a frozen, separately-versioned contract

- **Status:** accepted
- **Date:** 2026-10-28
- **Deciders:** lead (architecture), product (`docs/METRICS.md`)
- **Supersedes:** nothing. **Amends:** `docs/CONTRACTS.md` §7 (new section, no wire-shape change).
- **Version:** `EVENTS_VERSION = 1.1.0` in `packages/shared/src/analytics.ts`

## Context

Product named five events (`upload_started`, `prettify_approved`, `attributes_edited`,
`export_clicked`, `subscription_started`) and then found it needed a sixth
(`paywall_impression`) plus a new payload field (`fields_touched`) that the first draft's
`edited_field_count` could not express. Two concrete problems:

1. **A metric that cannot be computed from the vocabulary.** "Manual fields edited per item"
   was meant to measure seller effort. `edited_field_count` only counts fields whose value
   changed, so a seller who reads `chest_cm`, decides it is correct and moves on contributes
   zero. Trusting the model and fighting the model look identical. The metric would have been
   reported as a real workload number while being a lower bound.
2. **Event names that overwrite each other's meaning.** The number 3 appears three times with
   three unrelated meanings (7-day activation, 60-minute burst proxy, free-plan allowance). Any
   dashboard that summed them would produce a confident, wrong number.

## Decision

- Freeze **six** event names. Store them as a `const` tuple plus a zod discriminated union in
  `packages/shared/src/analytics.ts`, so the compiler and the runtime both refuse an invented
  name.
- Add **`fields_touched`** alongside `edited_field_count`. Definition: a field counts when the
  seller focuses then blurs it, or explicitly confirms it; repeated visits to one field count
  once. `edited_field_count` stays as the *changed* count — both are emitted, and the ratio is
  the interesting signal.
- Version analytics **separately** from `CONTRACT_VERSION`. The mobile client ships on its own
  cadence; an event rename must not force an API version bump, and an API shape change must not
  invalidate a month of already-collected events.
- Name the three threes apart: `activation_items_exported_7d`, `session_export_burst_60m`,
  `free_tier_item_limit`. Enforcement authority for the billing limit stays
  `apps/api/settings.py`; clients read it from `GET /v1/usage`.
- Latency proxies ride on the events: `job_latency_ms` (server) and `prettify_latency_ms` /
  `attrs_latency_ms` / `export_latency_ms` (client). `attrs_latency_ms` is attached to
  `prettify_approved`, not to `attributes_edited`, because a seller who never edits emits no
  edit event and tool time would silently under-count.

## Consequences

- `packages/shared` now owns two contracts (wire schemas + events). The parity test keeps
  covering only the wire half; the event half is covered by
  `packages/shared/test/analytics.test.ts`.
- Adding a seventh event requires an ADR + `EVENTS_VERSION` bump. That friction is the point:
  a renamed event breaks attribution retroactively, and no amount of later analysis repairs it.
- Event payloads are `.strict()`. An unknown key fails validation in development instead of
  shipping one `email` field to a dashboard.

## Rejected alternatives

| alternative | why not |
|---|---|
| Keep `edited_field_count` only, report it as a lower bound | honest but useless for the decision it was meant to drive (whether the model is good enough to trust). Product would keep guessing. |
| Fold events into `CONTRACT_VERSION` | couples two release cadences; every mobile analytics tweak becomes an API version bump |
| Free-text `metadata: Record<string, unknown>` on every event | this is exactly how PII ends up in an event store. A closed schema is cheaper than a data-deletion request |
| Reuse `export_clicked` for paywall impressions | conflates "wanted to export" with "was shown a wall", which is the one comparison the paywall A/B needs to stay clean |

**Cost accepted:** a schema change for every new event field, and mobile must ship the typed
module before the screens that use it. In exchange, six months from now `subscription_started`
is still attributable to a variant.
