# SnapList — CONTRACTS.md

**Status: v1.0.0 — frozen.** This file is the single source of truth named in BRIEF.md.
Two implementations must always agree with it, field for field:

| side | file | enforced by |
|---|---|---|
| python (normative source) | `apps/api/schemas.py` | `apps/api/tests/test_contracts.py` |
| TypeScript / zod mirror | `packages/shared/src/schemas.ts` | `packages/shared/test/schemas.test.ts` |
| cross-language parity | — | `apps/api/tests/test_contract_parity.py` (reads the compiled zod artifact through `node`) |

## The rule

> **Nobody changes a contract without an ADR in `docs/` and a version bump.**

Procedure, non-negotiable:

1. Write `docs/ADR-00N-<slug>.md`: what changes, why, what breaks, migration for existing rows
   and existing mobile clients.
2. Bump `CONTRACT_VERSION` in **both** `apps/api/schemas.py` and
   `packages/shared/src/schemas.ts` (they are asserted equal in CI).
3. Update this file in the same commit, plus the marketplace mapping tables if affected.
4. `make test` must pass — the parity test fails on any name, nullability or enum-order drift.
5. Adding a field is a **minor** bump. Renaming, removing, retyping or re-ordering an enum is a
   **major** bump and needs a client-compat note in the ADR.

Rules that prevent 90% of drift, applied in both languages:

| python | zod | note |
|---|---|---|
| `X | None = None` | `.nullable().optional()` | absent and `null` are both legal |
| field with a default | `.default(<same value>)` | defaults are duplicated deliberately |
| `list[T]` | `z.array(T)` | |
| `dict[str, float]` | `z.record(z.string(), z.number())` | |
| `StrEnum` | `z.enum([...])` | **same literals, same order** |
| `datetime` | `z.string().datetime()` | ISO 8601, UTC, `Z` suffix |
| `uuid.UUID` | `z.string().uuid()` | lowercase string on the wire |
| `extra="forbid"` | `.strict()` | unknown keys are an error on both sides |

No camelCase aliases anywhere. snake_case on the wire in both languages.

---

## 1. `ItemAttributes`

Emitted by `packages/vision/attrs.py`, edited by the mobile attributes screen, consumed by the
copy generator and every marketplace mapper.

| field | python type | zod | required | meaning / policy |
|---|---|---|---|---|
| `category` | `GarmentCategory` | `z.enum(GARMENT_CATEGORIES)` | **yes** | fixed vocabulary, see below |
| `subcategory` | `str \| None` | `.string().nullable().optional()` | no | free text, e.g. `"crewneck tee"`, `"straight leg jean"` |
| `colors` | `list[str]` (1–3) | `z.array(z.string()).min(1).max(3)` | **yes** | lowercase, primary first |
| `pattern` | `Pattern` | `z.enum(PATTERNS)` | no (default `unknown`) | |
| `material` | `list[str]` | `z.array(z.string())` | no (`[]`) | lowercase fibres, **no percentages** |
| `fit` | `Fit` | `z.enum(FITS)` | no (default `unknown`) | |
| `era` | `str \| None` | `.nullable().optional()` | no | `"90s"`, `"y2k"`, `"modern"` — free text |
| `brand` | `str \| None` | `.nullable().optional()` | no | **only when the mark is legible in the photo** |
| `size_label` | `str \| None` | `.nullable().optional()` | no | exactly as printed: `"M"`, `"W30 L32"`, `"EU 39"` |
| `condition` | `Condition` | `z.enum(CONDITIONS)` | no (default `good`) | |
| `defects` | `list[Defect]` | `z.array(z.enum(DEFECTS))` | no (`[]`) | |
| `measurements` | `Measurements \| None` | `Measurements.nullable().optional()` | no | absent is unknown; never `0` |
| `keywords` | `list[str]` (≤10) | `z.array(z.string()).max(10)` | no (`[]`) | search keywords for the listing |
| `confidence` | `dict[str, float]` | `z.record(z.string(), z.number())` | no (`{}`) | field name → 0.0–1.0 |
| `needs_review` | `list[str]` | `z.array(z.string())` | no (`[]`) | field names below the review threshold |
| `model_version` | `str` | `.string()` | no (`"unset"`) | adapter + model id that produced this |

