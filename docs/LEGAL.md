# SnapList — Legal & Trust Risk Register

> **DRAFT — not legal advice.** This document is an engineering-facing risk register prepared
> for Bert (product/lead) and for external counsel review. It states *risk categories* and the
> *mitigations shipped in code*. It does not assert legal conclusions. Items that depend on a
> specific statute's interpretation are marked **[COUNSEL]** or **UNVERIFIED** rather than
> glossed over. Owners are role names from `docs/PLAN.md`.
>
> Draft prepared: 2026-11-06. Audience: Bert. Scope: Epic E9 (legal), tickets E9-T1…T4.

---

## 0. What the product actually does (for counsel)

SnapList is a paid tool for resale sellers. A seller photographs a **garment** with the phone
app. The pipeline:

1. **Segment / matte** (`packages/vision/segment.py`, `matte.py`) — isolate the garment from the
   background and from any body part holding it; produce a transparent cutout PNG.
2. **Prettify** (`prettify.py`) — neutral backdrop, de-wrinkle, white balance. Output a flat-lay.
3. **Attributes** (`attrs.py`) — a VLM reads category/color/pattern/material etc. into JSON.
4. **On-model ("virtual try-on")** (`packages/vision/vton.py`) — render the garment cutout onto a
   **model reference image** chosen by the seller, producing an "on-model" shot.

**The on-model stage (E4) is currently a STUB.** `packages/vision/vton.py::get_adapter` raises
`StageNotImplemented`; no real render is produced and no FASHN/CatVTON call is made. Real renders
are **BLOCKED** on `FASHN_API_KEY` and the Modal token (`infra/modal_vton.py`). **Nothing in this
document should be read as describing a shipping on-model feature.** We are documenting the
*intended* flow and its open risks honestly, not claiming a working capability.

### 0.1 Model-image provenance — stated plainly (acceptance requirement)

We render garments onto **model images we did not photograph and do not own the underlying
likeness of, by default**. Concretely:

- The system's default model references are **synthetic** — AI-generated persons with no real
  individual's likeness. This is encoded as the DB default: `model_refs.is_synthetic = true`
  (`infra/migrations/0001_init.sql`). A synthetic reference requires **no** release.
- The schema *also* permits a real-person reference (`is_synthetic = false`). The migration
  enforces that such a reference **cannot be inserted without a signed release**
  (`consent_signed_at` and `consent_document_version` are NOT NULL when `is_synthetic` is false —
  constraint `model_refs_consent_or_synthetic` / `model_refs_consent_needs_a_version`). This is
  the legal guard: a missing release makes the row un-insertable, so a real likeness cannot ship
  silently.
- Sellers may in principle supply their own model reference images (`model_refs.user_id` is
  per-seller). Any real-person reference a seller uploads is covered by the same consent
  constraint and by the release template in `docs/templates/model-release.md`.

**What we do about the real-person risk:** (a) synthetic-by-default so the common path touches no
real likeness; (b) a hard DB constraint that refuses real-person references without a release on
file; (c) in-app consent copy shown *before* the first on-model render
(`docs/templates/consent-copy.md`); (d) a model-release template for any deliberately-licensed
real model.

> **UNVERIFIED / OPEN:** The 6 shipped *default* model references are intended synthetic, but the
> seed data and the exact generation pipeline for those synthetic persons are not yet built
> (they live with E4). Confirm before launch that the defaults are `is_synthetic = true` and that
> no real stock photo slips in without a release. See risk R2.

---

## 1. Risk register

Legend for **Status**: `SHIPPED` (mitigation is in code/schema today), `PARTIAL` (in progress),
`OPEN` (no mitigation yet, owner assigned), `UNVERIFIED` (mitigation exists but unproven / needs
counsel).

