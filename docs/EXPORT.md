# SnapList — Export guide

**Owner:** growth (E7) · **Last updated:** 2026-11-05
**Source of truth:** `packages/shared/src/marketplaces/` (eBay / Poshmark / Vinted mappers) and
`packages/shared/src/listing/copy.ts` (listing copy). Every claim below traces to code in that
directory or to `docs/PLAN.md` / `docs/METRICS.md`.

> **Read this first — what v1 actually ships.**
> v1 exports a **file or copy block you upload or paste yourself**. SnapList has **no marketplace
> API write access** and does **not** list on your behalf. Of the four marketplaces in the brief:
> - **eBay** — a real, upload-ready **File Exchange CSV**. This is the only marketplace with a
>   first-class, byte-verified export in v1 (E7-T1, P0).
> - **Poshmark** and **Vinted** — **copy/paste assist only** in v1. Neither has an official bulk
>   CSV we have verified, so we do **not** ship a file you can bulk-import. Instead we produce a
>   correctly-formatted row you copy field-by-field into their listing form (E7-T2, **P2**,
>   *verify-first* gate — see status table).
> - **Any other marketplace** (Depop, Mercari, Grailed, Facebook Marketplace, …) — the universal
>   **copy-to-clipboard** fallback below.

---

## 0. The export surface (what the app produces per item)

For every item, the app builds an `ExportBatch` (`packages/shared/src/marketplaces/index.ts`):

- a **prettified image** and any on-model renders,
- **detected attributes** (brand, colour, material, size, condition, measurements, defects),
- **listing copy** — a title (≤ 80 chars), a structured description with a measurements block,
  and up to 10 keywords (`listing/copy.ts`). The copy generator **never** states a brand or fibre
  the item does not have,
- and a **per-marketplace row** with a `requires_manual_columns` list naming exactly which columns
  you still must fill before the file is uploadable.

A row either **exports** or comes back **refused** naming the blocking field (e.g. `brand`,
`prettified_url`) — we never guess a value to fill a required column.

---

## 1. eBay — full export (ships in v1)

eBay is the only marketplace with a verified, upload-ready file. The mapper
(`marketplaces/ebay.ts`) emits a **File Exchange flat file** whose header line is byte-identical
to eBay's own template.

### Steps a seller follows

1. **Prep the item in SnapList.** Upload a phone photo → prettify → review the detected attributes.
   Give the item a **price** (you set it; see "manual" below).
2. **Tap Export → eBay.** The app returns a `.csv` whose first line is exactly:
   ```
   *Action(SiteID=US|Country=US|Currency=USD|Version=745|CC=UTF-8)
   ```
   followed by the columns: `*Category`, `*Title`, `*Description`, `*ConditionID`, `*Quantity`,
   `*StartPrice`, `*Duration`, `PicURL`, `C:Brand`, `C:Type`, `C:Size`, `C:Color`, `C:Material`,
   `C:Pattern`.
3. **Fill anything the app flagged.** The export response lists `requires_manual_columns`. In
   practice that is usually `*Category` and/or `*StartPrice` (see below).
4. **Upload on eBay.** Seller Hub → Listings → **File Exchange / bulk upload** → import the CSV.

### What we automate

- Background removal, straightening, even lighting (the image in `PicURL`, `|`-joined with any
  on-model renders).
- Title (≤ 80 chars, keyword-front-loaded, not SHOUTING) and description with measurements —
  from `listing/copy.ts`.
- Condition → eBay's `*ConditionID` enum (see mapping note).
- Item specifics: `C:Brand`, `C:Type`, `C:Size`, `C:Color`, `C:Material`, `C:Pattern` from the
  detected attributes.
