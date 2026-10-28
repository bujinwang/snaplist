# SnapList — PRD v1

**Owner:** 许清楚 (Head of Product) · **Status:** draft for architect review · **Date:** 2026-10-28
**Language:** English (repo default) · **Upstream constraints:** `BRIEF.md` (hard, not relitigated here)

---

## 0. Honesty banner — read before quoting any number in this document

Every number in this PRD is one of three things, and is labelled:

| Label | Meaning |
|---|---|
| **MEASURED** | Produced by a command run on this machine, output pasted in this repo. |
| **ASSUMPTION** | A chosen input, declared as such, with a sensitivity range. Not a fact. |
| **UNMEASURED** | We do not know. A measurement method is named. Do not put this in a deck. |

There is currently **zero** primary user research behind this document. `docs/INTERVIEWS.md`
is the instrument that replaces the ASSUMPTION labels with MEASURED ones. Until it has been
run, treat the ICP, the time budget, and the value math as *hypotheses with arithmetic*
attached — the arithmetic is verifiable, the inputs are not.

**Known conflict to resolve — flagged, not hidden:** `BRIEF.md` line 14 states the current
workflow is "~8 min per item". The bottom-up step budget in §3 totals **19.5 min**. One of
those numbers is wrong. §3 is built bottom-up and is internally consistent (sum verified,
§3.4); the 8-min figure in BRIEF is an unsourced top-down estimate. **Both are labelled
ASSUMPTION until §3 is validated by `docs/INTERVIEWS.md` Q3–Q5.** Consequence for the
roadmap: the BRIEF's "<2 min per item" target is **not reachable in v1** (§3.3 explains
exactly which two minutes are missing and why). Do not promise <2 min in marketing copy or
in `docs/PLAN.md` before §3 is measured.

---

## 1. ICP

**One paragraph.** A solo or two-person resale operation — predominantly women 25–45 — that
sells secondhand clothing it sources from thrift stores, estate lots, wholesale bundles, or
its own closet, across **eBay plus at least one of Poshmark / Depop / Vinted**. It lists
**20–400 items a month**, works from a spare room, a garage, or a small shop with a
ring-light and a bedsheet backdrop, and shoots on a phone. Its bottleneck is not sourcing and
not selling — it is the **per-item tax of turning a garment into a compliant listing**:
photograph, clean the image, measure, write a title, fill twenty item-specifics, price against
comps, and post the same item three times because each marketplace wants different fields. It
already pays real money every month for tools that each solve one slice of this (image
cleanup, cross-listing, label printing) and it evaluates new tools purely on one axis:
**does this let me list more items this week without hiring anyone**. It is not looking for a
stylist, a community, or a closet organiser; it is looking for hours back.

**Who this is not for (v1):** one-off closet clearers listing <5 items/month, consignment
shops with staff and a POS system, sneaker/hard-goods flippers (category fields and defect
semantics are different), and anyone who needs to sell *inside* SnapList rather than export
out of it.

---

## 2. Personas

Three named sketches. Volume figures (20 / 120 / 400 per month) are **ASSUMPTION** — they
are the segmentation hypothesis to be tested by `INTERVIEWS.md` Q1–Q2, not survey results.

### 2.1 Maya Okonkwo — casual, 20 listings/month → the free-tier → Starter convert
- 31, dental hygienist in Leeds, sells on **Vinted + eBay** on Sunday and Wednesday evenings.
- Sources: two charity-shop loops a month plus her own wardrobe. Sells ~20 items, average
  sale price £14.
- Kit: iPhone 13, a windowsill for daylight, the free tier of a background-remover app,
  a tape measure she keeps losing.
- Current process: photographs 6–8 items in one daylight window, then spends the evening
  typing listings on the sofa with the TV on. Abandons half-finished listings in drafts.
- What she actually wants: "photograph all six, then approve six listings." She does not
  want to learn a tool; she wants the evening back.
- Price sensitivity: **high**. $19/mo is a real decision. She will churn in a slow sourcing
  month. **ASSUMPTION: she converts only above ~10 items/month** (see §4.4 break-even).
- What makes her quit in the first 90 seconds: a permission prompt before she sees value,
  an account wall before she sees her own photo prettified, or a measurement field she
  cannot fill.

