# App Store Review Risk Memo — Payments & On-Model Imagery

> **DRAFT — not legal advice.** Prepared for Bert and the mobile role (E6-T4). Analyzes two
> App Review risks for SnapList: (1) selling the Pro subscription outside Apple's in-app
> purchase system, and (2) generating "person" imagery via on-model rendering. This memo is the
> authoritative source; `apps/mobile` MOBILE.md must **quote it**, not re-invent it.
>
> Draft prepared: 2026-11-06.

---

## 1. The payment decision we are making

**Intended approach (v1): sell Starter/Pro via Stripe Checkout, opened in an in-app browser.**
This is the team's stated decision (BRIEF §"Fixed technical decisions": "Payments — Stripe
Checkout + webhooks"; MOBILE prompt §7: "Purchase: Stripe Checkout via in-app browser"). Reasons:
Stripe is the single billing system across web and mobile, the plans are also sold on
`apps/web`, and avoiding StoreKit keeps one source of subscription truth and avoids Apple's 15–30%
commission on a B2B reseller tool.

**The risk this creates:** Apple's App Review Guidelines treat digital goods/services sold
*inside an app* as requiring StoreKit In-App Purchase (IAP) in most cases.

### 1.1 Guideline citations

- **Guideline 3.1.1 (In-App Purchase):** "If you want to unlock features or functionality within
  your app… you must use in-app purchase." A Pro unlock that enables on-model + higher quota is
  exactly the kind of "unlock features" Apple scrutinizes.
- **Guideline 3.1.3(a) (Reader apps / "other purchase methods"):** permits linking out to an
  external purchase mechanism **only** with the **StoreKit External Purchase Link Entitlement**
  (US) or the **StoreKit External Purchase Entitlement** (EU, under the DMA). Without the
  entitlement, presenting a Stripe link is a 3.1.1 rejection.
- **Guideline 3.1.3(b) (NFT/other):** not our case.
- **Guideline 5.1.1 / 3.1.2:** relevant if we also collect payment info in-app (we do not — the
  card form is Stripe-hosted; E8-T1, no PCI scope beyond redirect).

### 1.2 The decision and the rejection risk we accept

| Option | What it is | Risk |
|--------|-----------|------|
| **A. Stripe Checkout + External Purchase Link Entitlement (US) / External Purchase Entitlement (EU)** | our intended v1 | **Medium-High.** Entitlement must be applied for and granted; US entitlement still requires the "scare screen" and a fee to Apple (27%/12%). Rejection possible if entitlement not granted or wording non-compliant. |
| B. StoreKit IAP | use Apple's IAP for the iOS app, Stripe for web | **Low review risk**, but dual billing systems, commission cost, and reconciliation work. |
| C. Stripe only, no entitlement | **non-compliant** | **High** — near-certain 3.1.1 rejection. Do **not** ship this. |

**Decision recorded here (for mobile to quote):** v1 ships **Option A** — Stripe Checkout in an
in-app browser, with the appropriate external-purchase entitlement applied for. We **accept** the
rejection risk that the entitlement is delayed/denied, and we name **Option B (StoreKit IAP)** as
the fallback if App Review rejects A.

> **UNVERIFIED / [COUNSEL]:** The *exact* entitlement to apply for (US StoreKit External Purchase
> Link Entitlement vs EU DMA entitlement vs both), the required "scare screen" wording, and
> whether SnapList qualifies as a "reader" under 3.1.3(a) are **not yet determined** and need
> counsel + an Apple engagement before submission. This memo flags the decision as *intended*, not
> *final*. Do not treat "we'll use Stripe" as compliance on its own.

---

## 2. On-model "person" generation risk (R8)

SnapList generates an image of a **person** (the model) wearing the seller's garment. Even though
our default model is **synthetic** (no real likeness — see `docs/LEGAL.md` §0.1), App Review and
regulators still treat AI-generated persons as a sensitive category.

### 2.1 Relevant Guidelines / rules

- **Guideline 5.1 (Privacy):** data collection/disclosure. Our consent copy
  (`docs/templates/consent-copy.md`) is shown **before** the first render and discloses the
  synthetic nature — this directly addresses 5.1's "clear disclosure" expectation for
  user-facing processing.
- **Guideline 5.2.5 (Developer Code of Conduct — AI-generated content):** apps must clearly
  disclose when content is AI-generated and must not generate misleading impersonations. Our
  synthetic default + consent disclosure + export note satisfy the "clearly disclosed" bar; we
  must **not** let a real-person reference slip in without a release (see `LEGAL.md` R2).
- **Impersonation (Guideline 5.1 / 5.2):** generating a recognizable real person without consent
  is a rejection and a legal risk. Mitigated by synthetic-only defaults + the `model_refs`
  consent constraint.
- **EU AI Act (Art. 50):** obligations to label synthetic content and (for deepfake-style
  manipulable media) disclose. Our synthetic model images should carry a machine-readable / visible
  disclosure in export; **UNVERIFIED** how we surface this on exported listings — owner: growth.

### 2.2 Mitigation shipped / planned

- Synthetic-by-default model references (`model_refs.is_synthetic = true`) — **SHIPPED** in schema.
- Pre-render consent copy, shown before first render — **SHIPPED** (template) / wired in E6-T4.
- No real-person reference insertable without a release — **SHIPPED** (DB constraint).
- Export-side synthetic-media disclosure — **OPEN** (growth, EXPORT.md / AI Act).

---

## 3. What mobile must do (actionable)

1. Quote this memo's payment decision (Option A + Option B fallback) in MOBILE.md; do not
   paraphrase the guideline numbers.
2. Implement the on-model consent from `docs/templates/consent-copy.md` **before** the first
   render, verbatim.
3. Apply for the external-purchase entitlement **before** submission; have Option B (StoreKit IAP)
   scoped as the fallback so a rejection is not a launch blocker.
4. Confirm with legal whether the US "reader" carve-out (3.1.3(a)) applies to SnapList
   **[COUNSEL]**.

---

*See also: `docs/LEGAL.md` §7 (App Store), `docs/templates/consent-copy.md`,
`docs/templates/model-release.md`. Not legal advice.*