- `*Quantity = 1` (resale, one of each) and `*Duration = GTC` (Good 'Til Cancelled).

### What we deliberately do NOT automate (manual)

- **eBay category ID (`*Category`).** Resolving our vocabulary to eBay's numeric taxonomy needs
  their Taxonomy API + credentials we do not have. A guessed id lists the item in the wrong
  department. So the column is left **empty** and `ebay_category_id` appears in
  `requires_manual_columns` until you supply a verified id. (When a verified id exists it is filled
  automatically and drops out of the manual list — no code change needed.)
- **Price (`*StartPrice`).** The price is **your** number. We carry whatever you entered per item;
  if you left it blank, `ebay_start_price` is flagged and the cell is empty. A batch-level price
  would silently price every garment the same, so price is strictly per item.
- **Shipping / business policies.** Never a column we fill — your seller defaults live in eBay.
- **Auctions vs fixed-price.** We emit `*Action=Add` fixed-price listings. Auctions are manual.

### Honest caveats (all in `ebay.ts`)

- `*Version=745` in the action header is **unverified** against eBay's release notes — it is what
  the common template generator emits today. If eBay rejects the version, edit that one cell.
- The `C:*` item-specific column names (`C:Brand`, etc.) are **unverified** — we have not fetched
  a primary source confirming the exact spelling/prefix.
- **Condition is conservative.** Our four used grades all collapse onto eBay's single
  "Pre-owned" id (`3000`); `new_with_tags` → `1000`, `new_without_tags` → `1500`, `poor` → `7000`.
  Finer used grades are not confirmed against a primary source, so the nuance goes in the
  description, which we write.
- Refusals: a row with **no attributes** (pipeline hasn't run) or **no photo** is refused, not
  exported — eBay rejects a listing without `PicURL`, and we'd rather tell you here.

---

## 2. Universal fallback — copy to clipboard (works for every marketplace)

This is the escape hatch that covers **any** marketplace with a text box, including ones we have
no mapper for (Depop, Mercari, Grailed, Facebook Marketplace, …).

### Steps

1. In the app, open the item and tap **Copy listing**.
2. SnapList copies three blocks to your clipboard: the **title**, the **description** (with the
   measurements block), and the **keywords**.
3. On the marketplace's manual listing form, **paste** the title into the title field and the
   description into the description field; add the keywords to the tags/search-terms field if it
   has one. Upload the prettified image(s) yourself.

### What we automate
The title, description, and keywords — generated only from attributes the item actually has, so
nothing is invented.

### What's manual
Account creation, photo upload to that site, category selection, price, shipping, and publishing.
This is plain copy/paste; there is no file and no import step.

---

## 3. Poshmark — copy/paste assist (NOT a bulk upload) · template drafted, live verification pending

**Status: `template drafted, live verification pending` (E7-T2, P2).** Poshmark publishes **no
official bulk CSV upload** path (recorded in `docs/CONTRACTS.md` §5). The mapper
(`marketplaces/poshmark.ts`) therefore does **not** pretend to produce an importable file. It
produces a correctly-escaped row you copy **field-by-field** into Poshmark's listing form. Every
column is marked `unverified` in the code because it is modelled on the listing form, not on a
template we downloaded.

### Steps

1. Prep the item and tap **Export → Poshmark**.
2. The app shows a row with columns: `Title`, `Description`, `Category`, `Subcategory`, `Brand`,
   `Size`, `Color`, `Condition`, `Original Price`, `Price`, `Photos`.
3. Create the listing on Poshmark and copy each field across. `Brand` and `Price` must come from
   you (see below).

### What we automate
Title, description (with measurements), category/subcategory, colour, condition (mapped to
Poshmark's four-value set), and a `|`-joined photo list.

### What's manual / honest
- **Brand is required.** If it's missing we *refuse* the row and ask you — we never invent a brand.
- **Price** is your number (same input that fills eBay's `*StartPrice`); if blank, `poshmark_price`
  is flagged.
- **Condition** collapses our six grades → Poshmark's four (`poor` joins `fair`, because
  overstating condition is the expensive mistake).
- `Original Price` is a strikethrough anchor, not required — left blank, never a manual column.
- The batch carries a `poshmark_no_official_bulk_csv` warning so the client can tell you up front
  that this is copy-assist, not an import.

---

## 4. Vinted — copy/paste assist (NOT a bulk upload) · template drafted, live verification pending

**Status: `template drafted, live verification pending` (E7-T2, P2).** Vinted's bulk-upload
availability varies by country and is **unverified** (`docs/CONTRACTS.md` §5). Same posture as
Poshmark: the mapper (`marketplaces/vinted.ts`) emits a row to copy into Vinted's form, not a file
to import. All columns are `unverified`.

### Steps

1. Prep the item and tap **Export → Vinted**.
2. The app shows a row with columns: `Title`, `Description`, `Category`, `Brand`, `Size`,
   `Condition`, `Colour`, `Material`, `Package size`.
3. Create the listing on Vinted and copy each field across.

### What we automate
Title (Vinted's shorter 60-char ceiling), description (with the required measurements block),
category, brand, size, condition (mapped to Vinted's five-value set), primary colour, and any
recognised fibres.

### What's manual / honest
- **Brand and Size are required** — refused if missing; never invented.
- **Package size** is a shipping decision we cannot derive, so it is *always* empty and
  `vinted_package_size` is always flagged.
- **Material** is mapped only through Vinted's controlled fibre vocabulary
  (`VINTED_MATERIALS`); an unknown fibre is **dropped** from the column (not smuggled in) and
  still reaches the buyer via the description.
- The batch carries a `vinted_bulk_upload_unverified` warning.

---

## 5. What we deliberately do NOT automate (across all marketplaces)

- **We do not list on your behalf.** No marketplace API write access in v1; you upload/paste the
  export. (`export_delivered` = a file was produced and downloaded, **not** that the item went
  live — see `docs/METRICS.md` §1.1.)
- **We do not set your price.** Comps-based pricing (`listing/pricing.ts`) is **P2** — v1 does
  **not** suggest prices. Fewer than 5 comparable sold listings returns `insufficient_data`; we
  will not guess. Pricing is your call.
- **We do not pick eBay categories.** No Taxonomy API/credentials; the column is left for you.
- **We do not verify the Poshmark/Vinted live upload paths.** Both ship as copy-assist until the
  template is verified against the live site (E7-T2 gate).
- **We do not run auctions, offers, or promotions.** eBay output is fixed-price `GTC`.
- **We never invent attributes.** Brand/fibre absent from detection will not appear in the copy.

---

## 6. Status table

| Marketplace | v1 delivery | Verified? | Manual columns you fill | Notes |
|---|---|---|---|---|
| **eBay** | File Exchange CSV (upload) | Header line byte-verified to eBay template (E7-T1) | `*Category`, `*StartPrice` (when blank) | `*Version=745` & `C:*` columns unverified |
| **Any site** | Copy title/description/keywords to clipboard | Copy block from `listing/copy.ts` | Everything on the site | Universal fallback |
| **Poshmark** | Copy-assist row (paste) | **Template drafted, live verification pending** (P2) | Brand, Price, all listing fields | No official bulk CSV |
| **Vinted** | Copy-assist row (paste) | **Template drafted, live verification pending** (P2) | Brand, Size, Package size, all listing fields | Bulk upload unverified per country |

**No time-saved claim is made anywhere in this doc.** Per `docs/METRICS.md` there are no measured
values yet; the manual baseline (~8–19 min/item, `PRD §3`) is an **unverified** estimate, and our
per-item tool-time target (p50 ≤ 35 s / p90 ≤ 60 s) is a chosen engineering threshold that
excludes your review time — not a customer-facing promise.
