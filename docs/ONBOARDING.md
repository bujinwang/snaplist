# SnapList — ONBOARDING (first 90 seconds)

**Owner:** 许清楚 (Head of Product) · **Date:** 2026-10-28
**Depends on:** `docs/PRD.md` US-1/US-6, `docs/METRICS.md` §2 (activation), §5 (paywall A/B)

**The one sentence this document exists to enforce:** the seller must see *her own garment,
prettified* before she is asked for anything — no card, no email, no account, no plan.

---

## 0. Assumptions this script rests on (declare them, don't bury them)

| # | Assumption | Status | If it's wrong |
|---|---|---|---|
| A1 | Median job latency (upload → prettified image on screen) **≤ 20s**, p90 **≤ 35s** | **UNMEASURED** — no pipeline exists yet to time | If p90 > 35s the 90-second script **breaks at 0:42** and must switch to the "put your phone down" path (§3.2). The script is a latency contract, not just copy. |
| A2 | The quality gate rejects unusable frames client-side in **< 800ms** | **UNMEASURED** | A slow gate feels like a bug; a false reject at first use is fatal. Gate must be tuned for false-negatives (let a bad photo through) over false-positives (block a good one). |
| A3 | A first-time seller can frame a flat-lay from an overlay with no tutorial | **UNMEASURED** — needs ≥5 usability sessions | Falls back to the 3-frame illustrated guide at 0:20. |
| A4 | Anonymous sessions are possible and merge cleanly into a Supabase account at export | **requires architect decision** — see §6 | If not, the signup wall moves to 0:04 and expected activation drops for reasons we will not be able to separate from product quality. |

A1 is the assumption that matters most. **The 90 seconds is a budget the pipeline has to
fit inside, not a description of a pipeline we have.** Write the number in the plan, then
measure it, then adjust the script.

---

## 1. The script — second by second

Total: **90 seconds, 0:00 → 1:30.** Beats of 4–18s, because attention on a first-run screen
decays fast and each beat must earn the next.

| Clock | Screen | What the seller sees | What she does | What we must NOT do |
|---|---|---|---|---|
| **0:00–0:04** | Camera, live | Full-bleed viewfinder. One line: **"Lay it flat. Snap it once."** No logo animation, no splash, no loading spinner | Nothing — the app is already useful | Show a splash screen. Show a paywall. Show a plan comparison. Ask for an account |
| **0:04–0:12** | Camera + inline callout | Callout on the shutter: **"One photo per item. We do the rest — background, measurements, title, CSV."** Then it fades and never returns | Reads one sentence, or ignores it | Make her tap "Next" or "Get started" to reach the camera. The camera *is* the app |
| **0:12–0:20** | OS permission sheet | Camera permission, requested **with** the in-app pre-prompt: **"SnapList needs the camera to shoot your item. Photos stay on your device until you export."** | Taps Allow | Explain the permission *after* asking for it. Ask for notifications, contacts, or tracking here — ever |
| **0:20–0:32** | Camera, guidance overlay | Corner brackets, a horizon line, and a single live hint: **"Fill the frame"** → **"More light"** → **"Hold still"** (one at a time, never stacked) | Frames the garment | Show a 6-screen tutorial. Show a grid of tips. Auto-fire the shutter |
| **0:32–0:37** | Shutter + instant gate | Shutter flash. Then either **"Got it"** (green tick, 300ms) or one corrective line: **"Too dark — try the window"** / **"Garment isn't fully in frame"** | Waits ~2s, or re-shoots | Upload first and *then* reject. Never burn a free item on a rejected frame (PRD US-2) |
| **0:37–0:42** | Upload | A thin progress bar with real bytes: **"Uploading…"**. At completion: **"Saved."** | Waits | Show a fake progress bar that jumps to 90% and stalls. Show the "3 of 3 free items" counter only as a quiet line in the corner |
| **0:42–1:05** | Processing | One meaningful line at a time, tied to real job states (`queued → running`): **"Removing the background…"** → **"Straightening it out…"** → **"Evening the light…"**. Never a bare spinner, never a percentage we cannot back | Waits — this is the only dead air in the flow, so it must narrate | Say "Almost there" when we don't know. Offer an upsell while she waits. Auto-play a video |
| **1:05–1:18** | **Result reveal** | Her garment, catalog-clean, on neutral backdrop. Before/after on a drag handle, **before** on the left by default so the improvement reads as a gain. Buttons: **"Use this"** (primary) / **"Try again"** (secondary, up to 3) | Drags the handle, then taps **Use this** | Auto-approve for her. Hide "Try again". Cover her garment with a modal, a rating prompt, or a "how did we do?" survey |
| **1:18–1:30** | Attributes preview | The attribute card, filled: category, colour, material, era, measurements (flagged "suggested — check"), plus the defects field. Everything editable, nothing blocking. Bottom bar: a quiet **"3 free items left"** and a prominent **"Export"** | Scans it, maybe edits one field | Force her through fields to proceed. Ask her to confirm a measurement she can't check yet. Gate the export behind an upsell |

