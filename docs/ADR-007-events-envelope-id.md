# ADR-007 — `event_id` on the analytics envelope

- **Status:** accepted
- **Date:** 2026-11-04
- **Deciders:** lead (architecture), backend (E5-T6), mobile consumer (E6-T1)
- **Supersedes:** nothing. **Amends:** `docs/CONTRACTS.md` §7 and `packages/shared/src/analytics.ts`.
- **Version:** `EVENTS_VERSION` 1.2.0 → **1.3.0**. `CONTRACT_VERSION` stays 1.0.0.

## Context

`docs/PLAN.md` E5-T6 says: *"the same event id replayed is idempotent"*. Implementing that route
made the gap obvious: the frozen `Envelope` in `packages/shared/src/analytics.ts` has exactly six
fields (`event`, `client_ts`, `session_id`, `subject`, `app_version`, `platform`) and is
`.strict()`. There is no id in it.

Two ways to satisfy the DoD, and they are not equivalent:

1. **Derive the id server-side** from the payload (a uuid5 over subject + session + event +
   `client_ts` + payload digest). No contract change. But two genuinely distinct events that
   happen to share every one of those fields — two `paywall_impression` rows for variant `a`,
   trigger `upload_4th`, in the same second, from the same session — collapse into one, and the
   second is silently dropped. A funnel that quietly deletes impressions is worse than a funnel
   that is missing a field.
2. **Have the client generate the id once per event** and reuse it only when retrying that same
   event. Requires adding a field to a frozen contract.

There was a second, sharper symptom: with `.strict()`, a client that validated its body with
`AnalyticsEvent.parse(...)` could not produce a body the API would accept. The API required
`event_id`; the schema forbade it. Whichever side you think is authoritative, they could not both
be right, and the DoD says the id is the client's.

## Decision

- Add **`event_id: z.string().uuid()`** to `Envelope`, first field after `event`.
- Bump **`EVENTS_VERSION` to `1.3.0`**. No event was added and no payload field changed; the
  version exists so a client can tell whether its build emits an id.
- The server treats `event_id` as the primary key of `public.events`:
  same id + same payload digest ⇒ `202 {duplicate: true}` (a retry);
  same id + different digest ⇒ `409` (a reused id, which is a client bug and must be loud).
- The id is **not** derived from the payload, and the server does not invent one when it is
  missing: a body without `event_id` is a 422 like any other missing required field.

## Consequences

- **Client (E6-T1) must generate a uuid per event** at emit time and persist it until the request
  succeeds, then reuse it verbatim on retry. The digest normalises timestamps (`Z` and `.000Z`
  hash the same — measured), so the failure mode is not the clock: it is anything whose
  *order or spelling* survives normalisation, e.g. re-ordering `field_names` from
  `["colors","brand"]` to `["brand","colors"]` under the same id returns 409. Send the original
  bytes, not a re-serialisation of the same event.
- **`packages/shared/test/analytics.test.ts`** now pins both the version and the presence of the
  field, so a rollback of either fails the node suite.
- **`apps/api/tests/test_events.py`** compares the Python envelope to the TypeScript one with an
  empty additive-field set — the two are now supposed to be identical, not "identical plus one".
- **`CONTRACT_VERSION` does not move.** The events vocabulary is deliberately outside the API
  contract (`CONTRACTS.md` §7): mobile ships on its own schedule and an event field must not force
  a rest-of-the-API version bump.
- **Migration:** none. `public.events.event_id` is already the primary key
  (`infra/migrations/0002_events.sql`).
- **Cost of being wrong:** if a real client cannot generate a stable id (e.g. a platform where
  emit and retry run in different processes with no local storage), the fallback is a
  client-side outbox — not a server-derived id. That is a mobile ticket, not an API change.

## Alternatives rejected

- **Server-derived uuid5** — see Context (1): silently merges same-second identical events.
- **Dedupe on (subject, event, client_ts) with a time window** — makes a legitimate second event
  in the same second disappear, and turns a clock skew into either a duplicate or a double count.
- **Accept an optional id, dedupe only when present** — two ingestion paths with different
  guarantees, and the guarantee you get depends on the client build. The DoD has one guarantee.