### 2.2 Dario Whitfield — pro, 120 listings/month → the Pro plan anchor
- 38, full-time reseller in Manchester, 4 years in, **eBay (80%) + Depop (20%)**.
- Sources: wholesale rag-house bundles and a weekly estate-lot run. 250-item deadstock
  backlog on shelving labelled by bin number.
- Kit: phone on a tripod, a fold-flat lightbox, a laptop, a label printer, and **already
  pays for cross-listing + image tooling** — he is the "already pays for tools" buyer in
  BRIEF §Who pays.
- Current process: batch-shoots 30 items on Monday, then lists across Tuesday–Thursday. He
  times himself and knows his per-item number to the minute. He will tell us whether 19.5
  or 8 is correct.
- What he actually wants: throughput, consistency, and **no re-typing**. He will tolerate an
  ugly UI forever if it is 30 seconds faster. He is the persona who cares most about the
  **CSV export importing cleanly** — a broken import costs him an evening, and he will
  churn over it immediately.
- Price sensitivity: **low** relative to his revenue; $49/mo is noise if it saves 20 hours.
  His real objection is trust: "if it gets one measurement wrong and the item comes back, I
  lose more than the subscription."

### 2.3 Ingrid Solberg — vintage boutique, 400 listings/month → the ceiling case
- 52, owns **Solberg & Vine Vintage**, a physical shop in Portland OR plus online on
  **eBay + Etsy-style vintage channels**, with two part-time helpers.
- 400 listings/month is her ceiling case; she currently lists ~250 and the gap is exactly
  the labour she cannot afford.
- Kit: two shooting stations, her helpers do photography, she does copy because "the girls
  can't date a collar."
- What she actually needs that the others don't: **era/decade accuracy**, **material
  identification** (is it real silk?), **defect disclosure** (that is a legal and returns
  question, not a style question), and a **visually consistent catalog look** because her
  brand is the look.
- What will make her distrust us: a confident wrong decade. She would rather see
  `low_confidence` than a guess — which is exactly why the confidence field and the
  `reason_code` enum are user-facing surface, not internal telemetry.
- Price sensitivity: **lowest of the three**, but the **Pro cap of 400 items exactly equals
  her volume** — no headroom for relists and returns. **Open question for team-lead:** is
  there a tier above Pro, or overage billing? Not in v1 scope; flagged in
  `HANDOFF-product.md`.

---

## 3. The job-to-be-done and the current manual workflow

### 3.1 JTBD

> When I have a pile of garments that I have already paid money for, I want to turn each one
> into a marketplace-ready listing — image, measurements, attributes, copy, price — **without
> becoming a copywriter, a photographer, or a data-entry clerk**, so that my inventory turns
> into cash faster than my sourcing turns into inventory.

Functional job: *convert one physical garment into one compliant, publishable listing.*
Emotional job: *stop dreading the listing backlog.* Social job: *look like a professional
shop, not a car-boot seller.*

The unit of value is **one item moving from garment to exported listing**. That is why the
north star in `METRICS.md` is items per seller per week and not sessions, minutes, or photos.

### 3.2 Current manual workflow — minute-by-minute, per item