**End of the first 90 seconds.** She has not signed up, has not paid, has not seen a plan,
and has one real item ready to export. That is the whole design.

### 1.1 Why 90 seconds and not 3 minutes

At 90 seconds she is inside a single task with no context switch. Beyond ~2 minutes the
probability of her putting the phone down to do something else rises, and the session
becomes an "unfinished job" that we then have to win back with a notification — an expensive
recovery for a problem we created. The 90 seconds is therefore a **product constraint on
latency (A1)**, not a marketing number.

---

## 2. Free tier

**3 items, no card, no account, no on-model.** Per BRIEF §Cost ceilings.

| Rule | Detail |
|---|---|
| What counts as an item | One **successful export** (PRD US-5, US-6). A failed job, a rejected frame, or a retry does **not** consume an item |
| Why export and not upload | The limit must sit on the *value delivered*, not on the attempt. Charging for uploads would make `bad_photo` a billing event — the single fastest way to lose a seller |
| On-model | **Visible but disabled** on free and Starter (PRD US-4). Showing a locked door is what sells Pro; hiding it sells nothing |
| Re-downloads | Free. Exporting the same item again does not consume another item |
| Counter visibility | Always visible, quiet, never a modal: "3 of 3 free items left" → "1 of 3" → "0 of 3" |
| At 0 remaining | Hard paywall at the **next upload attempt** (§3.1), existing work preserved |
| Expiry | Free items do **not** expire. A monthly reset would create a farming loop and, worse, teach the seller to wait |

**Honest note for the team:** the free tier is deliberately *not* enough to run a real
resale operation — at the $25/h assumption, 3 items is worth about $13.75 of the seller's
time versus a $19 subscription (`PRD §4.4`). It is a demonstration, not a substitute. Do not
describe it in copy as "free forever" or "everything you need".

---

## 3. Where the paywall appears

**Two touchpoints, in this order. They are different screens with different jobs.**

### 3.1 Hard gate — at the 4th item's **upload attempt**

Fires **before any compute is spent** (PRD US-6), on a seller whose `free_items_exported = 3`.

- The photo she just took is **kept as a local draft** and shown as a thumbnail behind the
  paywall. Nothing she did is lost.
- The paywall is **dismissible**. She returns to her library, her 3 exported items, and her
  drafts. No dead end, no forced modal loop.
- No countdown timer, no "offer expires", no fake scarcity. There is no offer expiring.

### 3.2 Soft nudge — immediately **after** the 3rd export succeeds

The job here is different: this seller **has just got exactly what she paid nothing for**,
and it worked. The paywall must not touch that moment.

- Fires **after** the success state is fully visible and the CSV is confirmed produced.
- Never covers the success state. Slides in below it. Same copy family, softer framing.
- Dismissing it is a single obvious tap, and dismissal **does not re-prompt in the same
  session**.

### 3.3 The two screens ranked