### `Measurements` (nested)

| field | type | meaning |
|---|---|---|
| `chest_cm` | `float \| None` | flat measurement, centimetres |
| `length_cm` | `float \| None` | |
| `waist_cm` | `float \| None` | |
| `rise_cm` | `float \| None` | |
| `inseam_cm` | `float \| None` | |
| `sleeve_cm` | `float \| None` | |
| `source` | `MeasurementSource` (default `estimated`) | `estimated` \| `seller_provided` |

**Attribution policy (a correctness rule, not a style rule).** `brand` may only be populated when
the mark is legible in the photo. `material` may only contain fibres with visual or care-label
evidence. The copy generator must never state a brand or fibre that is absent here — E7-T3 ships
a test that fails if it does. Everything else is a judgement call and is allowed to be wrong;
that is what `confidence` and `needs_review` exist for.

### Enumerations (order is part of the contract)

```
GarmentCategory  top | bottom | dress | outerwear | shoe | accessory | other
Pattern          solid | striped | plaid | floral | graphic | animal | geometric | tie_dye | knit | other | unknown
Fit              slim | regular | relaxed | oversized | cropped | high_waisted | unknown
Condition        new_with_tags | new_without_tags | excellent | good | fair | poor
Defect           stain | hole | tear | pilling | fading | missing_button | broken_zipper | loose_seam | alteration | other
MeasurementSource estimated | seller_provided
```

---

## 2. `AssetPack` — `GET /v1/items/{id}`

Exactly the five keys promised in BRIEF.md. Clients cache this shape on disk, so it does not
grow without an ADR.

| field | type | notes |
|---|---|---|
| `item_id` | `UUID` / `string (uuid)` | |
| `prettified_url` | `str \| None` | signed Supabase Storage URL; `null` until E2 finishes |
| `cutout_url` | `str \| None` | transparent PNG of the masked garment |
| `on_model_urls` | `list[str]` | in render order; empty unless an on-model job ran |
| `attributes` | `ItemAttributes \| None` | `null` until E3 finishes |

```json
{
  "item_id": "9f1b0c62-5f2f-4a59-9a1d-0c9a1f66b0aa",
  "prettified_url": "https://<project>.supabase.co/storage/v1/object/sign/items/.../prettified.png",
  "cutout_url": "https://<project>.supabase.co/storage/v1/object/sign/items/.../cutout.png",
  "on_model_urls": [],
  "attributes": {
    "category": "top",
    "subcategory": "crewneck tee",
    "colors": ["black"],
    "pattern": "solid",
    "material": ["cotton"],
    "fit": "regular",
    "era": null,
    "brand": null,
    "size_label": "M",
    "condition": "good",
    "defects": [],
    "measurements": null,
    "keywords": ["black tee", "crewneck", "cotton"],
    "confidence": {"category": 0.97, "colors": 0.99, "material": 0.71},
    "needs_review": ["material"],
    "model_version": "vlm:<provider>:<model-id>"
  }
}
```

"Not ready yet" is expressed as `null` / `[]` inside a **200**, never as a 404 and never as a
field that disappears. A job's progress lives on `Job`, not on `AssetPack`.

**`on_model_urls` is asynchronous (ADR-004).** A first `GET /v1/items/{id}` after the
prettify/attributes job finishes returns `on_model_urls: []` if no on-model render has been
requested, and `[]` again if one is still running. On-model is a separate job kind (`vton`);
clients must show the render slot as "not requested" / "rendering" / "ready" by reading the
`Job`, and must never block the review screen waiting for a render. The invariant is:
`job(kind=vton, state=done) ⇒ on_model_urls.length >= 1`, and a `failed` render leaves the list
untouched. Consequence for PRD US-4: "at least one on-model image" holds only *after* a
completed render, not on first fetch — product has been told.

