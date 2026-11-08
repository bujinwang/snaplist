# HANDOFF — product

**Role:** 许清楚 (Head of Product) · **Date:** 2026-10-28T16:28Z
**Scope this run:** `docs/PRD.md`, `docs/METRICS.md`, `docs/ONBOARDING.md`, `docs/INTERVIEWS.md`.
No code, no dependencies, no files outside these four + this handoff.

---

## 1. What shipped

| File | What it contains | State |
|---|---|---|
| `docs/PRD.md` | ICP, 3 personas (20/120/400 per month), JTBD, **minute-by-minute time budget** (8 steps, 19.50 min baseline → 8.50 min with v1, **11.00 min saved**), value math with arithmetic shown at **two plan price points and three hourly values**, break-even and sensitivity, 6 Given/When/Then user stories with acceptance criteria, 16-row v1 cut list with a reason per row, 5 open questions | complete |
| `docs/METRICS.md` | North star (items exported per active seller per week), activation (3 exports in 7 days), the **5 frozen funnel event names with exact properties and PII bans**, derived metrics with pre-committed thresholds, instrumentation rules, paywall A/B readout rules | complete |
| `docs/ONBOARDING.md` | Second-by-second 90-second script (0:00→1:30), free tier rules, the two paywall surfaces, **3 paywall copy variants as literal strings**, a "never say this" table, in-90s funnel drops D1–D5 with owners | complete |
| `docs/INTERVIEWS.md` | Screener, 12 questions with probes and strong/weak answer guidance, **6 hypotheses with pre-committed GO/KILL gates**, per-participant scoring sheet (F1–F13 factual, J1–J7 judgement with 1–5 anchors), analysis procedure, limits of N=12 | complete, **not yet run (N=0)** |

### 1.1 Commands I actually ran (output verbatim, not paraphrased)

```
$ date -u +%Y-%m-%dT%H:%M:%SZ
2026-10-28T16:28:06Z
$ wc -l docs/PRD.md docs/METRICS.md docs/ONBOARDING.md docs/INTERVIEWS.md
    506 docs/PRD.md
    202 docs/METRICS.md
    240 docs/ONBOARDING.md
    361 docs/INTERVIEWS.md
   1309 total
$ ls docs/PLAN.md
ls: docs/PLAN.md: No such file or directory
```

The arithmetic in the PRD was also computed, not hand-waved. Verified output:

```
baseline steps: 8 sum= 19.5
v1       steps: 8 sum= 8.5
per-step delta: {'shoot': 2.0, 'cull': 1.0, 'background': 2.75, 'measure': 1.5,
                 'title': 1.25, 'specifics': 2.0, 'price': 0.0, 'post': 0.5}
total saving/item = 11.0
seller hourly value $25/h -> monthly $ saved: casual(20/mo): 91.67 | pro(120/mo): 550.00 | boutique(400/mo): 1,833.33
ROI multiples (value / plan price, base case $25/h):
  Starter $19: casual(20/mo): 4.82x | pro(120/mo): 28.95x | boutique(400/mo): 96.49x
  Pro $49: casual(20/mo): 1.87x | pro(120/mo): 11.22x | boutique(400/mo): 37.41x
$15/h: value/item $2.7500 -> break-even for $19 = 7 items
$25/h: value/item $4.5833 -> break-even for $19 = 5 items
$40/h: value/item $7.3333 -> break-even for $19 = 3 items
```

Interview script timing: `blocks: 13 scripted total: 32 min` · booked slot 35 min · 3 min buffer.

---

## 2. What is assumed / unmeasured — read before quoting anything

Nothing in these four documents is primary research. Zero interviews have been conducted.

| Claim | Label | Where it lives |
|---|---|---|
| Baseline listing time **19.50 min/item** | **ASSUMPTION** — bottom-up, internally consistent, unverified | `PRD §3.2`, `§3.4` |
| v1 listing time **8.50 min/item**, saving **11.00 min** | **ASSUMPTION** — a design target, not a measurement | `PRD §3.2` |
| Seller hourly value **$25/h** (range $15–40) | **ASSUMPTION** — replacement cost, not a wage survey | `PRD §4.1` |
| Persona volumes 20 / 120 / 400 per month | **ASSUMPTION** — segmentation hypothesis | `PRD §2` |
| Market size / TAM / number of sellers | **UNMEASURED** — deliberately no figure given | `PRD §4.6` |
| Free→paid conversion, churn, LTV, payback | **UNMEASURED** — no benchmark quoted on purpose | `PRD §4.6`, `METRICS §3.2` |
| Export→marketplace-listed confirmation rate | **UNMEASURED** — the north star's known gap, named openly | `METRICS §1.1` |
| All metric targets in `METRICS.md` | decision thresholds, **chosen in advance** — not observed values | `METRICS §2`, `§6` |
| Pipeline latency p50 ≤ 20s / p90 ≤ 35s | **UNMEASURED** — the 90-second script is a *contract on* this, not a description of it | `ONBOARDING §0` A1 |

**One conflict is unresolved and I have not papered over it:** `BRIEF.md` line 14 says the
current workflow is "~8 min per item"; my bottom-up budget totals **19.5 min**. One is wrong.
`PRD §0` and `§3.2` state this explicitly, and `INTERVIEWS.md` Q3 + Q4 are designed to settle
it. **Do not resolve this by editing either document to match the other.** Resolve it with
data, then update the PRD and change the label to MEASURED.

**Consequence the team needs to hear now:** the BRIEF's "<2 min per item" target is **not
reachable in v1** (`PRD §3.3`). 4.0 of the remaining 8.5 minutes are the seller verifying our
output, and 2.0 minutes are pricing comps that v1 does not touch at all. Recommend treating
<2 min as a post-v1 aspiration and keeping it out of copy and out of `docs/PLAN.md` sizing.

---

## 3. `docs/PLAN.md` review — CANNOT BE DONE, THE FILE DOES NOT EXIST

`01-product.md` item 5 instructs me to review `docs/PLAN.md` and file disagreements. At
2026-10-28T16:28Z the file did not exist (see the `ls` output in §1.1); `docs/CONTRACTS.md`
did not exist either. `mkdir -p docs` was required to write anything at all. The architect is
building in parallel, so this is a sequencing artefact, not a fault.

**Therefore: the review is deferred, not skipped, and I have not invented its contents.**
Instead, here are the **requirements `PLAN.md` must satisfy** so the review is mechanical when
the file lands. Each is a request, phrased so it can be accepted or rejected on the record.

### 3.1 Requests for `docs/PLAN.md` (file as disagreements if they are contradicted)

| # | Request | Why |
|---|---|---|
| R1 | Do **not** state or plan around "<2 min per item". Use "~8.5 min, most of it verification" | `PRD §3.3` — the target is unreachable in v1; promising it in a plan creates a false acceptance gate |
| R2 | Sequence **single-item vertical slice end-to-end** before any batch work | A 30-item batch that half-fails on day one is unrecoverable support load (`PRD §6`, batch upload) |
| R3 | **CSV export for eBay is P0 and must be in the slice**, not deferred | It is the only mechanism by which value leaves the app; without it we cannot measure the north star (`METRICS §1`) |
| R4 | Instrument the **5 frozen event names** in the same PR as the features that fire them | Retrofitting telemetry means the first weeks of data are unusable, and we will not know if onboarding works (`METRICS §3`) |
| R5 | Plan **anonymous sessions + account merge at first export** | `ONBOARDING §6.1` — if signup lands at 0:04, every drop-off we measure afterwards is confounded |
| R6 | Treat **p50 ≤ 20s / p90 ≤ 35s** prettify latency as a plumbing requirement, and emit `job_latency_ms` | The 90-second onboarding script breaks without it (`ONBOARDING §0` A1) |
| R7 | Keep `reason_code` **user-facing** in the UI, not just in logs | `PRD US-2`, and Ingrid's whole trust model (`PRD §2.3`) |
| R8 | Plan **cost accounting per job** (provider + cost on the job row) so $0.03 / $0.10 ceilings are auditable | BRIEF ceilings; "we believe it's cheap" is not a ceiling |
| R9 | Do not schedule Poshmark/Vinted mappers at eBay parity in v1 | Half-correct mappers cause the broken-import churn that loses the pro segment (`PRD §6`) |

If any of R1–R9 is rejected, I need the rejection written down with its cost — that is the
only way `PRD §0`'s assumptions stop being invisible.

### 3.2 Also needed from the architect, blocking `ONBOARDING.md`

1. **Anonymous session + account merge** — the single biggest onboarding dependency (`R5`).
2. **Draft persistence** on-device across the hard paywall (`ONBOARDING §3.1`).
3. **Server-assigned, stored paywall variant** (`a`/`b`/`c`) so `subscription_started` weeks
   later can still be attributed to it.
4. `AssetPack` timing: is `on_model_urls[]` allowed to be empty on first response (async
   render) or must the response block? Affects `PRD US-4` acceptance criteria — my criteria as
   written allow "at least one entry", which assumes blocking or polling. **Architect to
   confirm; I will amend US-4 to match.**
5. Confirmation that `paywall_impression` can be added to the event vocabulary — it is a 6th
   event and is **not** one of the five frozen funnel names (`METRICS §5`).

> **Superseded 2026-10-28.** `PLAN.md` has since landed. This section is kept only as a record of
> what was blocking at the time. **The live review is §11** (final: 7 PASS / 2 PARTIAL / 0 FAIL);
> §10 is the first pass and its R6 verdict was revised. Read §11, not this.