| | Hard gate (4th upload) | Soft nudge (after 3rd export) |
|---|---|---|
| Intent at that moment | Highest — she is mid-task | High, but satisfied and about to leave |
| Risk if handled badly | Losing a warm seller with a draft in limbo | Polluting a success moment |
| Primary metric | `subscription_started ÷ paywall_impression` | same, but measured on 7-day window |
| Variant test | **All 3 copy variants (§4)** | Variant A only, as the control |

**Decision rule:** run the A/B on the hard gate. The soft nudge ships with Variant A copy and
is not tested until the hard gate has a winner — three variants × two placements with our
volume is how you learn nothing confidently (`METRICS.md` §5: ≥200 impressions per arm).

---

## 4. Paywall copy — three variants, exact strings

Each variant is a **different psychological lever**, not a rephrasing. Ship all three behind
a `user_id` hash, stable per user.

Shared rules for every variant: the **dismiss action is always present and always
equally legible**; no fake urgency, no fake scarcity, no invented "join 10,000 sellers"
(we have no users), no fabricated time-saved number; the price and the item count are stated
plainly; nothing claims the free tier expires.

---

### Variant A — Unit economics (`paywall_variant = "a"`)

> **3 done. 97 to go this month.**
>
> Starter is $19/mo for 100 items — 19¢ a listing. Your free 3 are a trial,
> not a tier. Cancel from Settings in two taps.
>
> **`[ Start 100 items — $19/mo ]`**
> `Not now`

*(19¢ derives arithmetically from the BRIEF's own $19 / 100 items. No outside number is
used, and no time-saved claim is made.)*

---

### Variant B — Momentum / task completion (`paywall_variant = "b"`)

> **Your 4th item is already photographed.**
>
> It's saved in drafts — nothing lost either way. Finish it now and it's live
> tonight, or come back when you've got the tape measure out.
>
> **`[ Finish this item — $19/mo ]`**
> `Keep it in drafts`

---

### Variant C — Risk reversal (`paywall_variant = "c"`)

> **Cancel the day you stop listing.**
>
> No contract, no annual lock-in. Your images and CSVs stay downloadable after
> you cancel. $19/mo covers 100 items; Pro is $49 for 400 plus on-model.
>
> **`[ Try Starter — $19/mo ]`**
> `Maybe later`

---

### 4.1 What every variant must NOT say — enforced, not advisory

