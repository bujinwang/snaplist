# ADR-004 — On-model rendering is asynchronous; `AssetPack` never blocks on it

- **Status:** accepted
- **Date:** 2026-10-28
- **Deciders:** lead (architecture), product (PRD US-4), mobile
- **Amends:** `docs/CONTRACTS.md` §2 (documentation only — **no shape change**, `CONTRACT_VERSION`
  stays `1.0.0`)

## Context

Product's PRD US-4 says the seller gets "at least one" on-model image. That reads like a
synchronous guarantee, but a FASHN render is seconds-to-tens-of-seconds and costs up to $0.10.
A blocking `GET /v1/items/{id}` would mean:

- the review screen (prettify + attributes, ready in ~20 s) waits for a render the seller may
  not even want;
- a render failure turns an otherwise complete item into an error, which is the worst trade in
  the product: we would be hiding assets we already paid for;
- the API holds a request open for the duration of a GPU call — a timeout waiting to happen.

## Decision

`GET /v1/items/{id}` **never** waits for a render and never 5xxs because of one.

- `on_model_urls` is `[]` until a completed render exists. It is `[]` for "not requested" and
  `[]` for "rendering"; the current state is on the `Job` (`kind=vton`), which the client already
  polls.
- Invariant: `job(kind=vton, state=done) ⇒ on_model_urls.length >= 1`.
  A `failed` render (`vton_rejected`) leaves `on_model_urls` untouched and refunds the credit.
- The review screen is usable the moment `prettified_url` and `attributes` are non-null. The
  on-model slot renders as one of three states — *not requested* / *rendering* / *ready* — and
  is a separate Pro-gated action.

**Product action required:** PRD US-4 must say "at least one on-model image **after a completed
render**", not on first fetch. Product was told; this ADR is the record.

## Consequences

- Mobile needs a per-item `vton` job poll in addition to the main job poll. One extra request
  per rendered item, only for Pro users who ask for a render.
- `AssetPack` stays a five-key object; nothing to migrate.
- The "everything is on screen" latency proxy only covers prettify + attrs, and export latency is
  measured separately — a render is never part of tool time. That is correct: rendering is an
  optional upsell step, not part of listing an item.

## Rejected alternatives

| alternative | why not |
|---|---|
| Block `GET /items/{id}` until the render finishes | a 30 s request, and one provider hiccup turns a finished item into a failure |
| Always render eagerly on item creation | 10× the GPU spend for a feature most Starter users cannot use (Pro-only), and it would blow the $0.03/item ceiling on its own |
| Add `on_model_state` to `AssetPack` | a shape change to carry information the `Job` already carries. One source of truth beats two |
| Return 404/409 while rendering | the item exists and its prettified image is ready; a 409 would make clients throw away good data |