---

## 3. `Job` state machine

```
queued ──claim──▶ running ──ok──▶ done
                     │
                     ├─retryable (attempts < 3, backoff)──▶ queued
                     └─terminal──▶ failed(reason_code)
```

| from | to | trigger | who | side effects |
|---|---|---|---|---|
| — | `queued` | `POST /v1/items`, `/rerender` | api | quota decremented atomically; `attempts=0` |
| `queued` | `running` | worker claims with `SELECT … FOR UPDATE SKIP LOCKED` | worker (Modal) | `started_at`, heartbeat begins |
| `running` | `done` | all stages succeeded, assets written | worker | `finished_at`, `progress=1.0`, quota stays spent |
| `running` | `queued` | retryable error **and** `attempts < 3` | worker | `attempts += 1`, exponential backoff |
| `running` | `failed` | terminal error, or `attempts == 3` | worker | `reason_code` set, `finished_at`, **quota refunded** |
| `running` | `queued` | heartbeat lost (worker died) | sweeper | treated as a retryable failure, `attempts += 1` |
| `done`/`failed` | — | — | — | terminal; a re-render creates a **new** job row |

Invariants, all testable:

1. `state=done ⇒ reason_code is null`.
2. `state=failed ⇒ reason_code is not null` (one of the five below).
3. `attempts <= 3`, and `attempts == 3 ⇒ state ∈ {done, failed}`.
4. A job occupies exactly one quota unit from `queued` until it reaches a terminal state; a
   `failed` job refunds exactly one unit (idempotent by `job_id`).
5. `created_at` ≤ `started_at` ≤ `finished_at` whenever the later ones are non-null.
6. Only one worker owns a job at a time — enforced by the row lock, not by trust.

Job kinds: `full` (segment→prettify→attrs) · `prettify` · `attributes` · `vton` · `export`.

---

## 4. `reason_code` enum — five values, closed set

| code | who raises it | meaning | retryable | quota |
|---|---|---|---|---|
| `bad_photo` | ingest / segment | unusable photo: blur, cut-off garment, no light | no | refunded |
| `no_garment` | segment (`E1-T2`) | no garment found in frame | no | refunded |
| `low_confidence` | attrs (`E3-T2`) | VLM output failed schema after one repair retry, or all fields below floor | no | refunded |
| `vton_rejected` | vton gate (`E4-T3`) | provider or quality gate refused this garment/model pair | no | refunded |
| `internal` | anything else | our bug. Never used to hide a provider problem | maybe | refunded |

`internal` is the only code that should page someone. A provider outage must map to
`vton_rejected` / `low_confidence` with the provider code in `error_detail`, not to `internal` —
otherwise a third-party incident looks like our defect and gets triaged as one.

Every non-2xx response uses one envelope:

```json
{"error": {"code": "quota_exceeded", "message": "Free plan allows 3 items per month.",
           "retry_after_seconds": null, "details": {"plan": "free", "limit": "3"}}}
```

`ErrorCode`: `unauthorized` · `forbidden` · `not_found` · `invalid_request` · `quota_exceeded` ·
`not_implemented` · `internal`. The 402 quota case must carry `code="quota_exceeded"` — mobile
keys its upsell sheet off that string.

Not to be confused with the export refusal `reason` — see "Export refusals are not job failures"
(§5).

---

## 5. Marketplace field mappings

Source of truth for the mappers in `packages/shared/src/marketplaces/` (E7). **The exact
machine-readable headers are owned by E7-T1/T2 and must be verified against the marketplaces'
own current templates; anything not yet verified is marked `UNVERIFIED` and must not be pasted
into a seller-facing doc as fact.**

