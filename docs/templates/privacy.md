# SnapList — Privacy Policy

> **DRAFT — not legal advice.** Prepared for Bert and external counsel review. Items marked
> `[COUNSEL]` require a decision before launch. The data map, retention table, and subprocessor
> list mirror `docs/LEGAL.md` §6; if that register changes, change this document with it.

Version: 0.1 (draft) · Last updated: 2026-11-06

## 1. Summary

We process garment photos to produce listing assets. We do not sell personal data. We do not
create or store face embeddings. You can delete your items and your account, and deletion is a
real operation, not a support-email promise.

## 2. What we collect

| Data | Where it lives | Why |
|------|----------------|-----|
| Garment upload (source photo) | Supabase Storage | to produce the asset pack |
| Prettified / cutout / on-model PNGs | Supabase Storage (private bucket, signed URLs) | the delivered asset |
| Item attributes (JSON) | Postgres `items` / `assets` | listing copy and export |
| Analytics events | Postgres `events` | product improvement; no free-text PII column |
| Auth identity | Supabase Auth (`profiles`) | account and quota |
| Model references | Postgres `model_refs` + Storage | on-model rendering (synthetic by default) |

## 3. Why we process it

- **Provide the service** (contract): image processing, attribute extraction, copy, export.
- **Secure and debug** (legitimate interest): abuse prevention, error diagnosis.
- **Improve the product** (legitimate interest): the frozen analytics vocabulary in
  `docs/CONTRACTS.md` §7 — field names and counts, never free text.
- **Payments**: Stripe processes card data; we never see full card details.

## 4. Photos may contain people

A garment photo may show your hands, arms, or face. The segment/matte step removes the person
from the stored cutout and the prettified output, and we do **not** compute or store biometric
templates. An explicit upload-time face/skin warning is not yet built — see `docs/LEGAL.md` R1
(OPEN, owner: vision). Do not upload images of other people without their consent.

## 5. On-model images

Default model references are **synthetic**: no real person's likeness is used. The app shows a
verbatim consent before the first render (`docs/templates/consent-copy.md`). If real-person
references are ever enabled, the database requires a signed release before the reference can be
inserted (`model_refs` consent constraint).

## 6. Subprocessors

| Subprocessor | Purpose | Region / transfer |
|--------------|---------|-------------------|
| Supabase (Postgres + Storage + Auth) | DB, files, auth | US-hosted by default; EU region available `[COUNSEL/UNVERIFIED]` |
| Stripe | Payments | US; PCI scope limited to redirect |
| FASHN | On-model render | **not yet integrated (E4 stub)**; cross-border transfer when live |
| Modal | GPU job execution | US; processes garment cutouts |
| VLM provider (OpenAI-or-equivalent) | Attribute extraction | region per provider `[COUNSEL]` |

Cross-border transfer mechanism (SCCs vs adequacy) is `[COUNSEL]` and unverified until DPA review.

## 7. Retention

- Source photos, derived images, and attributes: until you delete the item or your account.
- Auth identity: for the life of the account.
- Analytics events: `[COUNSEL: set a window — not yet decided; see LEGAL §8.4]`.
- Model references: until deleted.

## 8. Your rights

Depending on where you live (GDPR, CCPA/CPRA, PIPEDA, Quebec Law 25), you may have rights to
access, correct, delete, port, or object to processing. Deletion removes Storage objects, DB rows,
and any eval fixtures derived from your image. DSAR intake: `[privacy@]` with identity
verification. `[COUNSEL: statutory response timelines and any jurisdiction-specific forms.]`

## 9. Security

Private storage buckets, signed URLs, encrypted transport, and least-privilege keys. No system is
perfect; we will notify affected users of a breach as required by law.

## 10. Children

The service is not for anyone under 18. We do not knowingly collect data from minors.

## 11. Changes and contact

We will post material changes here and, where required, notify you. `[privacy@]` — set at launch.

---

*Draft prepared by the engineering team for counsel review. Not legal advice.*