Eight steps, in the order a seller actually performs them. Each figure is **ASSUMPTION**
(operator's own resale experience + observed workflows; no instrumented timing yet).

| # | Step | What actually happens | Baseline min | SnapList v1 min | How v1 gets there |
|---|---|---|---|---|---|
| 1 | **Shoot** | Lay flat, smooth the fabric, fight the shadow, take 6–8 frames, discover half are blurry | 4.00 | 2.00 | Guided single frame: framing overlay, auto quality gate rejects blur before it becomes a job. One good shot instead of eight bad ones |
| 2 | **Cull** | Swipe through the camera roll, pick the least-bad frame | 1.00 | 0.00 | Guided shot has no cull step; the gate already happened |
| 3 | **Background** | Background remover app, review the matte, fix the sleeve it ate, re-export | 3.00 | 0.25 | Segment → matte → rectify → neutral backdrop runs as one job; seller only **approves or retries** |
| 4 | **Measure** | Find the tape, lay flat again, pit-to-pit, length, sleeve, waist, rise; write them down | 3.50 | 2.00 | VLM proposes measurements from the photo **plus known size label**; seller confirms with the tape on high-value items only. Deliberately not auto-applied (§6) |
| 5 | **Title** | Keyword-nudge, fit it into the character limit, avoid the same words as the last 20 | 1.50 | 0.25 | Generated from attributes; seller edits a filled field instead of a blank one |
| 6 | **Item specifics** | 12–20 dropdowns per marketplace: category, size type, style, material, sleeve, fit, colour, condition | 3.00 | 1.00 | Pre-filled from the attribute JSON; seller scans and corrects the two or three that matter |
| 7 | **Price** | Filter sold comps, sanity-check against what he paid, decide | 2.00 | 2.00 | **No saving.** Comps are not in v1 (§6) |
| 8 | **Post** | Upload photos, reorder, confirm category, set shipping policy, hit publish; repeat per marketplace | 1.50 | 1.00 | One CSV row per item, multi-marketplace mapping, one bulk import per marketplace instead of N manual posts |
| | **Total** | | **19.50** | **8.50** | **delta = 11.00 min/item** |

### 3.3 Where the remaining 8.5 minutes are, and why "<2 min" is not a v1 promise

The floor is not the model's speed, it is the **seller's judgement**. 8.5 min breaks down as:

- **4.0 min of human verification** (confirm measurements 2.0, scan item specifics 1.0,
  confirm the prettified image 0.25, confirm export 1.0 is partly included above). A seller
  who cannot check a number will not trust the listing, and a wrong measurement is a
  returned item, not a bad listing.
- **2.0 min of pricing** that v1 does not touch at all (§6).
- **2.0 min of photography** that no software removes — the garment has to be laid down and
  framed by a human.

Reaching **<2 min** requires two things that are *out* of v1: (a) measurement confidence high
enough that sellers stop reaching for the tape, and (b) comps-based price suggestion. Both are
post-v1 and both need accuracy data we do not have. **v1 claim to make instead: "about 8
minutes per item, most of it checking, not typing."** That is still a 56% cut and it is a
claim we can keep.

### 3.4 Sum check (MEASURED)

Baseline `4.00+1.00+3.00+3.50+1.50+3.00+2.00+1.50`, v1
`2.00+0.00+0.25+2.00+0.25+1.00+2.00+1.00`. Verified by running the arithmetic:

```
baseline steps: 8 sum= 19.5
v1       steps: 8 sum= 8.5
per-step delta: {'shoot': 2.0, 'cull': 1.0, 'background': 2.75, 'measure': 1.5,
                 'title': 1.25, 'specifics': 2.0, 'price': 0.0, 'post': 0.5}
total saving/item = 11.0
```

Per-step deltas sum to `2.0+1.0+2.75+1.5+1.25+2.0+0.0+0.5 = 11.00`, which equals
`19.50 − 8.50`. **The budget sums to a stated total: 19.50 min baseline, 8.50 min with v1,
11.00 min saved.**

---

## 4. Value math

### 4.1 The formula

```
minutes_saved_per_item  ×  items_per_month  ÷  60  ×  seller_hourly_value  =  $ saved / month
```

Three inputs, all **ASSUMPTION**:

| Input | Value used | Range to test | Where it gets measured |
|---|---|---|---|
| `minutes_saved_per_item` | 11.0 (§3.4) | 6.0 – 14.0 | `INTERVIEWS.md` Q3–Q5 + instrumented time-in-step once shipped |
| `items_per_month` | 20 / 120 / 400 (§2) | 20 – 400 | `INTERVIEWS.md` Q1–Q2 |
| `seller_hourly_value` | **$25/h** | **$15 – $40** | `INTERVIEWS.md` Q10–Q11 (van Westendorp + stated income/hour) |

**`seller_hourly_value` is explicitly an assumption, not a wage survey.** $25/h is a
*replacement-cost* figure: what it would cost to pay someone else to do this work, which is
the right number for a seller deciding whether to buy a tool. It is not median reseller
income and it is not derived from any dataset we possess.

### 4.2 Monthly value at the $25/h assumption (base case, 11.0 min saved/item)

| Persona | items/mo | minutes saved | hours saved | **$ saved/mo** |
|---|---|---|---|---|
| Maya (casual) | 20 | `11.0 × 20 = 220` | `220 ÷ 60 = 3.67` | `3.67 × $25 = $91.67` |
| Dario (pro) | 120 | `11.0 × 120 = 1,320` | `1,320 ÷ 60 = 22.00` | `22.00 × $25 = $550.00` |
| Ingrid (boutique) | 400 | `11.0 × 400 = 4,400` | `4,400 ÷ 60 = 73.33` | `73.33 × $25 = $1,833.33` |

### 4.3 Same formula, recomputed at two price points

"Price point" read both ways, because the seller cares about one and we care about the other.

**(a) Recompute at two *seller hourly values* — does the value still exist at a low rate?**

| seller hourly value | Maya (20/mo) | Dario (120/mo) | Ingrid (400/mo) |
|---|---|---|---|
| $15/h | `2.00h × 15 =` **$55.00** | `12.00h… → 22.00h × 15 =` **$330.00** | `73.33h × 15 =` **$1,100.00** |
| $25/h (base) | **$91.67** | **$550.00** | **$1,833.33** |
| $40/h | **$146.67** | **$880.00** | **$2,933.33** |

Even at the pessimistic $15/h, every persona's monthly value exceeds both plan prices.
**The value proposition survives the bad case.** What does *not* survive is Maya's ROI
at the $49 Pro price — see (b).

**(b) Recompute against the two *plan prices* (BRIEF §Cost ceilings: Starter $19, Pro $49)**

| Persona | $ value/mo @ $25/h | ROI on Starter **$19** | ROI on Pro **$49** | Right plan |
|---|---|---|---|---|
| Maya, 20 items | $91.67 | `91.67 ÷ 19 =` **4.82×** | `91.67 ÷ 49 =` **1.87×** | Starter |
| Dario, 120 items | $550.00 | `550.00 ÷ 19 =` **28.95×** | `550.00 ÷ 49 =` **11.22×** | Pro (needs on-model) |
| Ingrid, 400 items | $1,833.33 | `1833.33 ÷ 19 =` **96.49×** — but she is **20× over the 100-item cap** | `1833.33 ÷ 49 =` **37.41×** — cap is **exactly 400**, no headroom | Pro, and it is tight |

Reading: at $19 the pitch to Maya is "you get $4.82 of your time back per $1" — a good but
not amazing consumer-tool multiple. At $49 that pitch collapses to 1.87×, so **$49 must never
be shown to a 20-item seller**: on-model rendering is worth nothing to someone listing 20
items, and the multiple does not support it. Pricing is therefore not a test we can run
blindly across the whole base.

### 4.4 Break-even and why the free tier is 3 items

Break-even item count for Starter $19 — `$19 ÷ (11.0 ÷ 60 × hourly_value)`:

| seller hourly value | value per item | break-even | check |
|---|---|---|---|
| $15/h | `11.0/60 × 15 =` $2.75 | **7 items/mo** | `7 × 2.75 = $19.25 ≥ 19`; `6 × 2.75 = $16.50 < 19` |
| $25/h | `11.0/60 × 25 =` $4.5833 | **5 items/mo** | `5 × 4.5833 = $22.92 ≥ 19`; `4 × 4.5833 = $18.33 < 19` |
| $40/h | `11.0/60 × 40 =` $7.3333 | **3 items/mo** | `3 × 7.3333 = $22.00 ≥ 19`; `2 × 7.3333 = $14.67 < 19` |

Two conclusions that shape the product:

1. **The 3-item free tier sits at or below break-even for most sellers** — 3 items is worth
   $8.25–$22.00 of their time, versus a $19 subscription. So the free tier is a *demo*, not a
   self-sufficient substitute, and the paywall at item 4 is honest rather than exploitative.
   It also means we should **not** expect conversion from anyone under ~5 items/month, and we
   should not try to force it.
2. **Mid-case break-even is 5 items, not 3.** The paywall therefore appears one item before
   the seller has actually broken even. Copy must lead with *this month*, not *per item* —
   which is why the paywall variants in `ONBOARDING.md` are written the way they are.

### 4.5 Sensitivity — what if 11.0 min is wrong?

Value at $25/h, across the plausible saving range:

| minutes saved/item | Maya (20/mo) | Dario (120/mo) | Ingrid (400/mo) |
|---|---|---|---|
| 6.0 (seller overrides everything) | 2.00 h → **$50.00** | 12.00 h → **$300.00** | 40.00 h → **$1,000.00** |
| 8.5 | 2.83 h → **$70.83** | 17.00 h → **$425.00** | 56.67 h → **$1,416.67** |
| **11.0 (base)** | 3.67 h → **$91.67** | 22.00 h → **$550.00** | 73.33 h → **$1,833.33** |
| 14.0 | 4.67 h → **$116.67** | 28.00 h → **$700.00** | 93.33 h → **$2,333.33** |

**Kill criterion:** if the measured saving lands at or below **6.0 min/item**, Maya's value
($50) no longer justifies a $19 subscription with conviction and the casual segment should be
dropped from the GTM in favour of pro-only. Write that down now, before we have the data, so
we cannot rationalise later.

### 4.6 What this section does NOT claim

- No market size, TAM, or "N million resellers" figure. **UNMEASURED.** Method: bottom-up
  count of active sellers above a volume threshold from marketplace-published seller
  statistics, plus our own waitlist density by region. Not done.
- No conversion rate from free to paid, and no comparable-tool benchmark. **UNMEASURED.**
  Method: `subscription_started ÷ paywall_impression` from `METRICS.md`, once there are
  ≥200 impressions per paywall variant. Not measured at time of writing.
- No churn, LTV, or payback figure. **UNMEASURED.** Requires ≥90 days of cohort data.
- The $19/$49 prices are **fixed by BRIEF** and are not argued here; §4.3(b) only tests
  whether the value math supports them.

---

## 5. User stories

Six stories, Given/When/Then, each with acceptance criteria a QA engineer can turn directly
into a test. Story priority: **US-1 to US-4 are P0** (the paid loop does not exist without
them), **US-5 P0 for eBay only**, **US-6 P0** (no revenue otherwise).

Event names in these criteria are **literal and frozen** — see `METRICS.md` §3.

---

### US-1 — Prettify: garment photo → catalog-ready image (P0)

*As a seller, I want my phone photo turned into a clean, evenly lit, straight-on flat-lay so
that my listings look like a shop's without me owning a lightbox.*

- **Given** a signed-in seller on the upload screen,
  **when** they submit a single photo of one garment,
  **then** the app emits `upload_started`, creates a `jobs` row, and shows a progress state
  within 1 second of the upload completing.
- **Given** a job for that photo,
  **when** the job finishes,
  **then** the response conforms to the `AssetPack` shape in `docs/CONTRACTS.md` with
  `prettified_url` and `cutout_url` both non-null and both loading in the app.
- **Given** a prettified image on screen,
  **when** the seller taps "Use this",
  **then** the app emits `prettify_approved` with `item_id` and `job_id`, and advances to
  attributes.
- **Given** a prettified image on screen,
  **when** the seller taps "Try again" up to 3 times,
  **then** each retry creates a new job, only the final approval emits `prettify_approved`,
  and the retry count is recorded on the job row (this is our quality signal, §3 of METRICS).
- **Given** the job fails,
  **when** it fails,
  **then** the UI shows a human sentence for the `reason_code` — never the raw enum, never
  "Something went wrong" alone — and the seller is not charged an item credit.

---

### US-2 — Bad photo is caught before it costs a job (P0)

*As a seller, I want to be told my photo is unusable before I wait, so that I don't sit
through a 40-second job to learn I photographed my thumb.*

- **Given** the camera screen,
  **when** the seller taps the shutter,
  **then** a client-side quality gate runs and rejects clearly unusable frames *before*
  upload with an actionable instruction ("too dark — move to a window", "garment not fully
  in frame").
- **Given** a photo that passes the client gate but contains no garment,
  **when** the job runs,
  **then** it terminates in state `failed` with `reason_code = no_garment`, and the app
  emits no `prettify_approved`.
- **Given** a job that fails with `bad_photo` or `no_garment`,
  **then** the seller's free-item counter is **unchanged**, and the failure is recorded with
  its `reason_code` for the weekly `bad_photo` rate.
- **Given** the segmentation confidence is below threshold,
  **then** the job must return `low_confidence` rather than a plausible-looking bad matte.
  A wrong-but-confident output is worse than an honest failure — see Ingrid in §2.3.

---

### US-3 — Attributes: structured JSON the seller can correct (P0)

*As a seller, I want the category, colour, material, measurements and defects already filled
in so that I'm correcting a form instead of typing one.*

- **Given** an approved prettified image,
  **when** the attribute job completes,
  **then** the payload validates against `ItemAttributes` from `docs/CONTRACTS.md`
  (JSON-schema enforced, no partial objects, no nulls where the schema requires a value).
- **Given** the attributes screen,
  **when** it renders,
  **then** every field shows the suggested value and **every field is editable**; the seller
  is never blocked by a field the model refused to fill.
- **Given** the seller interacts with **any** field on the attributes screen,
  **when** they confirm,
  **then** the app emits `attributes_edited` with `item_id` and:
  `field_names[]` — **field names only, never values**, constrained to `^[a-z][a-z0-9_]*$` so
  that a value cannot be smuggled in by pasting `"Material: Silk"` as a name;
  `edited_field_count` — fields whose **value changed**;
  `fields_touched` — fields **read or changed** (focus→blur, or an explicit confirmation),
  deduplicated so re-entering one field counts once.
- **Given** the seller touches **nothing** and confirms,
  **then** the event still fires with `fields_touched = 0`, `edited_field_count = 0`.
  *This changed on 2026-10-28:* the original rule was "no change ⇒ no event", which made a
  seller who read all five measurements and accepted them invisible — and would have
  systematically understated the friction we are trying to remove. The distinction we actually
  want is **touched vs untouched**, not changed vs unchanged. `fields_touched = 0` is the
  "blind acceptance" signal we care about, and it is now observable instead of absent.
- **Given** a field the model is unsure about,
  **then** it is flagged in the UI as suggested-not-verified, and measurements are **never**
  written into the listing without the seller having seen them.
- **Given** the seller edits nothing and confirms,
  **then** `attributes_edited` **is still emitted** with `fields_touched = 0` (superseding the
  original "an empty edit is not an edit" rule — see the note above). The metric that
  distinguishes trust from inattention is `fields_touched`, not the existence of the event.

---

### US-4 — On-model render, Pro only (P0 for Pro, hard-gated)

*As a Pro seller, I want the garment shown on a consistent model so that my Depop listings
look like a brand's, without paying a model or a photographer.*

- **Given** a seller on the **Starter** plan,
  **when** they reach the on-model action,
  **then** it is visible but disabled with an upgrade path — the capability must be
  *seen* before it is bought — and no VTON job is created.
- **Given** a seller on **Pro** with an approved cutout,
  **when** they choose size and skin tone and request a render,
  **then** the request uses the chosen parameters and creates a job with `kind = vton`, and
  the `AssetPack` returned **immediately** may carry `on_model_urls: []`. The render is
  **asynchronous and must never block** the review screen (ADR-004, 2026-10-28): we do not
  make a paid seller wait on a render they may not want.
- **Given** a `vton` job that reaches `done`,
  **then** `on_model_urls[]` on the next fetch has **at least one** entry. This invariant is
  asserted on the **completed job**, never on the first fetch.
- **Given** a `vton` job in `queued` or `running`,
  **then** `on_model_urls[]` is `[]` and the client distinguishes "not requested" from "in
  flight" by **reading the job state**, never by the length of the array — the array is `[]`
  in both cases by design.
- **Given** a VTON provider rejection,
  **then** the job ends `failed` with `reason_code = vton_rejected`, `on_model_urls[]` is
  **left untouched** (a failed render must not destroy an asset the seller already has), the
  seller sees a specific explanation and a suggested re-shoot, and **the render is not billed**
  against the 400-item allowance.
- **Given** any single render,
  **then** cost per render stays ≤ **$0.10** (BRIEF §Cost ceilings); the job row records the
  provider and the cost so the ceiling is auditable and not merely believed.

---

### US-5 — Export a CSV that imports cleanly into eBay (P0, eBay only)

*As a seller, I want my finished item as a row in the marketplace's own CSV so that I can bulk
upload instead of posting twenty listings by hand.*

- **Given** an item with approved prettify + attributes,
  **when** the seller taps Export and chooses eBay,
  **then** the app emits `export_clicked` with `item_id` and `marketplace="ebay"`, and
  returns a file whose header row **exactly** matches the eBay template column names from
  `docs/CONTRACTS.md`.
- **Given** the exported file,
  **when** it is opened,
  **then** required columns are non-empty, character limits are respected (title length,
  description), and the category/mapping comes from the contract — not from a guess in the
  mobile client.
- **Given** the marketplace mapping is not yet contracted (Poshmark, Vinted in v1),
  **then** the option is **absent**, not present-and-broken. A broken import costs Dario an
  evening and the subscription (§2.2).
- **Given** the export succeeds,
  **then** the free-item counter increments **exactly once** per item, and re-downloading
  the same CSV does not increment it again.

---

### US-6 — Free tier cap, paywall, subscription (P0)

*As the business, I need the 4th item to be the moment a seller decides, so that a seller who
gets real value pays and a seller who doesn't leaves without friction.*

- **Given** a seller with 0 exported items,
  **then** they can complete the full loop with **no card, no account wall, and no on-model
  upsell**, and the app displays the remaining free count ("3 of 3 free items left") from the
  first screen.
- **Given** a seller completes their 3rd export,
  **when** the export succeeds,
  **then** the soft paywall appears **after** the success state is visible — never
  interrupting it — and emits a paywall impression event.
- **Given** a seller with 3 exports attempts a 4th item,
  **then** the hard paywall blocks the upload before any compute is spent, and the item is
  preserved as a draft so nothing is lost.
- **Given** the seller completes Stripe Checkout,
  **when** the webhook is received and verified,
  **then** the app emits `subscription_started` with `plan` and `price_cents`, the account
  limit updates before the next upload, and their 3 free items remain visible.
- **Given** a Checkout session is abandoned,
  **then** no `subscription_started` is emitted, the draft survives, and the seller is
  returned to the paywall — not to a blank home screen.

---

## 6. v1 scope cut list — what is explicitly NOT in v1, and why

The cut is the product. Every row is a decision with a reason, not a wishlist item.

| Cut | Why NOT in v1 | Revisit when | If built, priority |
|---|---|---|---|
| **Price / sold-comps suggestion** | Needs marketplace sold-data. eBay's API does not serve arbitrary sold comps to us without partner status; scraping breaches terms. **Legal risk + unbudgeted cost**, and §3.3 shows pricing is the single largest remaining block of seller time — so it is the *most valuable* post-v1 item, and we must not fake it with a heuristic. | Legal signs off on a data source AND we have measured attribute accuracy | P1, top |
| **Auto-publish directly to eBay / Poshmark / Depop** | OAuth per seller, per-marketplace rate limits, and we become the liable party for a wrong listing that Googles wrong. The boring CSV already gets the item live and is already the market's habit. Cross-listing is also someone else's incumbent product. | CSV export has shipped and imported cleanly for ≥20 sellers | P2 |
| **Attribute→measurement auto-applied without confirmation** | A wrong pit-to-pit is a **returned item**, i.e. the seller's money, not ours. We have zero calibration data for measurement error. Confidence must be earned before autonomy. | Measurement error measured on ≥200 items with ground truth | P1 |
| **Batch upload (multi-item in one job)** | The job queue supports it, but the bad_photo rate at batch scale is unknown and a 30-item batch that half-fails is a support nightmare on day one. Single-item first, then batch. | `bad_photo` rate measured below 5% | P1 |
| **Lifestyle / scene backgrounds, styled flat-lays** | Generation cost per attempt plus a taste risk we cannot A/B with no users. A neutral backdrop is what catalog images need and is cheap. | Cost per item ≤ $0.03 headroom proven | P2 |
| **Video, 360°, model walking/turning** | Per-render cost blows the $0.10 ceiling (BRIEF), and no marketplace we target surfaces video in search. Zero demand evidence. **UNMEASURED.** | Never in this product line | Not planned |
| **Model choice beyond size + skin tone** (body type, poses, hair, multiple models per item) | Each extra axis multiplies render attempts and therefore cost, against a hard $0.10 ceiling. VTON provider constraints also apply. | Cost per render measured with real accept rate | P2 |
| **Poshmark + Vinted field mapping at eBay parity** | eBay's item specifics are the widest and hardest; getting one right proves the mapper. Shipping three half-correct mappers is how Dario churns. | eBay mapper validated by a real import | P1 |
| **Closet / wardrobe graph, social feed, "stylist" features, virtual closet** | Hard non-goal in BRIEF §Non-goals. Also monetarily wrong: resellers do not pay for organisation, they pay for throughput. | Never | Not planned |
| **Multi-tenant team admin, seats, roles, permissions** | BRIEF §Non-goals. Ingrid's two helpers are a real need but a v2 business decision, not a v1 build. | Post-revenue, when a 400/mo account asks and will pay | P2 |
| **Native iOS/Android, anything beyond Expo** | BRIEF §Non-goals and §Fixed decisions. | Never | Not planned |
| **Self-hosted model training / fine-tuning** | BRIEF §Non-goals. Hosted APIs + one self-hosted fallback. | Never in v1 | Not planned |
| **In-app payments / checkout for the garment sale itself** | BRIEF §Non-goals (no marketplace of its own). We are not becoming a payments business. | Never | Not planned |
| **Marketplace-grade search, saved searches, demand alerts** | Adjacent product. Nobody has asked. | After north star is moving | Not planned |
| **Mobile web / desktop listing UI** | The phone is where the photo is. Two clients doubles the QA surface for the same job. | After mobile retention is proven | P2 |
| **Defect-detection with severity grading** | Ingrid needs it, but a false "no defects" on a stained sleeve is a consumer-protection problem, not a bug. Needs its own accuracy bar and legal review. | Legal + measured precision on a labelled set | P1 |

**Priority pool, condensed** (for `docs/PLAN.md` sequencing — this file does not sequence):

- **P0 (v1, must ship):** upload → prettify → approve → attributes → edit → CSV export (eBay)
  → free cap → paywall → Stripe; `reason_code` surfaced in UI; cost recorded per job.
  Plus on-model for Pro, hard-gated.
- **P1 (post-v1, month 2–3):** comps pricing, batch upload, measurement autonomy, Poshmark/
  Vinted parity, defect grading.
- **P2 (later, only with evidence):** auto-publish, extra model axes, scene backgrounds,
  desktop client.
- **Never:** the BRIEF non-goals above.

---

## 7. Open questions — needing a decision, not more analysis

> **Status as of 2026-10-28:** Q2, Q3, Q4 and Q5 are **closed** (rulings below). Q1 is still
> open and still blocking pricing/GTM. Nothing here was closed by me inventing an answer.

1. **BRIEF's "~8 min" vs this document's 19.5 min (§0, §3).** Which is the number we plan
   against? If 8 is right, the entire value math shrinks by ~2.4× and Maya's segment gets
   thin. Needs: 5 interviews, `INTERVIEWS.md` Q3–Q5. **Blocking for pricing/GTM, not for
   building.** — **OPEN.** Team-lead ruling: both numbers stay unedited, and `<2 min/item` is
   barred from acceptance criteria and customer-facing copy; resolved by data, not by editing.
2. **Is <2 min/item a promise or an aspiration?** — **CLOSED 2026-10-28** (team-lead): an
   aspiration only. It is not an acceptance criterion (end-to-end minutes are not scriptable)
   and it is banned from product pages, store copy, paywall text, ads and sales DMs. The two
   scriptable proxies that replace it are in `METRICS.md §3.3`.
3. **Pro cap = 400 = Ingrid's exact volume (§2.3).** — **CLOSED 2026-10-28** (team-lead,
   ADR-005 §3): no overage feature in v1. The schema reserves the ledger columns
   (`overage_enabled default false`, `overage_unit_price_cents null`) and v1 hard-blocks with
   402. Zero-cost schema headroom, no feature creep. **Whether a tier above Pro exists is
   still a product question with no answer** — it does not block v1.
4. **Where the on-model render sits in the flow for Pro.** — **CLOSED 2026-10-28**
   (ADR-004): **asynchronous, never blocking.** The first `AssetPack` may return
   `on_model_urls: []`; the invariant `job(vton, done) ⇒ on_model_urls.length ≥ 1` is asserted
   on the completed job, and render state is read from the job, not from array length. US-4
   above was amended to match — the two documents now agree.
5. **Does the free tier include on-model?** — **CLOSED 2026-10-28**: no (BRIEF), and the
   control is **visible but disabled** (US-4), not hidden. Visibility is what sells Pro.
