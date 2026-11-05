# SnapList — METRICS

**Owner:** 许清楚 (Head of Product) · **Date:** 2026-10-28
**Consumes:** `docs/PRD.md` §5 (user stories), `docs/CONTRACTS.md` (job states, `reason_code`)

**Rule for this document:** there are no measured values in it. There are **definitions**,
**event names**, and **decision thresholds we choose in advance**. Anything we would need
real users to know is written `UNMEASURED` with the measurement that would fix it. A
threshold is a *decision*, not data — it is allowed to be a made-up round number, as long as
it is labelled as a decision and fixed before the data arrives.

---

## 1. North star

> ### Items listed per active seller per week
>
> `items_listed` = count of **distinct items** that reached **`export_delivered`**
> (CSV actually produced and delivered to the client) in the week, per seller, averaged over
> sellers with ≥1 such delivery in the trailing 7 days.
>
> **Changed 2026-10-28 on the lead's ruling:** the north star counts `export_delivered`, **not**
> `export_clicked`. `export_clicked` is retained as the *attempt*, and the gap between the two is
> the "tapped and did not get a file" segment — a thing we want to see, not a thing to average
> away. Binding the north star to attempts would have made it an upper bound on itself.

**Why this and not sessions / photos / minutes.** The seller's own unit of progress is one
garment becoming one publishable listing (PRD §3.1). Every other candidate metric moves when
we are *engaging* the seller; this one moves only when the seller's backlog shrinks. If this
number goes up, we are delivering the job. If it goes down while DAU goes up, we are building
a toy.

**Why per *active* seller, not per seller.** Including dormant accounts would let a shrinking
active base look like improving productivity. The cohort is defined below.

**Active seller (definition, fixed):** a seller with ≥1 `export_delivered` in the
trailing 7 days. Weekly active seller count is a **secondary** metric, reported next to the
north star so we can see volume vs. per-seller productivity separately. Neither alone is
enough and this document does not pretend otherwise.

### 1.1 The honest gap in the north star — read this

`export_delivered` proves a CSV was downloaded. It does **not** prove the item was **listed on
a marketplace**. We have no marketplace read access in v1 and will not have it.

> **This is the second of two gaps, and they stack.** Gap 1: `export_clicked` (attempt) vs
> `export_delivered` (a file exists) — now closed by the 7th event. Gap 2: delivered vs actually
> **listed** — still open by design, and the reason the north star is labelled an upper bound.

- **What we can measure today: exported items.** Server-side, exact.
- **What we actually want: listed items.** `UNMEASURED` in v1.
- **Interim method (must ship in v1, cheap):** 24–48h after export, one in-app prompt —
  "Did these 3 items go live?" — with `yes / some / no`. Store as
  `listing_confirmed` (self-report, never mixed into the north star without a label).
- **Correction factor:** `listed ≈ exported × self_report_confirmation_rate`. That rate is
  `UNMEASURED` at time of writing; do not assume it is high, and do not quote a
  "confirmation rate" until ≥50 responses exist.

So: **the north star is instrumented as exported-items-per-active-seller-per-week, and we
label it as an upper bound on listed items.** We name the gap here rather than quietly
calling exports "listings" in the weekly report.

### 1.2 Targets

| Metric | Value | Status |
|---|---|---|
| North star target at 90 days | **UNMEASURED** | Cannot set a target for a metric we have never observed with zero users. Method: after 4 weeks of data, set target = median + 1 for the top quartile, not an invented round number. |
| Directional goal (not a target) | Each active seller lists **more** items in week N+4 than in their first week | chosen goal; verified by comparing each seller's own week-1 baseline to week-4 |
| Baseline for comparison | The seller's stated current monthly volume from onboarding (`INTERVIEWS.md` Q1/Q2 shape) | self-report, ASSUMPTION-grade |

**Do not put a north-star number in any deck until §1.2 has a MEASURED row.**

---

## 2. Activation

