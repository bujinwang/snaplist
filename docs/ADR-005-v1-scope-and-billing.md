# ADR-005 — v1 scope cuts and the billing headroom we are not building

- **Status:** accepted
- **Date:** 2026-10-28
- **Deciders:** lead (architecture), product (PRD §0, `docs/METRICS.md`)
- **Affects:** E7 (export scope), E8/E5-T1 (billing schema), E6 (paywall variant persistence)

Four decisions in one ADR because they share one cause: **v1 must not ship a half-right version
of anything that touches money or trust.**

## 1. Export ships eBay only; Poshmark and Vinted are P2

**Decision.** `packages/shared/src/marketplaces/ebay.ts` is in the first vertical slice.
`poshmark.ts` and `vinted.ts` are P2, gated on verifying the real upload path against the live
sites, and may ship as copy/paste assist rather than a file if no bulk path exists.

**Why.** Product's argument, accepted: a mapper that is 80% right produces an import error on a
professional seller's 40-item batch, and that seller does not retry — they churn and tell
people. The eBay CSV is also the only channel through which value leaves the app, which makes it
the measurement point for the north star. A wrong eBay file is a bug we can fix; three wrong
files at once is a reputation.

**Also decided:** the eBay mapper returns either a valid row or a machine-readable
`insufficient_data` reason naming the blocking fields. Refusing one row is always better than
guessing a category id.

**Cost accepted:** Poshmark and Vinted sellers get a weaker v1 (copy/paste assist) even though
BRIEF names both as target marketplaces. Recorded so nobody is surprised at launch.

## 2. Paywall variant is assigned and persisted server-side, sticky per account

**Decision.** `profiles.paywall_variant text not null default 'a'` with a CHECK over
`a|b|c`, assigned once at first sight of a paywall (or at profile creation) and never
reassigned. Every `paywall_impression` and `subscription_started` carries it.

**Why.** If the variant lives in client config or is chosen at render time, then a few weeks
later `subscription_started` cannot be attributed to anything, and the experiment was wasted.
The write is one column on a row we already create.

**Not in scope:** actually building three paywall designs. This is the *attribution* plumbing
only; variant copy is product's, and all three may look identical at first.

## 3. `subscriptions` carries overage columns; v1 does not implement overage billing

**Decision.** Add two nullable columns:

```sql
overage_enabled          boolean not null default false,
overage_unit_price_cents integer null
```

v1 hard-blocks at the plan limit with a 402 `quota_exceeded`. No overage arithmetic, no new
tier, no proration.

**Why.** Pro is 400 items/month and the largest realistic boutique profile is ~400 items/month —
zero headroom for re-lists and returns. The first big customer would hit a wall on day 30.
Adding the columns now costs one migration line; adding them after launch means a migration plus
a backfill plus a pricing conversation under pressure.

**Explicitly rejected:** implementing overage logic now. That is a pricing product, not a
column, and BRIEF caps what v1 may build.

## 4. The visual-language and copy defaults stay boring

Not really a cut — a reminder that E6's screens use the neutral palette in `apps/mobile/App.tsx`
(#F2F1EE paper, #151515 ink) and the failure copy in `CONTRACTS.md` §6. No design system, no
component library beyond React Native primitives. One less dependency to defend in
`docs/DEPS.md`.
