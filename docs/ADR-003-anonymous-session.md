# ADR-003 — Anonymous session first, account merged on first export

- **Status:** accepted
- **Date:** 2026-10-28
- **Deciders:** lead (architecture), product (`docs/PRD.md` §0, `docs/METRICS.md`), informed by BRIEF's
  "cut listing time" goal
- **Affects:** E5-T5 (auth), E5-T1 (schema), E6-T1 (mobile shell), E8 (paywall), `docs/CONTRACTS.md` §7

## Context

The seller's first 90 seconds decide whether this product exists. A signup wall at second four
would (a) lose a meaningful share of trials, and (b) — worse for us — destroy the ability to
tell "the product is not good enough" from "the login wall is too early", because every
post-signup drop-off would be confounded.

Against that: uploads cost real money (segmentation + VLM), and an anonymous user is an
unlimited supply of free quota unless something bounds it.

## Decision

**Anonymous by default, identified at the moment of value.**

1. On first launch the app calls Supabase `signInAnonymously()`. It gets a real JWT with
   `is_anonymous: true`. Items, jobs and quota all belong to that `auth.users` row from the
   very first photo — no parallel "guest" storage path, no migration of local-only rows.
2. The free allowance (3 items/month) is attached to that anonymous user. It is enforced
   exactly like any other plan — same 402, same ledger.
3. At **first export** — the moment the seller tries to take value out of the app — we require
   an identity (email magic link, or Apple/Google). Supabase `updateUser` converts the anonymous
   user in place; the `user_id` does not change, so items, assets, jobs, quota consumption and
   the analytics `session_id` all survive. **No row copying, no merge job, no data loss path.**
4. If instead the anonymous user signs into an *existing* account, that is a genuine merge and
   is handled explicitly: items are re-pointed, the free-tier usage of the anonymous row is
   *not* credited back, and the attempt is logged. Merges are rare (returning seller who
   reinstalled) and must never happen silently.

## Consequences

- `profiles` needs `is_anonymous boolean`, `merged_into uuid null`, `paywall_variant text`
  (see ADR-005). Analytics `subject` is `anon_<uuid>` before upgrade and `usr_<uuid>` after —
  the mapping table is the auth user id, which is why we do not generate a separate client id.
- Abuse: an anonymous install can farm 3 items per reinstall. Mitigations, in order of cost:
  device-keyed rate limit at `POST /v1/items`, no on-model render for anonymous users, and
  requiring identity at export (which is where the value is). We accept the residual risk in
  v1 — 3 items of GPU time per reinstall is cheaper than losing the honest trial.
- The paywall can be reached while anonymous (4th upload). That is intended: the conversion
  event must be attributable to a variant, and asking for a card before asking for an email
  would be worse.

## Rejected alternatives

| alternative | why not |
|---|---|
| Require signup before first upload | simplest to build, and it is the one choice that makes the whole funnel unanalysable. Product loses the ability to attribute any drop-off |
| Local-only guest data, "claim your items" at signup | a second storage path, a merge on every signup, and photos that vanish when the app is reinstalled before signup |
| Skip auth entirely for v1, one shared quota per device | no row-level security story, no way to bill, and `usage_events` loses its owner |
| Anonymous forever, no identity ever | cannot bill, cannot recover a lost device, cannot export to a marketplace account |

**Cost accepted:** one `auth.users` row per fresh install, and a merge path that must be
written and tested even though it fires rarely. Cheaper than a funnel nobody can read.