### eBay (Inventory / Trading CSV)

| eBay concept | our source | mapping rule |
|---|---|---|
| `Title` | `copy.ts` title | ≤ 80 chars, keyword-front-loaded, no ALL CAPS. Truncate at a word boundary. |
| `Description` | `copy.ts` description | template with a measurements block + condition notes; no brand/fibre claims absent from `ItemAttributes` |
| `Category` (category id) | `ItemAttributes.category` + `subcategory` | table lookup; unmapped ⇒ refuse to export that row with `insufficient_data` (never guess a category id) |
| Item specifics — `Type` | `category`/`subcategory` | our vocabulary → eBay aspect values |
| Item specifics — `Colour` | `colors[0]` | title-case for display |
| Item specifics — `Pattern` | `pattern` | only when ≠ `unknown` |
| Item specifics — `Material` | `material` | comma-joined; omit entirely when empty |
| Item specifics — `Size` | `size_label` | verbatim, never normalized |
| Item specifics — `Condition` | `condition` | eBay's own enum, **not** ours — explicit table |
| Item specifics — `Brand` | `brand` | **only if non-null**, else omit the aspect |
| `Quantity` | fixed `1` | resale, one of each |
| Shipping / returns | not from `ItemAttributes` | seller defaults set in the app; never invented |

Required aspects vary per category on eBay. The mapper ships a per-category required-aspect
table and a test that fails when a required aspect cannot be satisfied — worse to export a
broken CSV than to refuse one row. `UNVERIFIED`: exact header spelling and the full
required-aspect list per category (E7-T1 verifies against the live template).

### Poshmark

| Poshmark field | our source | mapping rule |
|---|---|---|
| Title | `copy.ts` | Poshmark allows a longer title than eBay; still no ALL CAPS |
| Description | `copy.ts` | same template as eBay, minus item specifics |
| Category / Subcategory | `category` / `subcategory` | Poshmark's own two-level tree; unmapped ⇒ refuse row |
| Brand | `brand` | Poshmark *requires* brand — when `brand` is null we must ask the seller, not invent |
| Size | `size_label` | Poshmark's size vocabulary differs from eBay's; explicit table |
| Color | `colors` | Poshmark allows multiple colours — join |
| Condition | `condition` | Poshmark has 4 conditions; our 6 collapse, and the collapse table is documented |
| Original price / price | *not ours* | seller sets it; comps (`E7-T4`) may suggest, never auto-fill |
| Photos | `prettified_url`, `on_model_urls` | order matters: prettified first |

`UNVERIFIED`: Poshmark has no official bulk CSV upload — E7-T2 must confirm the real path
(the current assumption is a copy/paste-assisted flow, see `docs/EXPORT.md`).

### Vinted

| Vinted field | our source | mapping rule |
|---|---|---|
| Title | `copy.ts` | shorter limit than eBay; truncate on a word boundary |
| Description | `copy.ts` | measurements block required by Vinted's own guidance |
| Category | `category`/`subcategory` | Vinted's tree; unmapped ⇒ refuse row |
| Brand | `brand` | Vinted requires it; same rule as Poshmark |
| Size | `size_label` | verbatim |
| Condition | `condition` | Vinted's 5-value enum; explicit table |
| Colour | `colors[0]` | single value on Vinted — primary only, and the rest go into the description |
| Material | `material` | Vinted has a controlled list; map only known fibres, drop the rest |
| Package size | *not ours* | seller default |

`UNVERIFIED`: Vinted's bulk-upload availability per country; E7-T2 verifies before E7-T6 writes
the seller-facing steps.

**Refusal beats guessing.** Every mapper returns either a row or a machine-readable
`insufficient_data` reason naming the fields that blocked it. A listing the seller has to fix
is recoverable; a wrong category id silently costs them the sale.

### Export refusals are not job failures

`POST /v1/exports` returns `refused[]` entries shaped `{item_id, reason, fields[]}`.