---

## 4. What is deliberately NOT in these documents

- **No competitor pricing.** I did not fabricate a pricing table. The only competitor prices
  we will have are the ones sellers quote in `INTERVIEWS.md` Q6. That is a deliberate choice,
  not an omission: a fabricated comparison table is the fastest way to lose the team's trust.
- **No quadrant chart / competitive landscape.** `01-product.md` did not ask for the full PRD
  mode, and a competitive quadrant built on invented scores would be worse than none.
- **No market sizing.** `UNMEASURED`, method named, not done (`PRD §4.6`).
- **No CLAUDE.md-style style rules, no ADRs, no contracts.** Not my directory.

---

## 5. Exact next command

The documents are all on disk; nothing to build here. The next real step is a decision, then
a study:

```bash
cd /Users/bujin/Documents/Projects/SnapList && ls -la docs && wc -l docs/*.md
```

Then, for the person who owns the next action (team-lead): approve the two items blocking
`INTERVIEWS.md` (§6 below), and re-run the review in §3 once `docs/PLAN.md` exists:

```bash
cd /Users/bujin/Documents/Projects/SnapList && test -f docs/PLAN.md && echo PLAN-READY || echo PLAN-STILL-MISSING
```

---

## 6. Open questions — decisions I cannot make

1. **The 8 vs 19.5 min conflict** (§2). Blocking for pricing and GTM, not for building.
   Owner: team-lead, resolved by data.
2. **Is "<2 min/item" a promise?** I recommend: no. Owner: team-lead.
3. **Interview incentive amount and recruitment channels** — both unapproved, both block
   `INTERVIEWS.md`. Owner: team-lead.
4. **A note-taker for interviews** — the script explicitly needs two people; verbatim
   timestamps are impossible while moderating. Owner: team-lead.
5. **Pro cap of 400 = the boutique persona's exact monthly volume** (`PRD §2.3`, `§7`).
   Overage billing or a tier above Pro? Not a v1 build, but the *billing model* choice affects
   the subscriptions schema **now**, so the architect should not close that schema blind.
6. **Does the free tier show on-model as visible-but-locked?** `PRD US-4` says yes; BRIEF says
   free tier has no on-model. Confirming the *visible* part matters, because visibility is
   what sells Pro.

---

## 7. If you only read one paragraph

The four documents are complete and internally consistent, and **every number in them is
either arithmetic I ran or an assumption I named**. The PRD's time budget sums correctly to
19.50 → 8.50 → 11.00 minutes saved, the value math recomputes at both $19 and $49 and at
$15/$25/$40 per hour, and break-even lands at 3–7 items depending on the hourly value — which
is why the 3-item free tier is a demo rather than a substitute. The most important thing in
here is not the PRD; it is that `docs/INTERVIEWS.md` has **six hypotheses with gates fixed
before the data arrives**, so the team cannot rationalise a bad result. Run that before
building anything else. The second most important thing is the unresolved conflict between
`BRIEF.md`'s "~8 min per item" and my 19.5-minute bottom-up budget — if BRIEF is right, the
value proposition shrinks by roughly 2.4× and the casual segment may not be worth serving.

---

## 8. Revision pass — team-lead rulings of 2026-10-28

Three rulings received and applied. **No number in `PRD.md` was changed** in this pass.

### 8.1 Ruling 1 — the 8-vs-19.5 min conflict: both numbers stay, the line is redrawn

Adopted exactly as instructed: `BRIEF.md` is untouched (it is a fixed decision and other roles
anchor to it), `PRD §0` keeps the conflict stated, and **`<2 min/item` is barred from any
acceptance criterion** because end-to-end minutes cannot be asserted by a script.

Replaced, as directed, by two scriptable proxies — written up in **`METRICS.md` §3.3**:

| Proxy | What it is | Chosen threshold |
|---|---|---|
| **A. tool time** | `prettify_latency_ms` + `attrs_latency_ms` + `export_latency_ms`, the only per-item duration a test can assert | p50 ≤ 35 s, p90 ≤ 60 s |
| **B. session cleanliness** | `session_3_exports_rate` (3 exports in a rolling 60-min window) and `manual_field_edits_per_item` | **N = 5 edited fields/item** |

Two things I added while writing that up, because leaving them implicit would have caused
silent mistakes:

1. **`METRICS.md` §3.3 now warns that three different "3"s exist** in this repo — 3 exports
   in **7 days** (activation), 3 exports in **60 minutes** (QA proxy), 3 **free** items
   (billing). They are deliberately different and must never be reported as one another.
2. **`manual_field_edits_per_item` is not computable from today's contract.**
   `edited_field_count` counts only *changed* fields; a seller who reads a measurement and
   accepts it spends real time and produces zero edits. Needs **`fields_touched`** on
   `attributes_edited`. Raised with the architect; if rejected we report a **lower bound, and
   label it as one**.

`ONBOARDING.md` §4.1 gained the explicit row you asked for: `<2 min` / "2 minutes per item" /
"list 30 items in an hour" is **banned from product pages, store copy, paywall text, ads and
sales DMs** until `PRD §3` is measured.

### 8.2 Ruling 2a — incentive and recruitment: now a labelled proposal, not a plan