| ID | Risk | Domain | Jurisdictions | Likelihood | Mitigation (file / role) | Owner | Status |
|----|------|--------|--------------|-----------|--------------------------|-------|--------|
| R1 | Person in garment upload (face/hands) → biometric / special-category data | Privacy / Biometrics | US (IL BIPA, TX CUBI, WA, CCPA/CPRA), CAN (PIPEDA, QC Law 25), EU (GDPR Art. 9) | Medium | Segment/matte strips the person from output; original not retained with person; no face embeddings stored; consent copy + privacy policy (`segment.py`, `consent-copy.md`, `templates/privacy.md` — privacy.md is E9-T3, *not in this ticket's four files*) | vision / legal | PARTIAL |
| R2 | Real-person model likeness used without release | IP / Publicity / Biometrics | US (right of publicity, state biometric), EU (GDPR Art. 9, Art. 22) | Low (synthetic default) | `model_refs` consent constraint + synthetic default (`0001_init.sql`); release template (`templates/model-release.md`) | legal / backend | SHIPPED (guard) / UNVERIFIED (seed data) |
| R3 | AI-generated "person" imagery — disclosure & deepfake rules | Regulatory / Platform | EU (AI Act Art. 50 synthetic-content labeling), platform policy | Medium | Synthetic-only default; consent copy discloses synthetic model; export watermark/TOS note | legal / growth | PARTIAL |
| R4 | Trademark / brand in generated copy or render (invented logos) | IP | All | Medium | VLM brand only when evidenced; copy generator forbids brand not in attributes; render mask never invents logos (`attrs.py`, `prettify.py`) | vision / growth | SHIPPED (guard) |
| R5 | Marketplace ToS breach (automated listing/login on seller's behalf) | Contract | US/CAN/EU (eBay/Poshmark/Vinted T&Cs) | Low (CSV only) | Export is a CSV the *seller* uploads; no automated login; UI wording in `EXPORT.md` (E7-T6) | growth | PARTIAL |
| R6 | Data retention / deletion not actually deleting | Privacy | GDPR Art. 17, CCPA deletion, QC Law 25 | Medium | Deletion op deletes Storage objects + DB rows + derived eval fixtures; DSAR procedure (`storage.py`, `E5-T1`) | backend / legal | PARTIAL |
| R7 | App Store rejection — digital subscription sold outside IAP | Platform / Contract | Apple App Store (Guideline 3.1.1, 3.1.3(a)) | Medium-High | Decision: Stripe Checkout via in-app browser; see `APPSTORE-RISK.md`; external-purchase entitlement [COUNSEL] | mobile / legal | OPEN |
| R8 | App Store rejection — generated "person" without consent/disclosure | Platform | Apple Guideline 5.1, 5.2.5; EU AI Act | Low-Medium | Synthetic default + pre-render consent copy + no real likeness | mobile / legal | PARTIAL |
| R9 | Subprocessor / cross-border data (Supabase, Modal, FASHN, Stripe, VLM) | Privacy | GDPR Ch. V, PIPEDA | Medium | DPA/subprocessor table + region mapping (§6); SCCs [COUNSEL] | legal / backend | PARTIAL |

---

## 2. Likeness & biometric exposure (R1, R2)

**What we collect.** Sellers upload **garment** photos. A garment photo frequently contains the
seller's own hands, arms, or face (someone wearing or holding the item). Under several regimes a
photograph from which biometric identifiers can be derived is "biometric information," and a
*photograph* of a person is a potential special-category (GDPR Art. 9) or biometric (IL BIPA,
TX CUBI) data subject.

**Intended mitigation (shipped in code).**
- The `segment` + `matte` pipeline isolates the garment and returns a **transparent cutout**; the
  person holding the garment is not in the stored asset (`packages/vision/segment.py`,
  `matte.py`). The prettified output is a flat-lay with a neutral backdrop — no person.
- We **do not** compute or store face embeddings, faceprints, or any biometric template. The VLM
  (`attrs.py`) returns garment attributes only.
- The original upload is retained only as long as needed for the job and is covered by the
  retention/deletion policy (R6). Counsel should confirm the retention window **[COUNSEL]**.

**Real-model likeness (R2).** If a real-person model reference is ever enabled, the `model_refs`
constraint blocks insertion without `consent_signed_at` + `consent_document_version`. The release
template (`docs/templates/model-release.md`) is the instrument that satisfies that constraint.
Until then the default is synthetic and no release is needed.

> **OPEN:** We have not yet implemented explicit face/skin *detection-and-reject* on upload (the
> 08-legal prompt asks for "detect faces/skin in uploads and strip or reject"). The segment/matte
> step removes the person downstream, but an explicit pre-check that warns the seller ("a person
> is visible — they will be removed") is not built. Owner: vision. Track separately.

---

## 3. Model-image provenance (R2, R3)

See §0.1. Summary for counsel:

- **Default = synthetic persons.** No real individual's likeness, no release needed, lowest risk.
- **Opt-in real persons** (our licensed stock, or a seller's own upload) are gated by a DB
  constraint requiring a signed release and a consent-document version. This is the only path
  that touches real likeness, and it is fail-closed (cannot insert without the release).
- **Consent model.** Two consent surfaces:
  1. *ToS + Privacy* accepted at account creation / first export (E9-T3, separate ticket) —
     covers processing of uploads.
  2. *In-app on-model consent* shown **before the first render** — see
     `docs/templates/consent-copy.md`. The app quotes this verbatim; it states the model is
     synthetic, licensed to us, and no real person's likeness is used.

---

## 4. Generated-image rights & trademark (R4)

- **Seller ownership:** the seller owns their garment photo and the resulting listing asset they
  export. We grant a licence to process it; we do not claim ownership of the seller's garment IP.
- **Our ownership:** we own the synthetic model references and the rendering pipeline. The
  *composition* (garment-on-synthetic-model) is a generated image; we license its use to the
  seller for their listing.
- **Trademark rule:** generated renders must **not** invent brand logos or marks. The
  prettify inpaint mask never overlaps the logo/text region (`prettify.py` unit test). Brand
  names in listing copy appear only when the VLM found evidence in the image (`attrs.py`,
  `extra="forbid"` schema); the copy generator (`packages/shared/src/copy.ts`) forbids asserting
  a `brand` that is absent from `ItemAttributes`. Brand names are **user-asserted**, not our
  claims — the UI/ToS must say so.

---

## 5. Marketplace ToS (R5)

Our export writes a **CSV the seller uploads themselves** to eBay (v1; Poshmark/Vinted are P2
per ADR-005 §1). We do **not** log into a marketplace on the seller's behalf, do not automate
listing creation through their web session, and do not store marketplace credentials. The UI
wording that keeps us out of breach is owned by growth in `docs/EXPORT.md` (E7-T6) and should be
quoted verbatim there. This memo does not restate it; flag to growth if EXPORT.md is missing.

---

## 6. Privacy / data map & subprocessors (R6, R9)

**Data we store** (source: `infra/migrations/0001_init.sql`, `apps/api/schemas.py`):

| Data | Where | Retention | Notes |
|------|-------|-----------|-------|
| Garment upload (source photo) | Supabase Storage | until item deleted / job TTL | removed person downstream |
| Prettified / cutout / on-model PNGs | Supabase Storage | until item deleted | signed URLs, private bucket |
| Item attributes (JSON) | Postgres `items`/`assets` | until item deleted | no PII fields |
| Analytics events | Postgres `events` | see `CONTRACTS.md` §7 | no free-text PII column |
| Auth identity | Supabase Auth (`profiles`) | account life | anon→identified at export (ADR-003) |
| Model references | Postgres `model_refs` + Storage | until deleted | synthetic default; release if real |

**Deletion** must be a real operation: delete Storage objects + DB rows + any eval fixtures
derived from the user's image (`apps/api/storage.py`, `E5-T1`). A support-email "please delete
me" is not acceptable (acceptance, E9-T3). DSAR intake procedure to be defined in privacy.md.

**Subprocessor / DPA table (R9):**

| Subprocessor | Purpose | Region / transfer note |
|--------------|---------|------------------------|
| Supabase (Postgres + Storage + Auth) | DB, file storage, auth | US-hosted by default; EU region available **[COUNSEL/UNVERIFIED]** |
| Stripe | Payments | US; PCI scope limited to redirect (E8-T1) |
| FASHN | On-model render (VTON) | **not yet integrated — E4 stub**; cross-border transfer when live |
| Modal | GPU job execution (incl. CatVTON) | US; processes garment cutouts |
| VLM provider (OpenAI-or-equivalent, ADR-006) | Attribute extraction | region per provider **[COUNSEL]** |

Cross-border transfer mechanisms (SCCs / adequacy) are **[COUNSEL]** and UNVERIFIED until DPA
review.

---

## 7. App Store (R7, R8)

Full analysis in `docs/APPSTORE-RISK.md` (E9-T4). Short version: v1 intends to sell the Pro
subscription via **Stripe Checkout in an in-app browser**, not StoreKit IAP. This carries a real
Guideline 3.1.1 / 3.1.3(a) rejection risk; the fallback (StoreKit IAP, or an external-purchase
entitlement) is documented there. The on-model "person" generation risk (Guideline 5.1, 5.2.5)
is mitigated by the synthetic default + pre-render consent copy. The mobile role's risk section
(`apps/mobile` MOBILE.md) must **quote APPSTORE-RISK.md**, not re-invent it.

---

## 8. Open questions / UNVERIFIED

1. **Seed model references** — confirm the 6 shipped defaults are `is_synthetic = true` and not
   real stock photos (R2). Owner: vton / backend.
2. **Upload face/skin pre-check** — not yet built; segment/matte removes the person but no
   explicit warn/reject (R1). Owner: vision.
3. **Stripe-vs-IAP final compliance path** — which entitlement (US StoreKit External Purchase
   Link; EU DMA external purchase) or fallback to IAP. **[COUNSEL]** — see APPSTORE-RISK.md.
4. **Retention windows** for source photos and events — values not yet set; needs counsel (R6).
5. **Cross-border transfer mechanism** for Supabase/VLM/FASHN/Modal — SCCs vs adequacy
   **[COUNSEL]** (R9).
6. **ToS + Privacy drafts** (E9-T3) live in `docs/templates/terms.md` and `privacy.md` — out of
   scope for this ticket's four files but required before launch; this register assumes they will
   exist and name the processors/retention above.

---

*Prepared by: eng-e9-legal (Engineer / product-counsel role). For review by Bert and external
counsel. Not legal advice.*