`reason` is **not** `Job.reason_code` and the two enums must never be merged. They answer
different questions and live in different layers:

| | `Job.reason_code` | export `reason` |
|---|---|---|
| subject | one item's render attempt | one item's eligibility for a batch file |
| raised by | the CV pipeline | the marketplace mapper |
| closed set | five frozen values (see §4) | open per marketplace (e.g. `insufficient_data`) |
| retryable | yes, with backoff | no — the seller fixes a field, then re-exports |

Merging them would force every marketplace's refusal reason into a five-value enum designed
around GPU failures, and would make `insufficient_data` look like something the pipeline can
retry. If a value ever needs to exist in both places, it needs two names and an ADR, not one
shared name.

`fields[]` names the attribute keys the seller must supply (`["brand"]`), so the client can
point at a control instead of printing prose. It is empty when the refusal is not field-shaped
(e.g. a whole-batch refusal).

---

## 6. Failure copy — `reason_code` in a human voice

A `reason_code` is a contract value (logs, retries, refunds). It is **not** seller-facing copy.
The mobile app must never render the enum. This table is the single source for the strings, so
two screens cannot describe the same failure differently.

| `reason_code` | title (≤ 40 chars) | body | primary action |
|---|---|---|---|
| `bad_photo` | This photo is hard to use | Too dark, blurry, or the garment is cut off at the edge. | Retake |
| `no_garment` | No garment found | We could not find a piece of clothing in this photo. Lay it flat and fill the frame. | Retake |
| `low_confidence` | We are not sure about this item | Some details did not come through clearly. Check the labels before you export. | Review attributes |
| `vton_rejected` | This one cannot go on a model | The garment or the chosen model did not work together. Your credit was not used. | Pick another model |
| `internal` | Something broke on our side | This is our fault, not yours. Your credit was not used and we have been notified. | Try again |

Rules: never blame the seller for an `internal` failure; always say whether a credit was used
(it was not — terminal failures refund); never show a stack trace or a provider name.

---

## 7. Analytics event vocabulary — frozen, own version (`EVENTS_VERSION = 1.4.0`)

Typed source of truth: `packages/shared/src/analytics.ts`. This is deliberately **not** under
`CONTRACT_VERSION`: the mobile client ships on its own schedule and an event rename must not
force an API version bump. It is still frozen, and still requires an ADR to change.

**Seven events.** Adding an eighth is a version bump here, not an improvisation in a screen.
Current: `EVENTS_VERSION = 1.4.0` (`1.1.0` = six events; `1.2.0` adds `export_delivered`, H below;
`1.3.0` adds `event_id` to the envelope — a field, not an event, ADR-007; `1.4.0` adds `item_id`
to the envelope, also a field, graded per event, ADR-008).