`INTERVIEWS.md` §1.4 is new and marked **`AWAITING-APPROVAL`** in the heading, in the body, and
in §6 item 1. Contents: **$50 gift card per completed 35-minute interview**, 12 completed =
**$600**, range $40–75 with an escalation rule ("if round 1 yields < 3 bookings, raise the
offer to $75 *before* widening channels"), three channels, and no paid panel vendor.

Two honesty guards written into it:
- **Contacts-per-booking is `UNMEASURED`** and I refused to invent a funnel multiplier; budget
  in *completed* interviews and report actual contacts after round 1.
- **`$600 ÷ 12 = $50` is not a CAC** and must never be reported as one. It is the cost of a
  pre-launch conversation with someone who has bought nothing; there is no conversion to divide
  by. Explicitly flagged so nobody puts "$50 CAC" in a deck.

### 8.3 Ruling 2b — single moderator + recording + ASR: done, with the cost stated

`INTERVIEWS.md` reworked: the "two people" requirement is gone from the logistics table, the
room rules, the scorecard, the Q3 callout, the analysis step and the asks list. Added:

- **§1.2 recording kit** — 7-item pre-call checklist, incl. a 10-second playback test, file
  naming at call end, and the rule that **a dead recording is announced immediately and a
  transcript is never reconstructed from memory**.
- **§1.3 transcription workflow + the consent script verbatim**, plus the step that replaces
  the note-taker: a **mandatory audio-check pass** over every F-field number, tool name/price
  and marketplace name, because ASR mangles precisely "pit-to-pit", "Gildan", "£19". Numbers
  that cannot be resolved by ear go in as `[unclear]` + `LOW CONFIDENCE` — never guessed.
- **§2 rule 6** rewritten: every number carries a timestamp, because the transcript is now the
  note-taker.
- **§5.2 single-analyst guard** — this is the part of your change that has a real cost, and I
  am not going to pretend otherwise. Removing the second person from the room also removed the
  second *reader*, and one analyst reading 12 transcripts finds what they expect to find. So:
  write and timestamp your predicted median F5 **before** tabulating; report it if the
  prediction matched; and have a second person cold re-score 3 transcripts on F5 and F10
  (disagreement > 20% ⇒ re-read all 12). That is ~1 hour total instead of 7 hours of second
  attendance, which is why I am not objecting to the trade — only to doing it unguarded.
- **PII constraint landed in `METRICS.md` §4 rule 7**: recordings/transcripts/attributable
  quotes live only in `docs/research/` (git-ignored), never in the event store or a dashboard;
  aggregated scorecard numbers may be committed, verbatim quotes only anonymised; consent to
  record ≠ consent to publish; audio deleted after the audio-check pass is signed off.

Still needs a human: **someone must confirm `docs/research/` is actually git-ignored before
the first recording exists** (`INTERVIEWS.md` §6 item 4).

### 8.4 Ruling 2c — Pro 400 cap: noted, not my file

Your decision (reserve overage ledger columns in `subscriptions`, all `false` in v1, hard-block
402) is a schema change and belongs to the architect; you said you are relaying it, so I have
not written it into any of my four documents. `PRD §7` question 5 stands as an open product
question about whether a tier above Pro exists — it does not contradict your decision, which is
the right v1 answer.

### 8.5 Real command output for this revision pass

```
$ wc -l docs/PRD.md docs/METRICS.md docs/ONBOARDING.md docs/INTERVIEWS.md
    506 docs/PRD.md
    264 docs/METRICS.md
    241 docs/ONBOARDING.md
    477 docs/INTERVIEWS.md
   1488 total
$ grep -n '^### 3\.3' docs/METRICS.md ; grep -n '^## 4\.' docs/METRICS.md
144:### 3.3 Engineering acceptance metrics — **not** activation, do not merge
197:## 4. Instrumentation rules
$ grep -n '^### 5\.2' docs/INTERVIEWS.md
445:### 5.2 Single-analyst guard — write your prior *before* you tabulate
$ grep -n 'Note-taker:' docs/INTERVIEWS.md
(none: duties migrated to moderator)
```

Deltas this pass: `METRICS.md` 202 → 264 (+62), `ONBOARDING.md` 240 → 241 (+1),
`INTERVIEWS.md` 361 → 477 (+116). `PRD.md` unchanged at 506 — deliberately, per ruling 1.

### 8.6 Standing instruction: PLAN review is deferred, not abandoned

I am on standby for `docs/PLAN.md`. When its path arrives I will mechanically check **R1–R9
(`§3.1`)** and report **PASS/FAIL per item with the evidence line number**, appending the
result to this file. I will not rewrite `PRD.md`, and I will not edit `PLAN.md` itself.

Two items I expect to fail on a first draft, so they are worth watching for:
- **R6** (latency p50 ≤ 20 s / p90 ≤ 35 s as a plumbing requirement) — plumbers usually plan
  for correctness first and latency later, and this one is on the critical path of onboarding.
- **R3** (eBay CSV in the first vertical slice) — it is easy to treat export as "the last
  screen" and defer it, which would leave us unable to measure the north star for weeks.

---

## 9. Revision pass 2 — architect's five decisions absorbed (2026-10-28)

The architect closed **all five** blocking questions and absorbed **all nine** of R1–R9, with
nothing rejected. Five of my documents changed as a direct consequence. `PRD.md` changed this
time (US-3, US-4, §7) — unlike the previous pass, because the architect's decisions landed
*inside* my acceptance criteria.

| Change | Where | Why |
|---|---|---|
| US-4 rewritten for **async render** | `PRD US-4` | ADR-004: `on_model_urls` may be `[]` on first fetch; the invariant `job(vton, done) ⇒ length ≥ 1` is asserted on the completed job; state is read from the job, never from array length. A failed render leaves the array untouched. |
| **`fields_touched` added to US-3** | `PRD US-3` | The architect adopted the property, so the "lower bound" caveat is gone. `field_names[]` is now regex-constrained `^[a-z][a-z0-9_]*$` so a value cannot be pasted in as a name. |
| **`attributes_edited` firing condition changed** | `PRD US-3` | See §9.1 — the one place I changed a rule rather than just documenting one. |
| 4 of 5 open questions closed | `PRD §7` | Q2/Q3/Q4/Q5 marked CLOSED with the ruling and its ADR; Q1 (8 vs 19.5 min) stays **OPEN** because only data closes it. |
| Latency props on the right events | `METRICS §3` table | `prettify_latency_ms` + `attrs_latency_ms` ride on `prettify_approved`; `export_latency_ms` on `export_clicked`. |
| Tool-time basis corrected | `METRICS §3.3` | Architect's catch, and a good one: a seller who accepts every suggested value **never fires `attributes_edited`**, so deriving tool time from it would understate latency precisely for the smoothest sessions. Now computed from `prettify_approved` + `export_clicked` only. |
| Attribute override formula **was silently broken** | `METRICS §3.2` | Once the event fires on every confirm, "items with `attributes_edited`" measures *screen reach*, not override. Rewrote it as `edited_field_count > 0`, and added the missing twin: **blind-acceptance rate** (`fields_touched = 0`), because a low override rate and a skipped screen look identical otherwise. |
| Paywall variant assignment corrected | `METRICS §5` | My draft said "hash of `user_id`". ADR-005 §2 says server-assigned and **sticky**. A client hash re-rolls on device change and destroys attribution. |
| All four architect asks closed | `ONBOARDING §6` | Anonymous session (ADR-003), latency, drafts at P0, sticky variant — each with the accepted cost written down, including the 3-free-items-per-reinstall farm. |

### 9.1 One rule I changed rather than documented — flagged, not smuggled

Original US-3: *"the seller edits nothing and confirms → **no** `attributes_edited` is emitted
(an empty edit is not an edit)."*

That rule was fine when `edited_field_count` was the only counter. With `fields_touched` it
becomes actively harmful: a seller who reads all five measurements, agrees, and confirms is
exactly the **blind-acceptance** case we most need to see, and the old rule made it invisible.
So the firing condition is now **"the seller touched the screen"**, and `fields_touched = 0` is a
recorded outcome rather than a missing event.

**This may contradict the current implementation.** If `apps/api` or the mobile emitter only
fires on a value change, the semantics and a test need to change; if you would rather keep the
current condition, say so and I will revert the doc instead. I am not going to quietly leave the
doc and the code disagreeing — the fault is a one-line difference and the cost of leaving it is a
metric that quietly measures the wrong thing.

### 9.2 Command output, this pass

```
$ wc -l docs/PRD.md docs/METRICS.md docs/ONBOARDING.md docs/INTERVIEWS.md docs/PLAN.md
    539 docs/PRD.md
    303 docs/METRICS.md
    252 docs/ONBOARDING.md
    477 docs/INTERVIEWS.md
    682 docs/PLAN.md
   2253 total
```

Deltas vs §8: `PRD.md` 506 → 539 (+33), `METRICS.md` 264 → 303 (+39), `ONBOARDING.md`
241 → 252 (+11), `INTERVIEWS.md` unchanged at 477.

---

## 10. `docs/PLAN.md` mechanical review — R1–R9 (682 lines, 46 tickets)

The file landed; this is the deferred review from §3, done as instructed: **PASS/FAIL per item
with the evidence line number**, no rewriting of `PLAN.md`, no changes to its content.

| # | R-item | Verdict | Evidence |
|---|---|---|---|
| R1 | `<2 min/item` barred as an acceptance criterion | **PASS** (with a wording conflict, §10.1) | `PLAN:14-22` hard-constraint block; `PLAN:49` "no ticket is accepted on one"; `PLAN:452-455` E7-T5 DoD forbids stating it as fact and prescribes the honest "~8.5 min" line; `PLAN:663` cost table records what the weaker pitch costs |
| R2 | Single item before batch; batch depends on **both** T3 and T5 | **PASS** | `PLAN:340` dependency column "E6-T3, E6-T5"; `PLAN:342-345` the stated rationale; `PLAN:390-391` "may not start before E6-T3 and E6-T5 are done"; `PLAN:575-576` explicitly not in the slice; `PLAN:622-623` graph edges |
| R3 | eBay CSV in the first vertical slice, no upstream code dependency | **PASS** | `PLAN:399` priority "P0 — first slice", dependency "frozen — no code dependency"; `PLAN:406-409` the reasoning; `PLAN:564` slice table row; `PLAN:571-573` slice acceptance includes a CSV whose header matches; `PLAN:650-651` "do not serialise export behind E3" |
| R4 | Analytics in the same PR; event names frozen | **PASS** | `PLAN:36-38` global DoD #7 ("Six event names, frozen; a seventh needs an ADR"); `PLAN:278` E5-T6; `PLAN:320-327` closed vocabulary, unknown name → 422, no free-text column; `PLAN:369-370` E6-T3 emits the latency props; `PLAN:666` |
| R5 | Anonymous session + identity at first export | **PASS** | `PLAN:277` E5-T5 title; `PLAN:314-318` acceptance (same `user_id` before/after `updateUser`) and DoD "No signup wall before the first upload"; `PLAN:352-353` E6-T1; `PLAN:667` accepted cost (3 items per reinstall) |
| R6 | Latency as a **plumbing requirement**, thresholds asserted with `job_latency_ms` | **FAIL — thresholds declared, no gate enforces them** | see §10.2 |
| R7 | `reason_code` user-facing, not logs-only | **PASS** | `PLAN:39-40` global DoD #8 ("never a `reason_code`"); `PLAN:367-368` E6-T3; `PLAN:388` E6-T6 per-row failure uses §6 copy |
| R8 | `provider` + cost on the job row so both ceilings are auditable | **PASS** | `PLAN:283-287` E5-T1 DoD (`jobs.provider/model_version/cost_micros`, "`cost_micros` NULL means **unmeasured**, never zero"); `PLAN:301-302` E5-T3; `PLAN:113-115` E0-T4 cost dashboard from `evals/reports/cost.md`; `PLAN:250-253` E4-T1 measured `$/render` with arithmetic shown; `PLAN:675` |
| R9 | Poshmark/Vinted below eBay with a verify-first gate | **PASS** | `PLAN:403` P2 + "gate: verify the live upload path first"; `PLAN:411-416` the 80%-wrong-mapper churn argument; `PLAN:429-437` acceptance is *refused* until `docs/evidence/*` exists; `PLAN:673` accepted cost ("two of BRIEF's four marketplaces are weaker in v1") |

**Score: 8 PASS, 1 FAIL (R6).** The one FAIL is the one I predicted, and it is narrow.

### 10.1 R1 wording conflict — needs one ruling, not a rewrite

`PLAN:21-22` says `<2 min/item` "survives only as an outbound claim product must verify with
interviews". That is a **licence to put it on a page**, and it contradicts two other places in the
same repo: `PLAN:453` E7-T5 ("**No page may state `<2 min per item` as fact**") and
`ONBOARDING §4.1`, which bans it from pages, store copy, paywall, ads and sales DMs **entirely**
until `PRD §3` is measured.

The acceptance-criteria requirement (what R1 actually asked for) is satisfied — this is a wording
issue, not a scope issue. But a hedged "<2 min\*" with an asterisk still reads as a promise to a
seller, and the person writing the landing page will follow line 21, not line 453.
**Recommendation:** change `PLAN:21` to "survives only as an **internal** hypothesis; it may not
appear in customer-facing copy until `PRD §3` is measured". That is a one-line edit and it makes
three documents agree. Until it is made, **ONBOARDING §4.1 (the stricter rule) governs.**

### 10.2 R6 — the thresholds are real, the gate is missing

`PLAN` declares the numbers correctly (`PLAN:20`, `PLAN:55`, `PLAN:56`) and even names which
tickets the proxies "apply to" (`PLAN:65-67`: E6-T3, E6-T4 + E5-T3, E7-T1, E0-T5). Checked all
four against their own DoD:

| ticket | DoD lines | says anything about a latency number? |
|---|---|---|
| E5-T3 | `PLAN:298-302` | no — claim/retry/quota/provider/cost only |
| E6-T4 | `PLAN:375-379` | no — paywall copy, on-model states, variant only |
| E7-T1 | `PLAN:424-427` | no — header bytes, test, condition enum, `insufficient_data` |
| E0-T5 | `PLAN:117-125` | **prints** p50/p90 with sample size, and honestly prints `UNMEASURED (n=12)` under 30 samples — but **no pass/fail** |

So: nothing in this plan goes red if prettify takes 60 seconds. The instrumentation exists
(`PLAN:62-63` is right that "a latency number nobody emits is a wish"), but a number that is
printed and never compared to its threshold is only marginally better than a wish — it depends on
someone eyeballing a report.

**Minimal fix (3 lines, in PLAN, not mine to write):** add to E0-T5's DoD — *"exit non-zero when
p90 `prettify_latency_ms` > 35 s or p90 tool time > 60 s at n ≥ 30; printing a number is not a
gate"* — and add one sentence each to E5-T3 and E6-T4 saying the latency props must be emitted on
the events named in `METRICS §3`, so the gate has inputs.

**Why this matters more than it looks:** `ONBOARDING.md` §0 A1 is a *contract on the pipeline*.
The 90-second first-run script has a 23-second dead-air beat at 0:42–1:05 (`ONBOARDING.md` §1). If
prettify lands at 60 s, the script does not merely get slower — its whole design breaks, and we
would discover that from a churn number rather than from a red build.

### 10.3 One cross-doc arithmetic disagreement found and fixed by me

`PLAN:59` and `PLAN:20` gate on the **mean** of `fields_touched`; my `METRICS §3.3` said **p50**.
Two documents disagreeing on an acceptance gate is how a gate quietly stops existing. I aligned
`METRICS` to the **mean** (with p50 reported alongside, so a mean dragged up by a few 15-field
items is visible) and left a note in both directions. No other numeric disagreement found across
`PRD`/`METRICS`/`ONBOARDING`/`PLAN`/`CONTRACTS`.

### 10.4 What this review does not cover

I checked R1–R9 and the four documents' numbers against `PLAN`. I did **not** review `PLAN`'s
ticket decomposition, file-ownership boundaries, epic ordering, or the mermaid graph for
correctness — that is the architect's and the integrator's judgement, and I have no evidence to
add. The 46-ticket count, epic dependencies and `docs/evidence/` gates I only spot-checked where
they touched R1–R9.

---

## 11. R1–R9 re-verification against the **final** PLAN (2026-10-28T16:51Z)

Scope of this pass, per instruction: **read-only** on `docs/PLAN.md` (682 lines, 46 tickets),
`docs/CONTRACTS.md` (410 lines, `CONTRACTS_VERSION 1.0.0` / `EVENTS_VERSION 1.1.0`) and
`ADR-001..005`. **I modified none of them.** My own `METRICS.md` was touched once (§11.5) because
the review found a payload mismatch *in my document*, not in theirs.

> **This section supersedes §10's verdicts** where the two differ, and §10 is left unedited so the
> change of judgement is auditable. Two verdicts moved: **R6 FAIL → PARTIAL** (with a sharper and
> more useful finding) and **R1 PASS → PARTIAL** (a licensing conflict that has now propagated into
> `CONTRACTS.md` as well).

### 11.1 Verdicts

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| R1 | `<2 min/item` barred as an acceptance criterion | **PARTIAL** | Barred correctly: `PLAN:14-22`, `PLAN:49`, `CONTRACTS:357-358`. But both files license it as an "unverified **outbound** claim" (`PLAN:21`, `CONTRACTS:357`), which contradicts `PLAN:453` ("no page may state it as fact") and `ONBOARDING §4.1` (banned from customer copy outright). Details §11.4. |
| R2 | Single item before batch; batch needs T3 **and** T5 | **PASS** | `PLAN:340`, `PLAN:342-345`, `PLAN:390-391`, `PLAN:575-576`, `PLAN:622-623` |
| R3 | eBay CSV in the first vertical slice, no upstream code dependency | **PASS** | `PLAN:399` ("P0 — first slice", dependency = frozen contract only), `PLAN:406-409`, `PLAN:564`, `PLAN:571-573`, `PLAN:650-651` |
| R4 | Analytics in the same PR as the feature; names frozen | **PASS** | `PLAN:36-38` (DoD #7), `PLAN:320-327` (closed vocabulary, 422 on unknown), `CONTRACTS:301-323`, `PLAN:666` |
| R5 | Anonymous session, identity at first export | **PASS** | `PLAN:277`, `PLAN:314-318`, `PLAN:352-353`, `PLAN:667` |
| R6 | Latency as a **plumbing requirement**, thresholds asserted | **PARTIAL** | Thresholds are correct and single-sourced (`PLAN:55-58`, `CONTRACTS:344-355`), and the tool-time 口径 is now unambiguous — but **no ticket's DoD gates on a latency number**, and **E1–E4 carry no latency requirement at all**. Details §11.2. |
| R7 | `reason_code` user-facing, not logs-only | **PASS** | `PLAN:39-40` (DoD #8), `PLAN:367-368`, `PLAN:388` |
| R8 | `provider` + cost on the job row; both ceilings auditable | **PASS** | `PLAN:283-287` (`cost_micros` NULL = unmeasured, never zero), `PLAN:301-302`, `PLAN:113-115`, `PLAN:250-253`, `PLAN:675` |
| R9 | Poshmark/Vinted below eBay, verify-first gate | **PASS** | `PLAN:403`, `PLAN:411-416`, `PLAN:429-437` (acceptance *refused* until `docs/evidence/*` exists), `PLAN:673` |

**7 PASS · 2 PARTIAL · 0 FAIL.** (R6 was FAIL in §10; see §11.2 for why I am raising it.)

### 11.2 R6 — the 口径 is now clean; the ownership is not

**What the lead asked me to confirm: is the tool-time 口径 ambiguous?** It is **not**. Confirmed
clean on all three points:
- tool time = `prettify_latency_ms + attrs_latency_ms + export_latency_ms` for one item
  (`PLAN:55`, `CONTRACTS:347-348`);
- all three are **client** measurements, explicitly larger than server `job_latency_ms`
  (`CONTRACTS:352-353`);
- `attrs_latency_ms` sits on `prettify_approved`, **not** `attributes_edited`, with the reason
  written down (`CONTRACTS:353-355`: a seller who never edits would never report a latency and
  tool time would be silently under-counted). My original catch is fixed at the contract level,
  which is stronger than fixing it only in my metrics doc.

**What is still missing — and this is the answer to your E1–E4 question:** latency there is
neither an acceptance gate **nor** a scheduled optimisation ticket. It is **unowned**.
- `E1-T2` DoD (`PLAN:147-153`): IoU ≥ 0.92, backend recorded. No time.
- `E2-T1/T2` DoD (`PLAN:177-192`): SSIM, no generative repainting, logo mask. No time.
- `E3-T2/T3` DoD (`PLAN:218-228`): schema valid, per-attribute accuracy. No time.
- `E4-T1` DoD (`PLAN:247-253`): `$/render` from observed latency × rate — **cost**, not latency.
- `E0-T5` DoD (`PLAN:117-125`): **prints** p50/p90 with sample size (and honestly prints
  `UNMEASURED (n=12)` under 30 samples) — **no pass/fail**.
- `E5-T3` (`PLAN:298-302`), `E6-T4` (`PLAN:375-379`) — no latency number, despite `PLAN:65-67`
  claiming the proxies "apply to exactly four tickets" including these.

So a vision role can pass **every** ticket while building a 90-second sequential pipeline, and we
would find out at E6-T4 — the last ticket before the export screen. That is the structural risk,
and it is exactly the "correctness first, performance later" habit you predicted.

**But I do not recommend gating E1–E4 on p90.** Gating quality work on latency measured on an
unrepresentative dev machine is how a plan gets stuck: the vision role would be blocked by
hardware it does not control, on fixtures that are not a population. My recommendation splits
reporting from gating:

1. **Now (before E1 starts), reporting only — zero schedule risk.** Add one DoD clause to
   `E1-T2`, `E2-T2`, `E3-T2`, `E4-T1`: *"the eval report prints wall-clock ms per stage for the
   fixture set; no threshold, but the number must appear."* Cost: a sentence and a timer. Benefit:
   a 90 s pipeline is visible in week 2 instead of at E6-T4, and the vision role can see the trend
   while it is still cheap to change (e.g. batching the SDXL inpaint).
2. **Before launch, gating.** Add to `E0-T5` DoD: *"exit non-zero when p90 `prettify_latency_ms`
   > 35 s or p90 tool time > 60 s at n ≥ 30; printing a number is not a gate."* This cannot be a
   pre-E1 gate anyway — it needs 30 real event rows, which do not exist until after launch.

That split is the honest resolution of your worry: **don't let p90 block the pipeline, don't let
it go unwatched either.**

### 11.3 R3 — confirmed PASS, and it is genuinely in the slice

`PLAN:399` gives E7-T1 priority "P0 — first slice" with its only dependency being the frozen
contract, `PLAN:406-409` states the reasoning, `PLAN:564` places it in the slice table, and
`PLAN:571-573` makes a CSV with a matching header part of the slice's acceptance. It did not get
pushed to "the last screen". The north star will be measurable from the first end-to-end run.
No action needed.

### 11.4 R1 — a licence conflict that has now propagated to a second file

`PLAN:21` and `CONTRACTS:357` both say `<2 min/item` "survives only as an **outbound** claim
product must verify". `PLAN:453` (E7-T5 DoD) says no page may state it as fact. `ONBOARDING §4.1`
bans it from product pages, store copy, paywall text, ads and sales DMs **entirely** until
`PRD §3` is measured.

The acceptance-criteria half of R1 is satisfied — nothing is gated on it. The conflict is about
**who is allowed to say it and when**, and the person who writes the landing page (`E7-T5`, P1)
will follow `PLAN:21`, not `PLAN:453`. A hedged "<2 min\*" is still read as a promise.

**Needs one ruling, not a rewrite.** My recommendation: change the phrase in both files to
"survives only as an **internal** hypothesis; it may not appear in customer-facing copy until
`PRD §3` is measured", or relax `ONBOARDING §4.1` and tell me which lines I may run. **Until
ruled, `ONBOARDING §4.1` governs** — the stricter rule wins by default, which is the safe failure
direction. Not blocking E1; blocks E7-T5.

### 11.5 Your activation question — my call, and it is a real defect

**You are right, and it is worse than "action vs success".** There are three states, not two:

| # | state | what we can see | what it means |
|---|---|---|---|
| 1 | export **attempted** | — | nothing yet |
| 2 | CSV **produced** | `export_clicked` | value left the app |
| 3 | listing **published** | nothing | the seller's backlog actually shrank |

`CONTRACTS:321` defines `export_clicked` as *"seller **asks for** a marketplace file"* — state 1,
not state 2. `CONTRACTS:340` defines `activation_items_exported_7d` as "3 exports / 7 days".
So: **if CSV generation fails, or the seller taps export and abandons, activation still counts it.**
Activation and the north star are both **upper bounds**, and the bias is not measurable from our
own data. My `METRICS §3` says "successfully produced"; CONTRACTS says "asks for" — the contract
wins, so **my number is the wrong one and the contract should be tightened, not the metric.**

**Fix (one word, before E7-T1 is coded — it changes what the client emits):** `CONTRACTS:321`
"asks for" → "**receives** a produced file". Then `export_clicked` = state 2, an abandoned or
failed export emits nothing, and activation stops being inflated. `export_latency_ms` already
implies the file is produced and previewed (`PLAN:58` "tap export → rows previewed"), so the
payload already assumes state 2 — only the prose contradicts it.

**State 3 I recommend leaving out of v1, deliberately.** It needs a 24–48h "did these go live?"
prompt and would be a **7th event** (`EVENTS_VERSION` bump, ADR-002). Cheaper and more honest:
store it as a **`listing_confirmations` table, not an analytics event** — it is survey data with
a long tail and a low response rate, not a funnel step, and putting it in the event stream would
tempt someone to divide it into a conversion rate. Until then, label the north star exactly as
`METRICS §1.1` does: *"exported items, an upper bound on listed items"*. Do not let anyone call
it "listings".

**Second, smaller gap found while checking:** `activation_items_exported_7d` is defined in
`CONTRACTS:340` and `packages/shared/src/analytics.ts`, but **no ticket reports it.** `E0-T5`
(`PLAN:117-125`) reports tool time, mean fields-touched and the export burst — activation, which
is my #2 weekly-review number (`METRICS §6`), has no reporting surface. One DoD line on E0-T5
("also print activation: sellers reaching 3 exports in 7 days ÷ sellers with ≥1 export, with n")
closes it. Cheap; worth doing while E0-T5 is open, worthless to retrofit.

### 11.6 What must be fixed now vs before launch

| # | Fix | When | Why that moment | Blocking? |
|---|---|---|---|---|
| 1 | `export_clicked`: "asks for" → "receives a produced file" (`CONTRACTS:321`) | **Now** | E7-T1 is in the first slice; changing what an event means *after* the client emits it means re-testing it, and until then activation is a known-wrong number | Blocks E7-T1, not E1 |
| 2 | Per-stage wall-clock printed in E1–E4 eval reports (no threshold) | **Now** | Cost is one DoD sentence; retrofitting timers across 4 finished epics is expensive, and a 90 s pipeline found at E6-T4 is too late to change cheaply | No — but cheapest now |
| 3 | `E0-T5` also prints **activation** | **Now** | It is my #2 weekly number and currently has no reporting surface at all | No |
| 4 | Latency **gate** in `E0-T5` (exit non-zero, n ≥ 30) | **Before launch** | Needs 30 real event rows; impossible pre-launch by definition | No |
| 5 | `<2 min` wording in `PLAN:21` + `CONTRACTS:357` vs `ONBOARDING §4.1` | **Before E7-T5** (landing page) | Commercial copy, not engineering; P1 | No |
| 6 | `listing_confirmed` as a table, not a 7th event | **Post-v1** | Needs a prompt + response data; still an upper bound until then | No |

**Nothing in this list blocks E1 starting.** One item (#1) blocks E7-T1, which is in the first
slice and therefore worth doing first.

### 11.7 One inconsistency I found in *my own* document and fixed

Reviewing CONTRACTS §7 against `METRICS §3` turned up payload drift **in my file**, not theirs:
I listed `is_free_item`, `export_attempt_index`, hashed `stripe_customer_id`, `retry_count` and
`confidence_bucket`, none of which are in the frozen payload. Three were dropped, and the
prettify retry rate is now derived from `decision` (`count(decision="retry") ÷ count(*)`) instead
of from a `retry_count` field that does not exist — the old formula would have produced a **zero
or a null**, silently. `METRICS §3` now states that **CONTRACTS owns payload shape and wins any
disagreement**, with a note on what was reconciled. My metrics needed `retry_count` and
`confidence_bucket` for nothing in the weekly five (`METRICS §6`), so nothing of value was lost —
but the divergence would have become a wrong number on a dashboard.

### 11.8 Command output, this pass

```
$ wc -l docs/PRD.md docs/METRICS.md docs/ONBOARDING.md docs/INTERVIEWS.md docs/PLAN.md docs/CONTRACTS.md
    539 docs/PRD.md
    311 docs/METRICS.md
    252 docs/ONBOARDING.md
    477 docs/INTERVIEWS.md
    682 docs/PLAN.md
    410 docs/CONTRACTS.md
   2671 total
$ date -u +%Y-%m-%dT%H:%M:%SZ
2026-10-28T16:51:55Z
```

`METRICS.md` moved 303 → 311 (+8) in this pass; `PRD.md`, `ONBOARDING.md`, `INTERVIEWS.md`
unchanged. `PLAN.md` and `CONTRACTS.md` were read, not written.

---

## 12. Ruling record and the E0-T5 gate specification (2026-10-28T16:53Z)

Two things in this section: **(12.1)** every R1–R9 verdict is annotated with the lead's ruling,
so the trail is auditable later without reading a chat log; **(12.2)** a copy-paste
specification for the `E0-T5` gate, because the ruling was "product writes the spec, architect
pastes it". **I did not edit `PLAN.md` or `CONTRACTS.md`.** `PLAN.md` was modified by others at
10:53Z while I was reviewing; §12.1 is re-checked against that version, not the 10:48 one.

### 12.1 Verdict → ruling → current status

| # | My verdict (§11) | Lead's ruling | Status against the 10:53Z files |
|---|---|---|---|
| **R1** | PARTIAL — banned as a criterion, but licensed as an "outbound claim" in `PLAN:21` and `CONTRACTS:357` | **Accepted, strict version adopted.** `PLAN:21` to be changed to internal-only; `ONBOARDING §4.1` stays the strictest authority; "省 11 分钟" framing accepted as correct rather than as a cost | **Half done.** `PLAN:22-26` now reads "survives **only as an internal assumption**… must not appear in **any seller-facing material**… `ONBOARDING §4.1` is the stricter authority". **`CONTRACTS:358` still says "unverified outbound claim, tracked by product in METRICS.md"** — the licence survives in the second file and it is the one a landing-page author would also read. One-line fix, still open. |
| **R2** | PASS | no action | unchanged, PASS |
| **R3** | PASS | no action | unchanged, PASS |
| **R4** | PASS | no action | unchanged, PASS |
| **R5** | PASS | no action | unchanged, PASS |
| **R6** | PARTIAL — thresholds right, no gate, unowned in E1–E4 | **Accepted in full: "the most important finding of this round"** | **Mostly done.** `PLAN:63` gate is now `mean ≤ 5 AND p90 ≤ 12`; `PLAN:84-86` requires E5-T3/E6-T4 to emit the properties and says E0-T5 "fails the build"; `PLAN:136-155` is now a real gate with PASS/FAIL/UNMEASURED, `n < 30 ⇒ UNMEASURED` and no-threshold-loosening-without-ADR. **What is still unspecified is §12.2's scope:** exit-code semantics, `n = 0` pre-launch behaviour, percentile definition, per-item dedupe, and tool-time completeness. |
| **R7** | PASS | no action | unchanged, PASS |
| **R8** | PASS | no action | unchanged, PASS |
| **R9** | PASS | PASS; `insufficient_data` re-scoped to before-launch (the v1 comps path has no data source, so a state machine there is over-design) | unchanged, PASS |

**Two attributions I am correcting, not accepting.** The lead's message credits me with the
R5 "双编译 / false-green defence" point and the R9 `insufficient_data` point. **Neither was in my
review** — R5 and R9 both came out PASS in §11 and I raised neither. I would rather the record
show a finding unmade than have two findings credited to the wrong reviewer; if they came from
another role they belong in that role's handoff. Everything else in the ruling matches what §11
actually said.

**On "省 11 分钟" being correct rather than a cost — accepted, with one guard.** The lead is right
that a seller can verify the *mechanism* (no re-typing, no background removal) on their own first
item, whereas an absolute per-item claim needs our baseline. The guard: **11 minutes is
`19.5 − 8.5`, i.e. two assumptions subtracted from each other**, so it inherits the ban too. Sell
the mechanism in copy; if a number is printed, print it as illustration and keep it out of any
claim-of-record until `PRD §3` is measured. The current `ONBOARDING §4.1` row bans per-item time
promises — I am reading "11 minutes saved" as covered by it, and I would rather be over-strict
here for one more week than spend a quarter walking a claim back.

**On the two `fields_touched` gates.** Adopted as ruled (mean ≤ 5 **and** p90 ≤ 12; p50 reported
only). The reason is worth recording because it is not the intuitive one: once the event fires on
page-open with `fields_touched` allowed to be 0, a wave of blind acceptance adds **zeros**, which
drags the **mean down** — so the mean gate gets *easier* to pass exactly when the product is
getting worse. p90 is the half of the distribution that still shows the pain. `METRICS §3.3` was
updated to match, including the p50-is-report-only rule.

**On `attributes_edited` firing.** Confirmed by the architect: the contract already said
`CONTRACTS:320` "seller **touches** the attribute sheet", the zod schema already allows
`fields_touched: 0`, and no test asserted change-only. So **US-3 stands as I amended it** — no
revert. The architect also flagged a residual ambiguity worth recording: "touches the sheet" can
be read as *touched a field*, and the purest blind-accept sample (opens, looks at nothing, closes)
would then emit nothing. **Bias direction matters:** under that reading the rate is biased **low**
— the direction that hides the problem. Recorded in `METRICS §3.2` as a caveat: until
`CONTRACTS:320` says "opens the page (counts may be 0)", treat any blind-acceptance figure under
30% as a **lower bound on skipping**, never as reassurance.

### 12.2 `E0-T5` gate — copy-paste specification for the architect

`PLAN:136-155` already says "gate, not dashboard", requires non-zero exit, three rendered fields,
`n < 30 ⇒ UNMEASURED`, and forbids loosening a threshold without an ADR. Those five points are
good and I am not restating them. What is missing is **which** non-zero code, and the mechanics
that two honest implementations would otherwise disagree about. Paste as needed; nothing below
requires a change to any other ticket except where noted in (d) and (g).

**PASTE BELOW THIS LINE**

**(a) Invocation and exit codes.** Three modes, because a build gate and a weekly report have
different jobs, and because a gate that is red on an empty database gets disabled within a week.

```bash
python -m evals.telemetry --since 7d                  # report mode: prints, always exit 0
python -m evals.telemetry --since 7d --gate            # release gate: exit 0/1/2/3 (below)
python -m evals.telemetry --since 7d --gate --allow-inconclusive   # pre-launch CI
```

| exit | meaning | when | what a human does |
|---|---|---|---|
| **0** | all gates evaluated and passed | every gate has sufficient sample and is under threshold | nothing |
| **1** | **at least one gate FAILED** on sufficient sample | a threshold was breached, `n` was sufficient | treat as a regression; fix the pipeline or open an ADR to change the threshold (never a quiet edit) |
| **2** | **INCONCLUSIVE** — insufficient sample for ≥1 gate | `n < 30` for any gate (including `n = 0` on a fresh database) | gather data; **never** treat as pass, never as regression |
| **3** | **DATA ERROR** — the inputs are unusable | events table or schema missing, a required property absent from > 20% of qualifying rows, all rows unparseable | fix ingestion; this is a bug in E5-T6 or the emitter, not a performance problem |

**Precedence:** `3` > `1` > `2` > `0`. Data you cannot trust outranks a verdict you cannot trust.

**`--allow-inconclusive` changes exactly one row:** exit `2` becomes `0` (this is the pre-launch
CI mode, where `n = 0` is the correct state of the world); exit `1` and `3` stay non-zero. It must
**not** be enabled on the release branch — that is the difference between "we have no users yet"
and "we stopped looking".

**(b) Gate table — value, threshold, minimum sample, and the unit `n` counts.**

| gate | measured | fails when | min `n` | `n` counts |
|---|---|---|---|---|
| `prettify_p50` | p50 `prettify_latency_ms` | > 20 000 ms | 30 | **items** |
| `prettify_p90` | p90 `prettify_latency_ms` | > 35 000 ms | 30 | **items** |
| `tooltime_p50` | p50 `tool_time_ms` | > 35 000 ms | 30 | **items** |
| `tooltime_p90` | p90 `tool_time_ms` | > 60 000 ms | 30 | **items** |
| `attrs_p90` | p90 `attrs_latency_ms` | > 8 000 ms | 30 | **items** |
| `export_p90` | p90 `export_latency_ms` | > 5 000 ms | 30 | **items** |
| `fields_mean` | mean `fields_touched` | > 5.0 | 30 | **items** |
| `fields_p90` | p90 `fields_touched` | > 12 | 30 | **items** |
| `activation_7d` | sellers with ≥3 `export_clicked` in 7 days ÷ sellers with ≥1 | **reported, not gated** until a baseline exists | 20 | **sellers** |

`fields_p50` and `export_burst_60m` are **printed, never gated** (p50 is distorted by blind
acceptance; the burst is a QA proxy with no meaningful threshold yet).

**`n` counts items, except activation, which counts sellers.** Mixing them is the easiest way to
produce a confident wrong number: 30 items may be 3 sellers, and 3 sellers is not a population.

**(c) `tool_time_ms` construction — the join nobody specified.**

    for each item:
        approved = the prettify_approved event for that item with decision == "approve"
        exported = the export_clicked event for that item
        if approved.prettify_latency_ms is None or approved.attrs_latency_ms is None: skip (count as incomplete)
        elif exported is None or exported.export_latency_ms is None: skip (count as not-yet-exported)
        else: tool_time = approved.prettify_latency_ms + approved.attrs_latency_ms + exported.export_latency_ms

    completeness = items_with_tool_time / items_that_reached_prettify_approved

    if completeness < 0.80: exit 3     # too many holes to trust any percentile

Two traps this closes. First, **an item that was approved but never exported has no tool time** —
excluding it is correct, but excluding it *silently* while `n` still reaches 30 means the gate
describes only the sellers who finished, i.e. the fastest ones. Hence the completeness check
losing to exit 3 rather than quietly passing. Second, **retries produce several
`prettify_approved` events per item** — take the `decision == "approve"` one, and count the item
**once**, or retried items get over-weighted in exactly the percentile we care about.

**(d) Dedupe and window rules.**

- One row per **item_id**, never per event. Latest event per `(item_id, event_name)` wins.
- Window is **half-open on UTC**: `ts >= now() - 7 days AND ts < now()`. State it, because
  "since 7d" is otherwise implemented three ways in three repos.
- Items that hit `failed` with `bad_photo` / `no_garment` / `low_confidence` / `vton_rejected`
  are **excluded from latency and fields gates** (no reviewer time happened, or no output existed),
  and **counted separately** as the failure mix. They are never silently folded into a denominator.
- Refunded or deleted items are excluded and reported as `excluded_refunded=<n>`.

**(e) Percentile definition — pin it, do not let it be a default.**

    nearest-rank, on values sorted ascending, 0-based index = ceil(p * n) - 1
    p50 of [1,2,3,4] = index ceil(2.0)-1 = 1 -> 2
    p90 of 30 values  = index ceil(27)-1 = 26 -> 27th smallest

Nearest-rank, not linear interpolation, because the gate must be reproducible by a human with a
spreadsheet and because interpolation invents a latency that no item had. Print the method string
(`percentile=nearest-rank`) in the report header so a future disagreement is settled by reading,
not by arguing.

**(f) Output shape — three fields per row, and `UNMEASURED` must not look like `PASS`.**

```
gate               n     value      threshold   verdict
prettify_p50       34    18342 ms   <= 20000    PASS
prettify_p90       34    41207 ms   <= 35000    FAIL
tooltime_p90       12    -          <= 60000    UNMEASURED (n=12 < 30)
fields_p90         34    9          <= 12       PASS
activation_7d      4     -          report only UNMEASURED (n=4 sellers < 20)
completeness       34    0.94       >= 0.80     PASS
```

Rules: `value` is `-` when `n` is insufficient (never a number computed from a sample we just
called insufficient); `UNMEASURED` must differ from `PASS` in text **and** colour; the report
prints the window, the percentile method, `git SHA`, and the completeness ratio.

**(g) Activation has no reporting surface today — this is the line that fixes it.**

`activation_items_exported_7d` exists as a constant with tests (`CONTRACTS:340`,
`packages/shared/src/analytics.ts`) but **no ticket reports it**. It is my #2 weekly number
(`METRICS §6`). Row `activation_7d` in (b) is the whole fix: sellers with ≥3 `export_clicked`
in 7 days ÷ sellers with ≥1, `n` in **sellers**, min 20, **reported not gated** until a baseline
exists. Do not gate it — we have never observed it and a threshold would be invented.

**END OF PASTE**

### 12.3 What is left after the rulings

| item | owner | status |
|---|---|---|
| `CONTRACTS:358` "outbound claim" → internal-assumption wording (the R1 hole's last file) | architect (frozen?) | **open, one line** |
| §12.2 spec pasted into `PLAN` E0-T5, plus the `activation_7d` row (g) | architect | **open, spec provided here** |
| `CONTRACTS:320` "touches the sheet" → "opens the page (counts may be 0)" | architect | **open**, my `US-3`/`METRICS` are correct under either reading but the bias note stays until it lands |
| Wall-clock per stage printed in E1–E4 eval reports (no threshold) | lead / vision | **open**, cheapest before E1 starts |
| `listing_confirmations` as a table, not a 7th event | product (me) | post-v1, by design |
| Q1 (8 vs 19.5 min) | data | open until interviews run |
| Interview incentive $600 | lead → Bert | `AWAITING-APPROVAL` |

### 12.4 Command output, this pass

```
$ wc -l docs/PRD.md docs/METRICS.md docs/ONBOARDING.md docs/INTERVIEWS.md
    539 docs/PRD.md
    312 docs/METRICS.md
    252 docs/ONBOARDING.md
    477 docs/INTERVIEWS.md
   1580 total
$ date -u +%Y-%m-%dT%H:%M:%SZ
2026-10-28T16:53:17Z
$ ls -la docs/PLAN.md docs/CONTRACTS.md | awk '{print $6,$7,$8,$9}'
2026-10-28 10:48 docs/CONTRACTS.md
2026-10-28 10:53 docs/PLAN.md
```

`PLAN.md`'s 10:53 mtime is **not mine** — I have never written to it. `METRICS.md` moved
311 → 312 (+1, the blind-acceptance denominator caveat). `PRD.md`, `ONBOARDING.md`,
`INTERVIEWS.md` unchanged this pass.

### 12.5 Correction — the target files moved while I was writing §12

**§12.1's R1 row and two rows of §12.3 are already stale.** They were accurate at 16:53Z; the
architect (or lead) edited `CONTRACTS.md` at **10:54Z** and `PLAN.md` grew to **728 lines**. A
handoff that leaves a stale "still open" is worse than one that says nothing, so here is the
correction with the same evidence discipline as the rest.

| §12 claim | Status now | Evidence |
|---|---|---|
| "`CONTRACTS:358` still says *unverified outbound claim* — the licence survives in the second file" | **CLOSED.** The phrase is gone. `CONTRACTS:369-371` now reads "It is an **internal** … in any seller-facing material — landing page, store copy, paywall, ads, sales DMs", and defers to `ONBOARDING §4.1` as stricter | `CONTRACTS:369-371` |
| "`CONTRACTS:320` says 'touches the sheet' — ambiguity open" | **CLOSED, and better than I asked for.** It now specifies the emission moment as well as the condition: *"emitted when it is dismissed (leaving the screen or losing focus), one event per item per visit, `fields_touched` may be `0`"* — and `CONTRACTS:332-337` records the reasoning (change-only firing makes `fields_touched >= 1` by construction, which kills the blind-accept rate) | `CONTRACTS:320`, `332-337` |
| "dual gate still to be applied" | **CLOSED.** `PLAN:20`, `PLAN:63`, `PLAN:406` and `CONTRACTS:360-361` all carry `mean ≤ 5 AND p90 ≤ 12`, p50 report-only, with the dilution reasoning written down | those lines |
| "`PLAN:21` internal-only — half done" | **CLOSED at both files.** `PLAN:22-26` internal-assumption + stricter-authority clause; `CONTRACTS:369-371` matches | those lines |
| line numbers cited in §11/§12 | **`PLAN.md` 682 → 728 lines**, so citations after ~line 300 have shifted by up to +46. `E0-T5` acceptance is still `PLAN:136-155` (unchanged region); `E6-T3` is now ~`PLAN:398-410` | `wc -l docs/PLAN.md` |

**R1 and the `attributes_edited` semantics are now fully closed. Two items from §12.2 remain
genuinely unaddressed in `PLAN` — I re-checked each rather than assuming:**

1. **Exit-code semantics.** `PLAN:140-155` says "exits non-zero" and `n < 30` exits non-zero, but
   does not distinguish **regression (1)** from **no data yet (2)** from **bad data (3)**. Without
   that split, the gate is red on an empty database, and a gate that is red before the first user
   arrives gets commented out — after which it protects nothing. §12.2(a) is the fix, including
   `--allow-inconclusive` for pre-launch CI.
2. **Activation still has no reporting row.** `activation_items_exported_7d` is mentioned only in
   the "three different threes" note (`PLAN:91`); `E0-T5`'s DoD still prints tool time,
   fields-touched and export burst only. §12.2(g) is the one-line fix.
3. **E1–E4 still have no wall-clock requirement** (verified: no `wall-clock` / `per-stage` string
   anywhere in `PLAN`). This is the item I would do first, because it is the cheapest and it is
   the one that catches a 90-second pipeline in week 2 instead of at E6-T4.

**One residual risk introduced by the new `CONTRACTS:320` wording, worth one line.** Firing on
**dismiss** means a force-quit or crash while the sheet is open emits nothing — so the
blind-acceptance denominator loses exactly those items. Direction is the same as the caveat
already in `METRICS §3.2`: the rate is biased **low**, and any figure under 30% should be read as
a lower bound on skipping. Mitigation is free: the mobile client should emit on **screen blur as
well as on dismiss**, which `CONTRACTS:320` already allows ("leaving the screen **or** losing
focus"). No contract change needed — just do not implement it as dismiss-only.

---

## 13. Closing record — two review rounds, with ruling status (2026-10-28T16:57Z)

Final section. Everything below is either **已裁决 (ruled)** or **待 Bert 拍 (pending Bert)**;
there is no third state left open on my side. Line evidence is kept so each claim can be
re-checked without reading a chat log.

### 13.1 The two rulings that changed my documents

**Ruling A — `export_clicked` (attempt) vs `export_delivered` (a file exists).**
My proposal was to change `export_clicked`'s meaning in one word. **The lead's design is better
and I adopted it instead:** keep `export_clicked` as the attempt and add a **7th event**
`export_delivered`, with the north star and activation bound to the new one
(`EVENTS_VERSION 1.2.0`, `CONTRACTS:301`, `CONTRACTS:308`).

Why theirs is better, recorded because I was the one who proposed the weaker version: my
one-word fix would have made the number correct *by deleting the evidence*. "Tapped export and
got nothing" is a **product defect signal** — CSV generation failing, network dying, the sheet
abandoning — and collapsing the two events into one would have averaged that segment out of
existence in exchange for a cleaner metric. The pair keeps both: `export_clicked − export_delivered`
is now a number to watch weekly (`METRICS §3`), not a number to hide.

**`METRICS.md` synced (the only numeric edits authorised, all semantic, none cosmetic):**

| where | before | after |
|---|---|---|
| §1 north star | "successful `export_clicked`" | **`export_delivered`** |
| §1 active-seller definition | "≥1 successful `export_clicked`" | **≥1 `export_delivered`** |
| §2 activation | "3rd successful `export_clicked`" | **3rd `export_delivered`**; constant name `activation_items_exported_7d` kept frozen, mapping documented |
| §3 table | 5 rows, `export_clicked` = "file successfully produced" | **7 rows**; `export_clicked` = *attempt*, **`export_delivered` added** |
| §3.1 funnel | `… → export_clicked → subscription_started` | `… → export_clicked → export_delivered → subscription_started`, plus an explicit **failure branch** |
| §3.3 | `export_latency_ms` on `export_clicked`; tool time from `prettify_approved` + `export_clicked` | on **`export_delivered`** |
| §3.3 | `session_3_exports_rate` on successful `export_clicked` | on **`export_delivered`** |
| §4 rule 1 | server-side list = `export_clicked`, `subscription_started` | + **`export_delivered`, explicitly never client-only** (it is what the north star binds to) |
| §4 rule 5 | idempotency on `export_clicked` | split: attempt vs delivery, dedupe on `item_id` |
| §5 | "`paywall_impression` is the independent 6th event" | "one of the **two added** events; all seven frozen" |

`PRD.md`, `ONBOARDING.md`, `INTERVIEWS.md` were **not** touched in this pass. In particular
`ONBOARDING §4.1` stays at maximum strictness, as ruled — `<2 min` never appears in
seller-facing material, and I read "11 minutes saved" as covered by the same ban because it is
`19.5 − 8.5`, two assumptions subtracted.

### 13.2 Ruling status — the complete list

| # | item | status | where |
|---|---|---|---|
| R1 | `<2 min` as internal hypothesis only | **已裁决** — closed in **both** files (`PLAN:22-26`, `CONTRACTS:369-371`); `ONBOARDING §4.1` remains the strictest authority | §11.4, §12.5 |
| R2 | single item before batch | **已裁决** — PASS, no action | §11.1 |
| R3 | eBay CSV in first slice | **已裁决** — PASS, no action | §11.1, §11.3 |
| R4 | analytics in same PR | **已裁决** — PASS, no action | §11.1 |
| R5 | anonymous session | **已裁决** — PASS, no action | §11.1 |
| R6 | latency gate existed on paper only | **已裁决** — dual gate `mean ≤ 5 AND p90 ≤ 12` in `PLAN:20/63/406`, `CONTRACTS:360-361`; real gate at `PLAN:136-155`; **exit-code semantics still owed** (§12.2(a)) | §11.2, §12.2 |
| R7 | `reason_code` never user-facing | **已裁决** — PASS, no action | §11.1 |
| R8 | cost per job auditable | **已裁决** — PASS, no action | §11.1 |
| R9 | Poshmark/Vinted below eBay | **已裁决** — PASS; `insufficient_data` re-scoped to before-launch | §11.1, §12.1 |
| A | `export_delivered` as 7th event | **已裁决** — adopted, `EVENTS_VERSION 1.2.0` | §13.1 |
| B | third state (actually listed) | **已裁决** — v2 `listing_confirmations` **table**, deliberately not an event | §11.5, §12.1 |
| C | activation has no reporting ticket | **已裁决** — row goes into `E0-T5`, PASS/FAIL/`UNMEASURED`, n<30 unmeasured | §11.5, §12.2(g) |
| D | `make evals` (no gate) vs `make evals --strict` (gate) | **已裁决** — global rule, my `--allow-inconclusive` design superseded; adopted theirs | §13.3 |
| E | exit-code semantics 0/1/2/3 + percentile definition + tool-time join/completeness | **已裁决 in principle, not yet landed** — spec ready at §12.2, architect frozen | §12.2 |
| F | interview incentive **$600** (12 × $50) | **待 Bert 拍** — `INTERVIEWS §1.4` marked `AWAITING-APPROVAL`; interviews cannot start until cleared | §1.4 |
| G | VLM provider selection | **待 Bert 拍** — named by the lead. **No detail exists in my documents and I add no assumption here**; it is a vision/architect decision that reaches me only as an accuracy input to `METRICS §3.2` | — · **see §13.2.1 (2026-11-08)** |

**Nothing else is pending on the product side.** The two `待 Bert` items are both outside my
authority, and neither blocks E1.

### 13.2.1 状态更新（2026-11-08）

**This dated addendum is the current status. The ruling text in the §13.2 table and the
sentence immediately preceding it are preserved verbatim as the 2026-10-28 closing record;
only a dated pointer marker was added to the G-row reference (`where`) cell. No original ruling
was silently rewritten or deleted.**

* **G is no longer `待 Bert 拍`.** Bert ruled the VLM provider selection on **2026-11-04**:
  **DeepSeek V4.1 Flash**, recorded in `docs/ADR-006-attributes-vlm-provider.md` (API model id
  `deepseek-flash`, base URL `https://api.deepseek.com`). A non-empty `VLM_API_KEY` is configured
  in the local `.env`, a real provider call has run, and E3-T3 accuracy was measured on this
  machine (`evals/reports/vision.md`, a gitignored local report): category 0.9250, colour 0.6500,
  pattern 0.9750, material 0.2750, dedupe false-merge 0.0000. Three of the four attribute
  targets are NOT met; the figures are local, uncommitted, and fall back to `UNMEASURED` on a
  clean checkout. This is a measured accuracy input to `METRICS §3.2`, not a product decision
  reopened here.
* **F is still `待 Bert 拍`.** The **$600** interview incentive (12 × $50) remains
  `AWAITING-APPROVAL` in `INTERVIEWS §1.4`; interviews cannot start until Bert clears it.
* **So the "two `待 Bert` items" sentence above describes 2026-10-28, not today:** as of this
  addendum only **F** remains outside my authority. Neither F nor the resolved G ever blocked E1.
* For the same provenance reason, §13.3's `make evals --strict` wording is left as the
  2026-10-28 record; that flag does not exist in `Makefile`, where the release gate is the
  separate target `make evals-strict` (`Makefile:139`). The E0-T5 telemetry gate **is**
  implemented (`evals/telemetry/gate.py`); `make evals` now runs
  `exec $(PY) -m evals.telemetry --since 7d --gate --allow-inconclusive`
  (`Makefile:84`, with `PY` defined at `Makefile:9`), measured on this machine as
  `PASS=0 FAIL=0 INCONCLUSIVE=8`, exit 0. It must not be described as unimplemented.

### 13.3 The `make evals` / `--strict` split — adopted, with two consequences（2026-10-28 record; see §13.2.1）

Ruled: dev/CI runs `make evals` (no gate; unmeasured exits 0 and "does not count as a pass"),
release runs `make evals --strict` (unmeasured or over ⇒ exit 2). This replaces my
`--allow-inconclusive` design and achieves the same goal with fewer flags; **adopted as-is.**

Two consequences worth one line each, because they are where this pattern usually leaks:

1. **"Does not count as a pass" cannot be enforced by an exit code that is 0.** Most CI UIs paint
   a zero green; the distinction then lives only in the report body, i.e. it depends on someone
   reading it — the exact failure this whole round was about. Cheap guard: the report must print
   a verdict banner (`GATE NOT EVALUATED — n=0`), and the **release checklist must require the
   pasted `--strict` output**, so a release cannot be signed off with a dev-mode green.
2. **`--strict` collapsing "unmeasured" and "over" into one exit code hides which one happened.**
   For a release gate that is acceptable — both block — but the printed reason must differ
   (`FAIL` vs `UNMEASURED`), or six weeks later we cannot tell "we shipped blind" from "we
   shipped slow", and those have different fixes. `PLAN:145-147` already requires the three-state
   display, so this is satisfied at the report layer; it is the exit code that is coarse.

### 13.4 One correction I did not accept, with the quote

The ruling message says I wrote "`--strict` 之前跑的是 `make evals`，它会因为开发机慢而红". **That
sentence is not in any of my documents.** What is in them:

- `§11.2`: *"Gating **quality work** on latency measured on an unrepresentative dev machine is how
  a plan gets stuck"* — an argument against putting a **p90 threshold on E1–E4**, not a claim about
  `make evals`;
- `§12.2(a)`: *"a gate that is red on an empty database gets disabled within a week"* — an argument
  about **n = 0**, which the `--strict` split now solves.

Neither predicts a red `make evals` from a slow machine, and the `--strict` design is not a
rebuttal to anything I argued. Flagging it only so the record does not carry a correction for a
claim that does not exist — if that sentence came from another role, it belongs in that role's
handoff. This is the second misattribution in this round (§12.1 records the first, R5/R9).

### 13.5 What both rounds had in common — the standing rule I would keep

R6 was *"a dashboard impersonating a gate"*. The export finding was *"an attempt impersonating a
success"*. Same shape: **the metric existed in prose and not in mechanism.** In both cases every
document was internally consistent, the vocabulary was frozen, the property was emitted — and the
number would still have been wrong, in a direction nobody could detect afterwards.

So the rule I would keep for every future metric, three columns or it does not ship:

| required | question | R6 before the fix | export before the fix |
|---|---|---|---|
| **producer** | which event carries it, emitted by whom | latency props existed | `export_clicked` existed |
| **reporter** | which ticket prints it, with `n` | `E0-T5` printed it | none printed it |
| **red condition** | what fails, visibly, when it is bad | **nothing** | **nothing** (attempt counted as success) |

The failure is always the third column, because the first two are the easy, visible ones and the
third one only fires when something is already wrong.

### 13.6 Final state

```
$ wc -l docs/PRD.md docs/METRICS.md docs/ONBOARDING.md docs/INTERVIEWS.md
    539 docs/PRD.md
    346 docs/METRICS.md
    252 docs/ONBOARDING.md
    477 docs/INTERVIEWS.md
   1614 total
$ date -u +%Y-%m-%dT%H:%M:%SZ
2026-10-28T16:57:02Z
$ grep -n 'EVENTS_VERSION' docs/CONTRACTS.md | head -3
301:## 7. Analytics event vocabulary — frozen, own version (`EVENTS_VERSION = 1.1.0`)
308:Current: `EVENTS_VERSION = 1.2.0` (`1.1.0` = six events; `1.2.0` adds `export_delivered`, H below).
446:**Known drift, tracked:** `packages/shared/src/analytics.ts` is still at `EVENTS_VERSION = 1.1.0`
```

**One known drift, recorded rather than glossed:** `CONTRACTS` is at `1.2.0` while
`packages/shared/src/analytics.ts` is still at `1.1.0` (`CONTRACTS:446` says so itself). Until the
code catches up, the 7th event cannot actually be emitted — so every number in `METRICS §1`–§3
that now binds to `export_delivered` is, today, **a specification and not a measurement**. That is
the correct state for a pre-implementation repo, and it must not be quoted as if it were data.

`METRICS.md` moved 312 → 346 (+34) in this pass, all of it the `export_delivered` sync.
`PRD.md` 539, `ONBOARDING.md` 252, `INTERVIEWS.md` 477 — unchanged. `PLAN.md` and `CONTRACTS.md`
were read, never written, by me at any point in these two rounds.

Per instruction: **stopping here.**