> ### Activated = first 3 items exported
>
> A seller is **activated** at the moment their **3rd** `export_delivered` fires,
> within **7 days** of signup. `activation_at` is written once, on the 3rd delivery, and never
> rewritten. **Constant: `activation_items_exported_7d`** — note the name says "exported" and now
> means "delivered"; the constant is frozen and the mapping is documented here rather than
> renamed, because renaming a frozen constant would break the dashboards it exists to protect.
>
> **Binding corrected 2026-10-28 (lead's ruling):** this was `export_clicked`, i.e. it counted
> *attempts*. A seller whose 3rd export failed still activated. The 7th event fixes it; the
> fired-on-event changed, the number did not.

**Why 3, and not 1 or 5.**
- **Not 1:** the first export can be a fluke — a lucky photo, a seller humouring us. It does
  not prove repeatable value, and it does not reveal the friction (attribute corrections,
  retries) that determines whether they come back.
- **Not 5:** the free tier is 3 items (BRIEF §Cost ceilings). Item 4 is the paywall
  (PRD US-6). Defining activation at 5 would define activation as *"paid"*, which conflates
  two different questions — "does the product work" and "will they pay" — into one number
  and makes both unreadable.
- **3 is the point where the seller has reached the free-tier decision with the product in
  their hands.** That is exactly the state we want to measure the funnel from.

**Why 7 days:** a week is the natural listing cadence for the two larger personas (batch on
Sunday, list Tue–Thu) and two listing sessions for Maya. A seller who takes 10 days to reach
3 exports has not activated on our timeline even if they eventually do; they will have churned
from the paywall first. Report the 14-day variant alongside, but the headline is 7.

| Activation metric | Definition | Value |
|---|---|---|
| `activation_rate` | `activated sellers ÷ sellers who reached upload_started` | **UNMEASURED** |
| `time_to_activation` | median seconds from signup to 3rd export, p50 and p90 | **UNMEASURED** |
| Chosen threshold for "onboarding works" | **p50 ≤ 15 min, p90 ≤ 45 min** from signup to 3rd export | decision, fixed now |
| Chosen threshold for "onboarding is broken" | **< 25%** of upload_started sellers reach activation | decision, fixed now |

Those two thresholds are **chosen, not observed**. They are here so that the first dashboard
is readable on day one without anyone inventing a story around a bare number.

---

## 3. Funnel events to instrument

**The vocabulary is seven events, and it has been seven since `EVENTS_VERSION 1.2.0`.** Six
events were frozen in `docs/CONTRACTS.md` §7 / `packages/shared/src/analytics.ts` at
`EVENTS_VERSION 1.1.0`; `export_delivered` (the 7th) was added at product's request in
`1.2.0`. The current vocabulary is `1.4.0`, but the two later bumps added **fields, not
events**: `1.3.0` added `event_id` (ADR-007) and `1.4.0` added `item_id` (ADR-008). Renaming
any of the seven requires an ADR — same rule as any other contract.

> **Wire names come from `docs/CONTRACTS.md` §7.** This document owns *definitions and
> thresholds*; the contract owns the frozen *shape and field names*. If a row below names a
> field the contract does not have, **the contract wins and this table is the thing that is
> wrong** — check §7 before implementing or instrumenting from here. (This used to be a
> paragraph of prose; it is a standing instruction because the table drifted anyway: it carried
> `price_cents`, `paywall_type` and a hashed `stripe_customer_id`, none of which the contract
> has ever had.)
>
> **Payload authority (settled 2026-10-28).** Reconciled at review time: dropped `is_free_item`,
> `export_attempt_index` and hashed `stripe_customer_id` (CONTRACTS carries `row_count`,
> `export_latency_ms`, `amount_cents` instead — none of which my metrics need); `retry_count` and
> `confidence_bucket` are **not** in the frozen payload either, so the prettify retry rate is
> re-derived from `decision` (§3.2) rather than from a counter that does not exist.
>
> **`item_id` (2026-11-05, `EVENTS_VERSION 1.4.0`, ADR-008).** Added to the envelope and graded
> per event — **required** on the five per-item funnel events, **optional** on
> `paywall_impression`, **forbidden** on `subscription_started` (a Stripe webhook has no item).
> It is the join key every per-item number in this document assumes: `tool_time_ms` per item,
> `activation_items_exported_7d` deduped to one row per item, and the `export_clicked` −
> `export_delivered` failure gap. The `item_id` column in the table below states which grade each
> event carries.

| # | Event name (exact) | Fires when | Required properties | `item_id` | Explicitly must NOT contain |
|---|---|---|---|---|---|
| 1 | `upload_started` | The seller submits a photo for processing; a `jobs` row is created | `item_id`, `source` (`camera` \| `library`), `capture_mode` (`single` \| `batch`), `batch_index?` | **required** | image bytes, EXIF/GPS, filename |
| 2 | `prettify_approved` | Seller taps "Use this" on a prettified result and advances to attributes | `item_id`, `decision` (`approve` \| `retry` \| `remove_bg_waitlist`), `job_latency_ms`, **`prettify_latency_ms`**, **`attrs_latency_ms`** | **required** | the image, the URL, the matte |
| 3 | `attributes_edited` | Seller **confirms** the attributes screen — **fires whether or not any value changed** (`fields_touched` may be 0) | `item_id`, **`field_names[]`** (names only, regex `^[a-z][a-z0-9_]*$`, e.g. `["material","measurements_chest"]`), **`edited_field_count`** (value changed), **`fields_touched`** (read *or* changed, deduped per field) | **required** | the values, any measurement number, brand strings, `"Material: Silk"`-style name/value pastes |
| 4 | `export_clicked` | Seller **asks for** a marketplace file. **This is the attempt** — the file may never be produced | `item_id`, `marketplace` (`ebay` \| `poshmark` \| `vinted`), `row_count`, **`export_latency_ms`** | **required** | the CSV contents, buyer/seller data |
| 5 | **`export_delivered`** | The CSV is **actually produced and returned to the client** (7th event, `EVENTS_VERSION 1.2.0`). **The north star and activation bind to this one** | `item_id`, `marketplace`, `row_count`, **`bytes`**, **`delivery_latency_ms`** | **required** | the CSV contents, buyer/seller data |
| 6 | `subscription_started` | Stripe webhook `checkout.session.completed` is received **and signature-verified**, then plan is activated | `plan` (`starter` \| `pro`), **`amount_cents`**, `currency`, `paywall_variant` (`a` \| `b` \| `c`, from `ONBOARDING.md`) | **forbidden** | card data, email, full address; **`stripe_customer_id` is not in the event at all** (PII ban, §4) — read it from Stripe's own records, keyed by the subscription id |
| 7 | `paywall_impression` | The paywall is shown, converted or not (6th event) | `paywall_variant`, **`trigger`** (`upload_4th` \| `on_model` \| `export_limit`), `plan_offered` (`starter` \| `pro`) | optional | anything about the seller's identity; `free_items_exported` is **not** in v1 (`future field, not in EVENTS_VERSION 1.4.0`) |

> **Two fields in this table name something the contract does not carry, and that is now the
> table's problem, not the contract's.** `paywall_type (soft|hard)` was never a wire field — the
> contract carries `trigger` (why the wall appeared) and `plan_offered` (what it offered). If a
> "soft vs hard" distinction is still wanted, it is expressible today as
> `trigger=on_model`/`upload_4th` (soft: the seller can walk away) versus `trigger=export_limit`
> (hard: the work is done and cannot be handed over) — write the mapping down before adding a
> field. `free_items_exported` does not exist either; the confounder it was meant to capture is
> computed from the events themselves (count `export_delivered` per `item_id` for the seller in
> the window), which is also the version that cannot disagree with the funnel.

**`export_clicked` vs `export_delivered` is a deliberate pair, not a duplicate.** The difference
is the "tapped and got nothing" segment: CSV generation failed, the network died, or the seller
abandoned the sheet. That is a **product defect signal**, and collapsing the two into one event
would have averaged it away in exchange for a number that is easier to read. Watch
`export_clicked − export_delivered` weekly; it is not a metric you gate on, it is a metric that
tells you when the export screen is broken.

### 3.1 Funnel order and the drop-off each event names

```
upload_started  →  prettify_approved  →  attributes_edited*  →  export_clicked  →  export_delivered  →  subscription_started
```

`*` `attributes_edited` is **not a funnel step and must never be treated as a drop-off.**
A seller who accepts every suggested value produces an event with `fields_touched = 0`; a
seller who never opens the screen produces no event at all. Either way, counting its absence
as drop-off would make a *trusted* model look like a broken screen — the single most likely
instrumentation mistake in this funnel. Two counters now separate the cases
(`fields_touched`, `edited_field_count` — architect, 2026-10-28); see §3.2. The funnel is
therefore:

- **Main path:** `upload_started → prettify_approved → export_clicked → export_delivered → (paywall) → subscription_started`
- **Failure branch:** `export_clicked` with no matching `export_delivered` = attempted and did not
  get a file. Counted, never folded into the main path.
- **Trust branch (side signal, not a step):** `prettify_approved → attributes_edited`, read via
  **`fields_touched`** and **`edited_field_count`**, never via "did the event fire".
  - lots of edits ⇒ the model is wrong (fix the model);
  - `fields_touched = 0` on most items ⇒ the screen is being skipped (fix the screen).

### 3.2 Derived metrics (all UNMEASURED until the events exist)

| Metric | Formula | Why it matters | Decision threshold (chosen) |
|---|---|---|---|
| Prettify retry rate | `count(prettify_approved where decision = "retry") ÷ count(prettify_approved)` — derived from `decision`, **not** from a `retry_count` field (CONTRACTS §7 does not carry one) | Does the matte actually work on real garments? | > 1.5 retries/item ⇒ segmentation is not shippable |
| Attribute override rate | items where **`edited_field_count > 0`** ÷ items where `attributes_edited` fired | Is the VLM wrong often enough to matter? | **> 40% of items edited, or any single field edited on > 50% of items, ⇒ attributes are not trusted** — do not sell them as automatic |
| Blind-acceptance rate | items where **`fields_touched = 0`** ÷ items where `attributes_edited` fired | The other failure mode: nobody is reading the screen | **> 30% ⇒ the attributes screen is being skipped**, i.e. we are shipping unchecked data into listings. Investigate before celebrating the low override rate |
| **Denominator caveat (bias direction).** The denominator is only "items opened for review" if the event fires on **opening** the sheet. If it is implemented as "fires once a field is touched", a seller who opens the sheet, glances, and closes produces **no event**, so pure blind accepts fall out of *both* numerator and denominator and **this rate is biased low — the direction that hides the problem.** Pending the `CONTRACTS §7` wording clarification (architect, 2026-10-28: "opened the page, counts may be 0"), read any value under 30% as a lower bound on skipping, not as reassurance. | | | |
| Free→paid conversion | `subscription_started ÷ paywall impressions` | The business | **UNMEASURED.** No benchmark is quoted here on purpose. |
| `bad_photo` rate | `failed(bad_photo) ÷ upload_started` | Is our photo capture guidance working? | > 15% ⇒ fix the camera screen before anything else |
| Failure mix | share of each `reason_code` | Tells us which failure to work on next | n/a |
| Cost per item | summed provider cost ÷ exported items | BRIEF ceiling ≤ $0.03 | **> $0.03 ⇒ stop shipping features, cut cost** |
| Cost per on-model render | summed VTON cost ÷ successful renders | BRIEF ceiling ≤ $0.10 | > $0.10 ⇒ gate the feature or renegotiate |
| Export→listing confirmation | `listing_confirmed(yes)` ÷ prompts answered | The north star's honesty gap (§1.1) | < 70% ⇒ our CSV is not importing; that is a fire |

### 3.3 Engineering acceptance metrics — **not** activation, do not merge

**Team-lead ruling, 2026-10-28:** `<2 min per item` may **not** be an acceptance criterion,
because end-to-end minutes are not measurable by a script, and the per-item baseline itself is
unverified (`PRD §0`). Two measurable proxies replace it. Both are scriptable; neither is
the north star, activation, or a customer-facing claim.

**A. Instrumented tool time per item** — the part we actually control

| Measure | Definition | Carried on | Chosen threshold |
|---|---|---|---|
| `prettify_latency_ms` | Upload submit → prettified image rendered **on screen**. *More* than the server-side `job_latency_ms`: the seller also feels upload and render, so measure it **client-side** too | `prettify_approved` | p50 ≤ **20 s**, p90 ≤ **35 s** (`ONBOARDING.md` §0 A1) |
| `attrs_latency_ms` | Approve tap → attributes screen interactive (the "everything is on screen" moment) | `prettify_approved` | p90 ≤ **8 s** |
| `export_latency_ms` | Export tap → file produced and returned | `export_delivered` | p90 ≤ **5 s** |
| **tool time** = the three summed | The only per-item duration a test can assert | derived | p50 ≤ **35 s**, p90 ≤ **60 s** |

**Compute tool time from `prettify_approved` + `export_delivered` only. Never from
`attributes_edited`** (architect, 2026-10-28 — thank you for catching this): a seller who
accepts every suggested value never fires `attributes_edited` at all, so deriving tool time
from it would systematically **understate** the number precisely for the sellers who had the
smoothest run. Two events, three properties, no dependency on an optional event.

**Tool time deliberately excludes human review time.** It measures the part the machine owns;
the seller's 4.0 minutes of checking in `PRD §3.2` are *not* in it and must never be subtracted
from it to manufacture a "<2 min" number. That is the whole reason this proxy replaced the
per-item promise.

These thresholds are **decisions, not observations**. `35 s` already assumes the numbers in
`ONBOARDING.md` A1 hold; if the pipeline misses them, this table changes and the 90-second
script changes with it.

**B. Session-cleanliness ratio** — the part that stands in for "did it feel fast"

| Measure | Definition | Chosen threshold |
|---|---|---|
| `session_3_exports_rate` | Sellers with **3 `export_delivered` inside a rolling 60-minute window** ÷ sellers with ≥1 delivery | **no threshold yet** — first measure the baseline, then set it (see the warning below) |
| `fields_touched_per_item` | **mean** `fields_touched` (fields *read or changed*, not only changed) over exported items; **p50 reported alongside** so a mean dragged by a few heavy items is visible | **N = 5 fields touched per item** |

> **Aligned to `docs/PLAN.md` line 59 on 2026-10-28:** this row said `p50` and PLAN said `mean`.
> Two docs disagreeing on an acceptance gate is how a gate quietly stops existing. **The gate is
> the mean** (simpler to compute from event rows, and PLAN/CONTRACTS already state it), **p50 is
> reported next to it** so that a mean inflated by a handful of 15-field items is not mistaken
> for a typical item. Do not change one of the three documents without changing the other two.

**Why `fields_touched` and not `edited_field_count`.** `edited_field_count` counts only fields
whose value *changed*. A seller who reads `chest`, agrees with it, and moves on has spent real
time and produced zero edits — so an `edited_field_count`-based metric understates friction and
makes "the seller trusts the model" look identical to "the seller is not looking". `fields_touched`
(focus→blur or explicit confirmation, deduplicated per field) is the number that means what we
want it to mean. **Architect adopted this on 2026-10-28** (`attributes_edited` carries both
counters), so this is no longer a lower bound and must not be labelled one.

**Requires the firing condition `fields_touched ≥ 0`** — i.e. `attributes_edited` fires even
when the seller changes nothing (`PRD US-3`). If the event only fires on actual changes, the
blind-acceptance case is invisible again and we are back to a lower bound. This is a
**semantics change flagged to the architect on 2026-10-28**; PRD US-3 was amended to match.

**Why N = 5 and not 0.** A typical listing carries `chest / length / sleeve / waist / rise` —
five numbers a competent seller may legitimately adjust **even when the model is right**.
Expecting zero edits is not an ambition, it is a misunderstanding of the job (`PRD §3.3`: 4.0
of the remaining 8.5 minutes *are* the seller checking). N = 0 as an acceptance criterion
would push the team to make the attributes screen harder to edit, which is the opposite of the
product.

**Naming — the three "3"s are also three names in code.** The architect pinned
`activation_items_exported_7d` / `session_export_burst_60m` / `free_tier_item_limit` with tests,
so a dashboard that tries to merge them collides at import time rather than quietly reporting
a wrong number. Keep those identifiers; do not alias them in SQL.

**Warning — three different "3"s now exist in this repo. Keep them apart.**

| "3" | Where | Meaning | Used for |
|---|---|---|---|
| 3 items exported | §2 activation | within **7 days** of signup | lifecycle / funnel |
| 3 exports in 60 min | §3.3 B | within **one session** | QA acceptance proxy |
| 3 free items | BRIEF, `ONBOARDING.md` §2 | the **billing** cap | paywall trigger |

Reporting one as another will make the dashboard lie. They are deliberately different numbers.

---

## 4. Instrumentation rules

1. **Server-side for anything that can be billed or paid for.** `export_delivered` and
   `subscription_started` must be emitted by the API, not the client. `subscription_started`
   is emitted only after webhook signature verification — a client-side event here would
   over-report revenue. **`export_delivered` in particular may never be client-only**: it is the
   event the north star binds to, so a client-emitted "I got a file" is exactly the kind of
   self-report we refuse to trust.
2. **Client-side is acceptable for** `upload_started` and `prettify_approved` only if the API
   independently confirms the job exists. Client events without a server counterpart are
   treated as telemetry, never as truth.
3. **No PII in any property.** No email, no name, no address, no image, no free-text, no
   location. `field_names[]` carries **field names, never values** — a measurement value and a
   brand name are seller data (see `docs/PRD.md` §5 US-3).
4. **Envelope on every event:** `event_name`, `user_id`, `anon_id` (pre-signup), `ts` (ISO
   8601, UTC), `app_version`, `platform` (`ios` \| `android` \| `web`), `session_id`.
5. **Idempotency.** `export_clicked` fires once per item per *attempt*; `export_delivered` fires
   **once per item per delivered file**. Re-downloading the same file is not a new delivery and
   must not inflate the north star (PRD US-5); a genuine retry after a failure is a second
   delivery attempt, and a second success is still one item — dedupe on `item_id` (§3.3(d) in the
   `E0-T5` spec, `HANDOFF-product.md` §12.2).
6. **Event names are a contract.** Adding an event is free. Renaming or removing one is an
   ADR + version bump, because dashboards, the north star, and the paywall A/B all break
   silently otherwise.
7. **Interview transcripts are personal data, not analytics.** Recordings, transcripts, and
   any quote attributable to a named person live **only** in `docs/research/` (git-ignored)
   and never enter the event store, the warehouse, or a dashboard. This became load-bearing
   on 2026-10-28 when note-taking moved to recording + ASR (`INTERVIEWS.md` §1.3).
   - Aggregated scorecard numbers (`F1`–`F13`, medians) **may** be committed.
   - Verbatim quotes **may** be committed only **anonymised** — role + volume band, no name,
     no shop name, no town, no marketplace handle.
   - Consent to record is **not** consent to publish; they are separate lines on the consent
     form. Withdrawal of consent mid-call means delete, not "keep the useful part".
   - Audio files are deleted once the audio-check pass (`INTERVIEWS.md` §1.3 step 2) is
     signed off in the scorecard.

---

## 5. Paywall experiment instrumentation

Three copy variants exist in `docs/ONBOARDING.md` §4.

> **Assignment corrected 2026-10-28 (ADR-005 §2, architect):** assignment is **server-side and
> sticky** — `profiles.paywall_variant text not null default 'a' check (a|b|c)`, written the
> first time the seller meets a paywall and **never re-rolled per request or per session**. A
> `user_id` hash computed client-side would re-roll on device change and destroy attribution
> weeks later. **Do not implement hashing** — the earlier draft of this document said to.

- **Exposure:** `paywall_impression` with `paywall_variant`, **`trigger`** (`upload_4th` \|
  `on_model` \| `export_limit`), `plan_offered`, and `item_id` **when there is one** (it is
  optional on this event only — see §3).
  > **Naming note:** `paywall_impression` is not one of the five original funnel events. It is
  > one of the **two added events** (with `export_delivered`), all seven now frozen in
  > `docs/CONTRACTS.md §7` and `packages/shared/src/analytics.ts` under `EVENTS_VERSION 1.4.0`
  > (2026-10-28; `1.4.0` added `item_id`, ADR-008). Do not overload `export_clicked` to carry it,
  > and do not merge the two vocabularies. The wire field is `trigger`, not `paywall_type`.
- **Primary metric:** `subscription_started` with matching `paywall_variant`, within 7 days of
  the impression.
- **Winner rule (chosen now, so it cannot be rationalised later):** highest
  `subscription_started ÷ paywall_impression`, requiring **≥200 impressions per arm and ≥14
  days**, otherwise **no winner is declared**. Do **not** peek daily and stop on the first
  arm that leads.
- **Confounder to watch:** how far the seller had already got before the wall — 4+ delivered
  exports at impression time. A variant that reaches more high-intent sellers will look better
  for reasons unrelated to its copy; segment the readout by it. Compute it from the events
  (`export_delivered` rows per `item_id` for that seller in the window), not from a
  `free_items_exported` property — that property does not exist in the frozen payload, and a
  figure derived from the same table cannot disagree with the funnel it is explaining.

---

## 6. Weekly review — the five numbers that go in the report

In this order, with these labels, and nothing invented:

1. **North star** — exported items per active seller per week (labelled *upper bound on
   listings*, §1.1).
2. **Activation rate** — 3 exports in 7 days, vs. the chosen 25% threshold.
3. **Attribute override rate** — the trust number, vs. the chosen 40% threshold.
4. **Cost per item and per render** — against $0.03 / $0.10. Hard ceilings, no averaging away.
5. **Free→paid conversion** — with `UNMEASURED` printed verbatim until ≥200 impressions per
   arm exist.

Anything else is a question, not a report.