**Owner of event *definitions* and their thresholds:** `docs/METRICS.md` (product). This section
owns the *shape* and the frozen names; product owns what the numbers have to be. The five funnel
events product specified are `upload_started`, `prettify_approved`, `attributes_edited`,
`export_clicked`, `subscription_started`; `paywall_impression` is the sixth, added at product's
request so a paywall impression is never smuggled into `export_clicked` (conflating "wanted to
export" with "was shown a wall" destroys the only clean A/B comparison we have);
`export_delivered` is the seventh (see below).

**Retries must not be collapsed at the emitter.** Emit `prettify_approved` for **every** decision,
including each `retry` — do not send only the final one. The emitter cannot know which attempt will
be the last, and a client that only reports the final outcome destroys the retry rate (how often the
pipeline gets it wrong on the first try) which is a quality signal we cannot recover later.
Deduplication is the *reader's* job: reports take one row per `item_id`, using the last
`decision == "approve"`, so a flaky item is not over-weighted in the percentiles. Distinguish
first-pass from after-retry in the report by counting the item's preceding `retry` events.

**Attempt is not success — `export_clicked` vs `export_delivered`.** Do not "fix" this by changing
what `export_clicked` means. A click that fails to produce a file is *the most interesting row in
the funnel*; collapsing the two would delete the evidence we want and quietly inflate activation.

- `export_clicked` = the seller **asked** for a file (attempt). Unchanged.
- `export_delivered` = the seller **actually got** the file (client finished writing/downloading).
- **`activation_items_exported_7d` counts `export_delivered`, never `export_clicked`.** Counting
  attempts makes activation an upper bound: a timeout or a disk-write failure would register as an
  activation. This binding is part of the contract, not a dashboard choice.
- The gap (`export_clicked` present, no matching `export_delivered`) is a **required observable** —
  it is the export-failure rate. Report it; never drop the unmatched clicks.

| # | event | fires when | required payload beyond the envelope | `item_id` |
|---|---|---|---|---|
| 1 | `upload_started` | shutter fires, or a library photo is picked | `source: camera\|library`, `capture_mode: single\|batch`, `batch_index?` | **required** |
| 2 | `prettify_approved` | seller accepts / retries / asks for manual bg removal — **one event per decision, so a retried item has several; only the final `approve` is the item's verdict** | `decision`, `job_latency_ms` (server), `prettify_latency_ms` (client), `attrs_latency_ms` (client) | **required** |
| 3 | `attributes_edited` | **the attribute sheet is touched — emitted when it is dismissed (leaving the screen or losing focus), one event per item per visit, `fields_touched` may be `0`** | `edited_field_count`, **`fields_touched`**, `field_names[]` | **required** |
| 4 | `export_clicked` | seller asks for a marketplace file | `marketplace`, `row_count`, `export_latency_ms` | **required** |
| 5 | `subscription_started` | Stripe reports the subscription became active | `plan`, `paywall_variant`, `amount_cents`, `currency` | **forbidden** |
| 6 | `paywall_impression` | the paywall is shown, converted or not | `paywall_variant`, `trigger: upload_4th\|on_model\|export_limit`, `plan_offered` | optional |
| 7 | `export_delivered` | the seller **has** the marketplace file (write/download complete) | `marketplace`, `row_count`, `bytes`, `delivery_latency_ms` | **required** |

**`item_id` — required, optional, forbidden (`1.4.0`, ADR-008).** It is the envelope field every
per-item report joins on, and it is **not** uniformly optional:

- **required** on the five per-item events. Without it `tool_time_ms` per item (PLAN's p50 ≤ 35 s
  gate), `activation_items_exported_7d` (deduped to one row per `item_id`) and the export-failure
  rate — a *required observable* in this section — are three numbers that cannot be computed, and
  the reports that would print them degrade into counts of events, which look similar and mean
  something else. A body missing it is a **422**.
- **optional** on `paywall_impression`. `trigger=on_model` and `trigger=export_limit` always have
  an item; a paywall opened from the settings screen has none, and no paywall metric segments by
  item. Requiring it would refuse a legal impression and push an emitter to invent an id.
- **forbidden** on `subscription_started`. It is written by the API from a Stripe webhook, where
  no item is in scope; a plausible id here would attribute revenue to whichever item happened to
  be on screen. A body carrying one is a **422**, and the table's reverse CHECK refuses it again
  for writes that bypass the route.

**`item_id` must belong to the caller.** `POST /v1/events` looks it up with the authenticated
uuid and answers **404** — not 403 — for an item that is not the caller's, the same answer
`GET /v1/items/{id}` gives and for the same reason: confirming that a foreign id exists is the
leak. Soft-deleted items are accepted (`include_deleted=True`): events are append-only history,
and an item deleted after the event happened still owns it.

**`fields_touched` vs `edited_field_count`.** `edited_field_count` counts fields whose value
changed. `fields_touched` counts fields the seller *interacted with*, including reading and
confirming without changing (focus→blur, or an explicit confirm; one field visited three times
counts once). Workload measured with `edited_field_count` alone is a **lower bound** and
systematically makes "the seller trusted the model" look like "there was no friction".

**Why the trigger is "sheet touched", not "attribute edited" — do not narrow it.** The entire
point of `fields_touched` is to observe the seller who *reads and does not change anything*. If the
event only fires on an edit, then every emitted event has `fields_touched >= 1` by construction,
the `0` bucket is empty forever, and blind acceptance — "seller opened the panel, believed the
model, confirmed" — becomes unobservable. The metric would then report that nobody is blind-
accepting, when in fact nobody was looking. Emitting on dismissal, with `0` allowed, is what makes
the blind-accept rate (`fields_touched = 0` ÷ items with this event) computable at all. A reviewer
who "optimises" this to fire only on change silently deletes a product metric.

**PII ban (hard).** No free text, no attribute values, no photo content, no email, no device
advertising id. `field_names[]` carries field *names* only and is regex-restricted to
`^[a-z][a-z0-9_]*$` so a value glued onto a name fails validation. Event objects are
`.strict()` — an unknown key is rejected, not stripped, because stripping hides the mistake.

**Three different threes.** They are separate metrics and averaging them produces a lie:

| constant | metric name | means | where enforced |
|---|---|---|---|
| 3 exports / 7 days | `activation_items_exported_7d` | activation — **counted from `export_delivered`** | analytics (E0-T5 reports it) |
| 3 exports / 60 minutes | `session_export_burst_60m` | QA proxy for real workload | analytics only |
| 3 items / month, free plan | `free_tier_item_limit` | billing allowance | `apps/api/settings.py` — authoritative; clients read it from `GET /v1/usage`, never from a hardcoded constant |

**v2 placeholder — "listed on the marketplace" is a third state, not an eighth event.** Delivering
a file is not the same as the seller pasting it into eBay, and v1 does not attempt to observe the
latter (that needs marketplace integration). When v2 does, it belongs in a
`listing_confirmations (item_id, marketplace, confirmed_at)` table — **a state table, not an event**.
Putting it in the event stream would permanently confuse "files delivered" with "items listed", and
there would be no way to tell the two rates apart afterwards. Do not add `listing_confirmed` here.

**Latency proxies, because end-to-end minutes are not scriptable.** `$ per item` and
"minutes per item" are not acceptance criteria; these are:

- tool time = `prettify_latency_ms + attrs_latency_ms + export_latency_ms` for one item → **p50
  ≤ 35 s, p90 ≤ 60 s** (this is the number `docs/PLAN.md` gates on)
- `prettify_latency_ms` p50 ≤ 20 s, p90 ≤ 35 s
- `attrs_latency_ms` p90 ≤ 8 s, `export_latency_ms` p90 ≤ 5 s
- workload = `fields_touched` per item → **mean ≤ 5 AND p90 ≤ 12**; `p50` is reported, never gated
  (a mean-only gate gets *easier* as blind accepts pile up, since those contribute `0`)
- proportion of sessions with 3 exports inside one 60-minute window

All three latency fields are **client** measurements (shutter/tap → pixels on screen), which is
why they are larger than the server-side `job_latency_ms`. Putting `attrs_latency_ms` on
`prettify_approved` rather than on `attributes_edited` is deliberate: a seller who never edits
would otherwise never report a latency, and tool time would be silently under-counted.

`<2 min per item` appears nowhere in this repo as an acceptance criterion. It is an **internal,
unverified assumption** (`docs/METRICS.md`); until `docs/PRD.md` §3 measures it, it must not appear
in any seller-facing material — landing page, store copy, paywall, ads, sales DMs. `docs/ONBOARDING.md`
§4.1 is the stricter authority.

### Research data is not an event

Interview recordings, ASR transcripts and quotes attributable to a real person must live **only**
in `docs/research/`, which is git-ignored (`.gitignore` line `docs/research/`, no `.gitkeep` —
the directory is not tracked at all, so a fresh clone has no path to commit into). Consent to be
recorded is not consent to be published.

**Before every recording session**, prove the door is shut:

```bash
git check-ignore -q docs/research/probe.wav && echo "ignored, safe to record" || echo "STOP"
```

If that prints `STOP`, stop. Nothing in `docs/research/` may ever reach the events table, a
dashboard, or this file.

---

## 8. Billing headroom — fields v1 carries but does not use

Pro is 400 items/month and the largest realistic boutique profile is ~400 items/month: zero
headroom for re-lists and returns. The first big customer would hit a wall on day 30. So the
`subscriptions` table carries two columns from day one:

```sql
overage_enabled          boolean not null default false,
overage_unit_price_cents integer null,
```

**v1 behaviour, frozen:** `overage_enabled` is **false**. Hitting the plan limit returns
`402` with `error.code = "quota_exceeded"`. **No overage arithmetic, no proration, no new tier**
is implemented in v1 (ADR-005 §3). These columns exist so the first 400-item boutique does not
require a migration under commercial pressure — they are one migration line, not a feature.

The enforcement authority for the limit itself is `apps/api/settings.py`; clients read the real
number from `GET /v1/usage` (`Usage.items_included`) and must never hardcode 3, 100 or 400.

---

## 9. Version history


| version | date | change | ADR |
|---|---|---|---|
| 1.0.0 | 2026-10-28 | initial freeze: ItemAttributes, AssetPack, Job, reason codes, error envelope, marketplace mapping intent | — |
| 1.0.0 (note) | 2026-10-28 | `AssetPack.on_model_urls` documented as asynchronous; no shape change | ADR-004 |
| events 1.1.0 | 2026-10-28 | analytics vocabulary: 6 frozen events, `fields_touched`, latency proxies, three distinct limits, research-data rule | ADR-002 |
| 1.0.0 (note) | 2026-10-28 | `subscriptions.overage_enabled` / `overage_unit_price_cents` reserved, v1 hard-blocks; no wire shape change | ADR-005 §3 |
| events 1.2.0 | 2026-10-28 | 7th event `export_delivered`; activation re-bound to delivered (not attempted); v2 `listing_confirmations` placeholder | ADR-002 |
| events 1.3.0 | 2026-11-04 | envelope gains `event_id` — the client-generated idempotency key `POST /v1/events` is built on; no new event, no new payload field | ADR-007 |
| events 1.4.0 | 2026-11-05 | envelope gains `item_id`, **graded per event** (required ×5, optional on `paywall_impression`, forbidden on `subscription_started`); ownership checked on write (404); `infra/migrations/0003_events_item_id.sql` | ADR-008 |

**Drift, resolved (kept for the record):** `packages/shared/src/analytics.ts` was briefly at
`EVENTS_VERSION = 1.1.0` with six events while this table already listed seven — the contract ran
ahead of the typed module, which is the normal direction but was unsafe here because mobile codes
against the *types*, not this document. **At the time of that fix** the module was brought to
`EVENTS_VERSION = "1.2.0"` (then the current vocabulary), seven events, and an `ExportDelivered`
zod schema; `packages/shared/test/analytics.test.ts` pinned both the version **and** the presence
of `export_delivered` (a version bump alone would not catch someone rolling the event back). The
vocabulary has since moved on through `1.3.0` (`event_id`) and `1.4.0` (`item_id`) — see the
version history above for the current value. The statement "typed source of truth" at the top of
this section is true again.

`CONTRACT_VERSION` does not move: neither the wire shape nor an enum changed. The events
vocabulary is versioned separately on purpose — `EVENTS_VERSION = 1.4.0` (see §7). Two
consecutive events bumps (`1.3.0`, `1.4.0`) have added a field to the envelope and nothing else,
which is the intended shape of a change to this section: the funnel grew a join key, the API did
not change.