| Never | Why |
|---|---|
| "You've saved 33 minutes" / any time-saved figure | The minutes-per-item number is an ASSUMPTION (`PRD §0`). Printing it makes an unmeasured claim to a paying customer |
| **"2 minutes per item" / "<2 min" / "list 30 items in an hour" — any per-item time promise** | **Banned from product pages, App Store/Play copy, paywall text, ads, and sales DMs** until `PRD §3` is measured. v1 lands at ~8.5 min/item and even the baseline (~8 or ~19.5 min) is unverified. `<2 min` is a post-v1 aspiration (`PRD §3.3`). *Team-lead ruling, 2026-10-28: it may only appear as an explicitly-marked unverified claim in internal docs, never customer-facing* |
| "Join 10,000 sellers" / star ratings / testimonials | We have no users and no reviews. Fabricated social proof is a legal and trust problem |
| "Offer ends in 09:59" | There is no offer and no clock. Pure dark pattern |
| "Your items will be deleted" | They will not. Loss framing must be **true** (Variant B's drafts claim is true because the draft is persisted) |
| Hiding or greying the dismiss action | The dismiss must be as legible as the CTA. A greyed "Not now" is a dark pattern and a churn source |
| "Unlimited" | Nothing is unlimited. Starter is 100, Pro is 400 |
| Anything about on-model in Variants A/B | On-model is the *Pro* pitch. Putting it on the Starter paywall causes a downgrade in perceived fit and depresses Starter conversion |

---

## 5. Instrumentation hooks this doc requires

From `docs/METRICS.md` §3 and §5 — the parts onboarding owns:

- `upload_started` must carry `attempt_index` and `free_items_remaining` so we can see
  exactly where in the 90 seconds people leave.
- `prettify_approved` must carry `retry_count` — a high retry rate on **item 1** is the
  single clearest signal that the reveal at 1:05 is not convincing.
- `paywall_impression` with `paywall_variant` (`a`/`b`/`c`), `paywall_type` (`soft`/`hard`),
  `free_items_exported`.
- `subscription_started` must carry the same `paywall_variant` so the variant attribution
  survives to revenue and is not just an impression-count story.

**Funnel to watch inside the 90 seconds** — each drop is a different fix:

| Drop | Between | Diagnosis | Fix owner |
|---|---|---|---|
| D1 | 0:00 → 0:32 (never fires a shutter) | Camera or guidance problem | mobile |
| D2 | 0:32 → 0:42 (gate rejects > 1 frame) | Quality gate too strict | mobile + vision |
| D3 | 0:42 → 1:05 (abandons during processing) | Latency (A1) or narration | vision + backend |
| D4 | 1:05 → 1:18 (sees result, taps "Try again" ≥2×) | Prettify quality | vision |
| D5 | 1:18 → export | Attributes screen is too much work | product |

---

## 6. Decisions this doc required from the architect — **all four closed 2026-10-28**

1. **Anonymous sessions — ADOPTED** (ADR-003). `signInAnonymously()` on first launch issues a
   **real JWT** (`is_anonymous=true`), and items / jobs / quota hang off that `auth.users` row
   from the very first photo. At first export, `updateUser` adds the email and Supabase converts
   the row **in place**: `user_id` unchanged, so photos, assets, jobs, quota consumption and
   `session_id` all survive. **No row migration, no merge job, no data-loss path.** The login
   wall is therefore **not at 0:04** — it is at the Nth export, which is exactly what §1 assumes,
   so the 90-second script stands as written.
   *Accepted cost, stated by the architect and repeated here because it is an onboarding
   consequence:* reinstalling the app mints a fresh anonymous identity, so the 3 free items can be
   farmed. Mitigations: device/IP rate limiting, no on-model for anonymous users, and export
   requires a real account. I accept it — 3 items of GPU per reinstall is cheaper than losing an
   honest trial, and gating at the **value exit** rather than at the door is the right trade.
2. **Latency budget — ADOPTED** as a plumbing requirement, and **corrected in one important
   way**: `attrs_latency_ms` is carried on **`prettify_approved`** (the "everything is on screen"
   moment), because a seller who accepts all values never fires `attributes_edited`. Tool time is
   computed from `prettify_approved` + `export_clicked` only — see `METRICS.md §3.3`.
3. **Draft persistence — ADOPTED at P0** (not P1). Written into the DoD: when the 4th item hits
   the paywall, **the photo already taken must remain in the local draft**. The paywall blocks
   the *upload*, not the *camera*.
4. **Paywall variant assignment — ADOPTED**: server-side, sticky, `profiles.paywall_variant`
   written once on first paywall exposure and never re-rolled (ADR-005 §2).
   **Scope note:** this is the attribution pipe only. The three copy variants in §4 are mine and
   may legitimately start out identical — do not wait on copy to build the pipe.

---

## 7. Copy deck summary (for the mobile build — these strings are literal)

| Slot | String |
|---|---|
| Camera headline | `Lay it flat. Snap it once.` |
| Camera callout | `One photo per item. We do the rest — background, measurements, title, CSV.` |
| Permission pre-prompt | `SnapList needs the camera to shoot your item. Photos stay on your device until you export.` |
| Live hints | `Fill the frame` / `More light` / `Hold still` |
| Gate pass | `Got it` |
| Gate reject (dark) | `Too dark — try the window` |
| Gate reject (cropped) | `Garment isn't fully in frame` |
| Processing lines | `Removing the background…` / `Straightening it out…` / `Evening the light…` |
| Reveal buttons | `Use this` / `Try again` |
| Counter | `3 free items left` → `2 free items left` → `1 free item left` → `0 free items left` |
| Measurements flag | `Suggested — check with a tape` |
| Paywall A | see §4 |
| Paywall B | see §4 |
| Paywall C | see §4 |
