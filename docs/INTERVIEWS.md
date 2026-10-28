# SnapList — SELLER INTERVIEW SCRIPT & SCORING SHEET

**Owner:** 许清楚 (Head of Product) · **Date:** 2026-10-28
**Purpose:** convert the ASSUMPTION labels in `docs/PRD.md` into measured inputs **before we
build anything else**. This is the cheapest document in the repo and the one that decides
whether the rest of it is true.

**Status: not yet run. N = 0. Every input below is still an assumption.**

---

## 0. What this study must decide

Six hypotheses, each with the interview question that tests it and the gate that decides it.
The gates are fixed **now**, before data, so that the result cannot be rationalised later.

| # | Hypothesis (current assumption) | Tested by | GO if | KILL / REVISE if |
|---|---|---|---|---|
| **H1** | Baseline listing time is **~19.5 min/item** (`PRD §3.2`), not the **~8 min** in `BRIEF.md` line 14 | Q3 + Q4 | median ≥ **16 min/item** ⇒ 19.5 stands, BRIEF's 8 is wrong | median < **10 min/item** ⇒ BRIEF is right, redo `PRD §4` value math (it shrinks ~2.4×) and re-decide whether casual converts |
| **H2** | Sellers **already pay** for listing tools monthly | Q6 | ≥ **7 of 12** name a specific paid tool with a specific price | < **5 of 12** ⇒ "they already pay for tools" premise is weak; the product is a new budget line, not a substitution — harder sale, revisit pricing |
| **H3** | The honest gain is **11 min/item** because sellers still verify measurements and specifics | Q8 + Q7 | median measurement time ≥ **3 min/item** AND ≥ **6 of 12** report a return/complaint from a wrong attribute | < **4 of 12** with a specific return ⇒ accuracy risk is lower than assumed ⇒ measurement autonomy can be **P1 not P2** |
| **H4** | Sellers will pay **$19–49/mo** at these volumes | Q10 + Q11 | median stated WTP ≥ **$19** for a 100-item tier | median WTP < **$19** ⇒ pricing problem, escalate to team-lead before GTM; do **not** discount silently |
| **H5** | **On-model** is a real Pro differentiator | Q9 probe | ≥ **5 of 12** already do or have paid for on-model/worn shots | < **3 of 12** ⇒ on-model is not the Pro hook; find another one or ship Pro on volume alone |
| **H6** | Sellers will export a CSV and import it themselves | Q12 | ≥ **8 of 12** have imported a CSV/spreadsheet into a marketplace before | < **5 of 12** ⇒ CSV export is not the delivery mechanism; auto-publish moves up from P2 (and its legal/cost risk must be re-costed) |

**If H1, H2, and H4 all fail, stop the project and tell team-lead.** That combination means
the time saved is small, the tool budget does not exist, and the price is wrong. No amount of
model quality fixes all three.

---

## 1. Logistics — fix these before recruiting

| Item | Decision |
|---|---|
| **N** | **12 interviews**, minimum 8. This is a **qualitative** study: it finds the shape of the workflow and the failure modes. It does **not** produce percentages and must never be reported as "X% of sellers said". Report **medians and verbatim quotes** |
| **Length** | **35 minutes booked, 32 minutes scripted, 3 minutes buffer** (verified: `3+2+2+5+2+2+2+2+2+3+3+2+2 = 32`) |
| **Format** | Video call, 1:1, **single moderator. No second interviewer and no note-taker on the call** (team-lead ruling 2026-10-28). A 35-minute call with two of us in it doubles the scheduling failure rate and buys nothing the transcript does not already give us. The moderator talks; the recording captures |
| **Recording** | **The recording is the data point.** Only with explicit consent (§1.3). If they decline, continue — they may still be worth talking to — but mark the record `TRANSCRIPT: none / notes-only` and downgrade every timing figure from that call to `LOW CONFIDENCE` |
| **Transcription** | After the call: local ASR to a verbatim transcript at `docs/research/P__-transcript.txt`, **git-ignored and never committed**. Pipeline and the mandatory audio-check are in §1.3 |
| **Participant data** | A recording and its transcript are **personal data**. Consent to record is **not** consent to publish. No audio, transcript, or attributable quote leaves `docs/research/` without a separate consent line. Rules: `METRICS.md` §4 rule 7 |
| **Incentive** | Disclose it in the screener. Amount `UNMEASURED`/to be set by team-lead — do not promise a number in the script until it is approved |
| **Recruitment** | Post in reseller communities and DM warm leads. **Screen on Q-S1 to Q-S3 first**, then book — do not interview someone who fails the screener |
| **When** | Before any further feature work. The vision/architect work can proceed in parallel; the **pricing and GTM** cannot |

### 1.1 Screener (ask in writing, before booking — not part of the 12)

- **Q-S1:** Which marketplaces do you sell on, and how many active listings do you have right now?
- **Q-S2:** Roughly how many items did you list in the last 30 days?
- **Q-S3:** Do you list on a phone, a laptop, or both?

**Include** if: ≥ **15** items listed in the last 30 days, sells on **≥ 2** marketplaces,
takes their own photos. **Exclude** if: sells only in person, has staff who do the listing
for them, or is a friend/colleague of the interviewer (bias).

### 1.2 Recording kit — check before every call

Single moderator means **nothing is captured except the recording**. If the recording fails,
the interview is worth much less. Run this list in the 2 minutes before the call:

- [ ] Recorder running and **tested with a 10-second playback** — not "probably recording".
      A silent first interview is the most common way to lose a day.
- [ ] Mic close to the speaker (headset or laptop mic, not the far-field room mic).
- [ ] Participant asked to wear headphones — kills echo on the transcript.
- [ ] Quiet room; phone on silent; notifications off on the recording device itself.
- [ ] **Screen share offered** (Q2 and Q8 depend on seeing the actual listing screen). Any
      screen capture is *their* data — it follows the same consent line as the audio.
- [ ] File named `docs/research/P__-YYYYMMDD.m4a` **the moment the call ends**, before
      anything else.
- [ ] Backup rule: if the recording dies mid-call, **say so immediately**, tell them you will
      continue with written notes, and either continue or reschedule. **Do not reconstruct a
      transcript from memory afterwards** — a plausible-sounding invented quote is worse than
      a missing one.

### 1.3 Transcription workflow, and the consent script

**Pipeline (same day as the call, while you still remember the audio):**

1. Local ASR (the `AudioTranscribe` capability on this machine) → verbatim transcript.
   Chinese and English are both supported; set the language explicitly rather than relying
   on detection.
2. **Mandatory audio-check pass — this is the step that replaces the note-taker.** ASR will
   mangle exactly the data we care about: "pit-to-pit", "Gildan", "£19", "hem", "Vinted",
   "three hundred a month". Read the transcript against the audio and correct by ear:
   - every **number** in F1–F13 (it is data, not prose);
   - every **tool name and price** (Q6 — this is our only real competitive pricing data);
   - every **marketplace name**.
   Anything you cannot resolve by ear is recorded as `[unclear]` and the field is marked
   `LOW CONFIDENCE`. **Never guess a number into the scorecard.**
3. Timestamp every F-field number as `~14:20` so it stays auditable months later.
4. Single speaker, so speaker separation is not needed; do not bother labelling turns.

**The cost of going single-moderator, stated plainly:** you will not notice that a number
sounded like a guess until you hear it back. That is why step 2 is not optional, and why any
call without a usable recording is downgraded rather than trusted. The trade is deliberate
and cheap — one audio-check pass is ~20 minutes, versus a second person's full 35-minute slot.

**Consent script — say this verbatim at 0:00, before anything else:**

> "There's no product in this call and nothing to buy. I'd like to record so I can quote you
> accurately instead of relying on my notes. The recording gets transcribed and kept private
> — only anonymised quotes go into our internal notes, and your name, shop and town won't
> appear anywhere. You can ask me to pause or delete it at any time, and you can skip any
> question. Is it alright if I record?"

- If **yes** → start recording, and say the date and participant number out loud on the
  recording itself ("P03, 28 October").
- If **no** → "No problem, I'll take notes instead. Same questions." Record as
  `TRANSCRIPT: none / notes-only`, and downgrade timing fields.
- If **yes then later no** → stop recording immediately, keep what was said before the
  withdrawal only if they agree, and note the timestamp of the withdrawal.

### 1.4 Incentive and recruitment — **`AWAITING-APPROVAL`**, do not recruit against this yet

Team-lead cannot approve spend; this goes to Bert. Everything below is a **proposal**, not an
approved budget. **No outreach happens until it is approved.**

| Item | Proposal | Basis |
|---|---|---|
| Incentive | **$50 gift card per completed 35-minute interview** | A *budget request*, not a market rate. Implied `$50 ÷ 0.583h ≈ $85/h` — high enough to read as a courtesy to a working seller rather than an insult, low enough that all 12 calls cost less than one engineering day. **We have no data on what this population accepts** |
| Range worth considering | $40 – $75 | If the first outreach round yields < 3 bookings, go to **$75 before widening channels** — chasing volume with more channels before fixing the offer is how recruitment burns a week |
| N | 12 completed (minimum 8 usable) | See §0. Budget **completed interviews**, not contacts |
| Total incentive ask | **$600** for 12 completed | Plus moderator time (~35 min + ~20 min transcription each ≈ **11 h** total). `UNMEASURED` in dollars until someone's rate is agreed |
| Channels | (a) reseller subreddits / Discords / FB groups — 3 posts, **no paid placement**; (b) warm DMs to sellers already known to the team; (c) DM small reseller creators for a 1:1 — **not** a sponsorship | All channels need team-lead clearance before contact |
| Outreach volume needed | **`UNMEASURED`** — we have no response-rate data, so contacts-per-booking is unknown | Do not invent a funnel multiplier. Report actual contacts after round 1, then size round 2 |
| Explicit non-goal | **No paid panel vendor** | Unbudgeted, and panel-sourced "resellers" are not this ICP — they are people who answer surveys |

**Anti-fabrication guard on this number:** the $600 is **not** a CAC and must never be
reported as one. `$600 ÷ 12 = $50` is the cost of one *pre-launch research conversation with
someone who has not bought anything*, and there is no conversion to divide by. If a "$50 CAC"
appears in any deck, it came from here and it is wrong.

---

## 2. Rules of the room (these protect the data)

1. **Do not describe SnapList or show any screen for the first 25 minutes.** The moment they
   know what we are building, Q3–Q9 become theatre. Q10–Q12 are the only place the idea is
   allowed to exist.
2. **Ask about the past, never about the future.** "How long did the last one take" — not
   "how long would this take". Future-intent answers are recorded **as stated intent** and
   never as fact (that is why H4 uses "stated WTP", not "will pay").
3. **Never ask a question that contains the answer.** Ban the words: *easy, fast, convenient,
   automatic, save time, would you use, do you like*. If a quote contains them, mark it as
   prompted.
4. **Silence is a tool.** After an answer, wait 3 seconds. The second sentence is usually the
   real one.
5. **Ask for the artefact.** "Show me the listing" beats "describe the listing" every time.
   Ask them to share their screen — a live walk-through of one real listing is worth three
   questions.
6. **Every number gets a timestamp.** When they say a figure, write the clock with it:
   `~14:20 "about ten minutes"`. With no second person in the room, **the transcript is your
   note-taker** and the timestamp is how you get back to it. Never round to a mean while the
   call is running, and never write a number you cannot point at in the audio.

---

## 3. The 12 questions

Each block has: the question (say it as written), probes (only if needed), what it tests, and
what a strong vs. weak answer looks like.

---

### 0:00–0:03 — Intro & consent (3 min)
> "Thanks for the time. I'm researching how resellers handle the listing work — not selling
> you anything, and there's nothing to buy in this call. I want to hear what's actually
> annoying. Can I record so I don't misquote you? You can say skip at any point."

---

### Q1 (2 min) — Volume, concrete
> **"Take me to last week. How many items did you actually get listed, day by day?"**

- *Probes:* "Was last week typical? What was different about it?"
- **Tests:** H1 input; whether 20/120/400 is real.
- **Strong answer:** specific days and counts, and they know it without checking.
- **Weak answer:** "a lot", "depends", or a monthly number that changes when re-asked. Mark
  `LOW CONFIDENCE` — the persona bands in `PRD §2` are built on this number.

---

### Q2 (2 min) — Channels and the duplication cost
> **"Of your last 10 listings, where did each one go? And what's different about how you list
> it in each place?"**

- *Probes:* "Which fields do you retype per platform? Show me the same item on two platforms."
- **Tests:** H6 (CSV/multi-channel need), and the real cost of item specifics (`PRD §3.2` step 6).
- **Strong answer:** names actual differing fields and expresses irritation about them.
- **Weak answer:** "they're all the same really" ⇒ single-marketplace mapper is enough.

---

### Q3 (5 min) — ★ The step-by-step time budget (the most important question)
> **"Pick the last item you listed. From the moment you picked up the phone to the moment you
> hit publish — walk me through it, step by step. Don't skip the boring parts."**

- *Probes, one per step, timed as they talk:* "How long did the photo take? How many frames
  did you take? What did you do with the photo before uploading? Did you measure it — with
  what? How long on the title? How many dropdowns did you fill? Did you check sold prices?
  Then what?"
- **Tests:** H1 and H3 directly. This question produces the actual per-step minutes that
  `PRD §3.2` currently only assumes.
- **Strong answer:** an unprompted narrative with friction in it ("then I realise the tag is
  inside out and go back").
- **Weak answer (red flag):** a suspiciously smooth, tidy description — they are describing
  the ideal workflow, not last Tuesday. Re-ask with "and what went wrong on that one?"

> **Moderator: after the call (not during), fill one row per step of the `PRD §3.2` table from
> the transcript, using the seller's own minutes — with the timestamp next to each one. Do not
> help them add up, and do not tidy their "about ten" into "10".**

---

### Q4 (2 min) — The top-down check (this is the 8-vs-19.5 tie-breaker)
> **"Think of your most recent listing session — one sitting. What date was it, when did you
> start, when did you stop, and how many items did you get through?"**

- *Probes:* "Did you stop for anything in the middle — dinner, a message, a return?"
- **Tests:** H1 from the other direction. `Q4 minutes ÷ Q4 items` is the top-down per-item
  number; if it disagrees with Q3's bottom-up sum by more than 40%, **both are suspect** and
  we need at least two more Q4s.
- **Why it matters:** `BRIEF.md` says ~8 min. This one question decides whether the entire
  value math is 2× overstated.

---

### Q5 (2 min) — Abandonment
> **"Tell me about the last item you gave up on, or that's still sitting in drafts. What
> happened?"**

- *Probes:* "How long has it been sitting there? Why that one?"
- **Tests:** where the funnel actually breaks; whether the blocker is effort, information
  (can't identify the fabric), or confidence (don't know the price).
- **Strong answer:** a specific item, still in drafts, with a reason.
- **Weak answer:** "I don't really abandon things" ⇒ ask to see the drafts folder.

---

### Q6 (2 min) — What they already pay for (tests H2)
> **"What do you pay for each month that touches listing? Walk me through the last bill —
> name, amount, and whether you'd cancel it."**

- *Probes:* "What did you try and cancel? Why?"
- **Tests:** H2. Also gives us the **real** competitive set and real price anchors — the only
  competitor pricing data we will have that is not fabricated.
- **Strong answer:** names tools and prices from memory, has an opinion on each.
- **Weak answer:** "nothing, I use free stuff" ⇒ H2 in danger. Follow up: "what's the free
  thing, and what does it not do?"

---

### Q7 (2 min) — The cost of being wrong (tests H3)
> **"Tell me about a return or a complaint caused by something wrong in one of your listings.
> What was wrong, and what did it cost you?"**

- *Probes:* "Was it a measurement, a material, a stain you missed, or the colour? What
  happened after — refund, partial, bad feedback?"
- **Tests:** H3. This is the question that decides whether attribute accuracy is a **P1**
  feature or a **nice-to-have**. If sellers lose real money on wrong measurements, we cannot
  auto-apply them (`PRD §6`).
- **Strong answer:** a specific garment, a specific dollar loss.
- **Weak answer:** "it happens" with no example ⇒ accuracy risk lower than assumed, but
  probe once: "what about the last one that came back?"

---

### Q8 (2 min) — Measurement behaviour (tests H3)
> **"Do you measure every item? Show me the measurement fields on your last listing."**

- *Probes:* "When do you skip it? Do buyers ask for measurements you didn't give? How long
  does measuring take you, honestly?"
- **Tests:** H3. Also tests whether a *suggested* measurement would be checked or trusted —
  the crux of `PRD §3.3`.
- **Strong answer:** shows the screen, has a routine (pit-to-pit, length, sleeve), names the
  time.
- **Weak answer:** "I measure if it seems expensive" ⇒ segment by item price; measurement
  autonomy matters less on cheap items.

---

### Q9 (3 min) — Photo workflow + the on-model probe (tests H5)
> **"How many photos do you take per item, and how many do you keep? What do you do to the
> ones you keep before they go up?"**

- *Probes:* "What app? What does it get wrong? How often do you re-shoot?"
- **Then the H5 probe, still in past tense:**
  > **"Do you ever show the garment on a person? Have you ever paid for that — a model, a
  > mannequin, a service?"**
- *Probes:* "What did it cost? Would you do it for every item or only the expensive ones?"
- **Tests:** the shoot/cull/background budget in `PRD §3.2` steps 1–3, **and** whether
  on-model is a real Pro hook (H5) or a feature we are inventing for ourselves.
- **Strong answer (H5):** already owns a mannequin, or has paid someone, or has tried a VTON
  app and rejected it — and can say why.
- **Weak answer:** "no, never really thought about it" ⇒ on-model is a nice-to-have; Pro
  must be differentiated on volume, and `PRD §2.2`'s "on-model" justification weakens.

---

### Q10 (3 min) — Stated willingness to pay (tests H4)
> **"What's the most you've ever paid per month for a tool related to selling? What made you
> keep it — and what made you cancel the last one?"**

- *Probes:* "If something halved your listing time, what would you expect it to cost per
  month? What would make that feel too expensive? At what price would it feel suspiciously
  cheap?" *(that last one is the van Westendorp "too cheap" point — it catches tools that
  get priced into irrelevance)*
- **Tests:** H4. Answers are **stated intent**, recorded as a band, never as a fact. Do not
  average them; report the median band.
- **Strong answer:** an existing paid tool at a real price, plus a clear reason.
- **Weak answer (very common):** "depends what it does" ⇒ push once with "say it does exactly
  what I described at the start — what would you pay?"

---

### Q11 (2 min) — What an hour is worth (the `PRD §4.1` assumption)
> **"If you couldn't do the listing work yourself for a month, what would you have to pay
> someone to do it? Or — what's an hour of your listing time worth to you?"**

- *Probes:* "Do you have help? What do you pay them?"
- **Tests:** `seller_hourly_value`, currently **$25/h ASSUMPTION** with a $15–40 range.
  This is the multiplier on the entire value math, and it is currently made up.
- **Strong answer:** a real wage they have paid, or a real alternative use of the hour.
- **Weak answer:** a round guess ⇒ record as a band and mark `LOW CONFIDENCE`; the $15–$40
  sensitivity table in `PRD §4.3` already covers the range.

---

### Q12 (2 min) — Switching (tests H6)
> **"Last one. What would have to be true for you to change how you list, next month? And
> what's stopped you from changing it before?"**

- *Probes:* "Have you ever imported a spreadsheet or CSV into a marketplace? How did that go?
  Would you rather upload a file or have it posted for you?"
- **Tests:** H6 — the delivery mechanism. If CSV import is unfamiliar or scary, the export
  story in `PRD US-5` is wrong for this segment and auto-publish's priority changes.
- **Strong answer:** has done a bulk CSV import and has opinions about column names — that
  is our user.
- **Weak answer:** "I just do it one at a time" ⇒ CSV is not the unlock; the *attribute
  prefill* is, and CSV slips to P1.

---

### 3.1 Close (inside the 3-minute buffer)
> "Anything I should have asked and didn't? Can I come back to you when I have something to
> show you?" *(consent to re-contact, separately from the recording consent)*

---

## 4. Scoring sheet — one per participant

Copy this block per interview. **Fill it within 30 minutes of the call with the transcript
open** — not from memory, and not "later this week". Every F-field number must carry the
timestamp you can replay (§2 rule 6). If the transcript is not yet done, mark the sheet
`PENDING TRANSCRIPT` and do not aggregate it.

```
Participant:  P__            Date: ______        Moderator: ______
Screener:     items/30d ____  marketplaces ____________  device ________
Recording:    yes / no        Re-contact: yes / no     Incentive: ______
Transcript:   docs/research/P__-transcript.txt    Audio-check pass done: yes / no
              (no audio-check pass ⇒ every timing field is LOW CONFIDENCE)

--- FACTUAL (from transcript, not judgement) ---
F1  items listed last 30d ................................ ___ 
F2  items in last session (Q4) ........................... ___
F3  session minutes (Q4) ................................. ___
F4  TOP-DOWN min/item  (F3 / F2) ......................... ___ min   <-- H1 check
F5  BOTTOM-UP min/item (sum of Q3 step rows) ............. ___ min   <-- H1 check
F6  Q4 vs Q3 disagreement  ((F5-F4)/F5) .................. ___ %     (>40% = both suspect)
F7  paid tools, name + $/mo (Q6) ......................... ______________________
F8  total current tool spend $/mo ........................ $___
F9  measurement time per item (Q8) ....................... ___ min
F10 stated WTP band (Q10) ................................ $__ - $__
F11 "too cheap" point (Q10) .............................. $___
F12 hourly value stated (Q11) ............................ $__/h
F13 ever done a bulk CSV import (Q12) .................... Y / N

--- JUDGEMENT (1-5, use the anchors in §4.1) ---
J1  Pain intensity ....................................... _/5
J2  Volume of listings (band) ............................ _/5
J3  Willingness to pay ................................... _/5
J4  Accuracy tolerance (would they check our output?) .... _/5
J5  Switch readiness ..................................... _/5
J6  On-model demand (Q9 probe) ........................... _/5
J7  ICP fit ............................................. PASS / FAIL

--- QUALITATIVE (verbatim from the transcript, with timestamp — never a summary) ---
Worst step in the workflow: "______________________________________"  ~__:__
Best quote about wasted time: "____________________________________"  ~__:__
Objection to paying: "_____________________________________________"  ~__:__
Surprise / thing we got wrong: "___________________________________"  ~__:__
Transcribed by: ____   Audio-check pass by: ____
```

**Quote hygiene:** a line only goes in the box above if you can play the audio back to it.
If it is a near-miss, mark it `(paraphrased)` — a paraphrased quote presented as verbatim is
the exact failure mode that produces invented user research later.

### 4.1 Judgement anchors — keep this table open on the moderator's screen, beside the scorecard

| Score | J1 Pain intensity | J3 Willingness to pay | J4 Accuracy tolerance | J5 Switch readiness | J6 On-model |
|---|---|---|---|---|---|
| **1** | Listing is fine, no complaint voiced | Nothing paid, refused to name a price | Would trust anything, checks nothing | Actively avoids changing anything | "Never thought about it" |
| **2** | Mild irritation, no cost stated | Would pay < $10/mo only | Checks only when a return happens | Has ignored tools before | Curious, never tried |
| **3** | Names a real cost, no urgency | Would pay ~$10–19/mo | Checks measurements, not attributes | Tried something once, abandoned it | Tried a free VTON, didn't keep it |
| **4** | Voluntarily raises it more than twice; lost money to it | Would pay ~$19–49/mo | Checks measurements and material | Currently switching between tools | Owns a mannequin or has paid for shots |
| **5** | Has bought something to fix it, or hired help | Would pay > $49/mo | Checks everything, has been burned | Actively shopping for a replacement | Pays for it per item today |

**J2 volume band:** 1 = <10/mo · 2 = 10–19 · 3 = 20–59 · 4 = 60–199 · 5 = 200+.

---

## 5. Analysis — how the 12 become decisions

Do this in one sitting, immediately after the last interview, with all transcripts and
scorecards open in front of you. (This is the one step that genuinely suffers from having no
second person in the room: the analysis is now a single person's read of 12 transcripts, so
write down your own prior **before** tabulating — see §5.2.)

1. **Tabulate F4 and F5** for all 12. Report **median F5** and **median F4** separately, and
   note how many participants disagreed by >40%. **Do not average the two numbers into one
   — explaining the gap is the finding.**
2. **Apply the six gates in §0.** Write GO / KILL / REVISE next to each, with the count that
   decided it. No narrative before the counts.
3. **Quote file:** every `Worst step` and `Objection to paying` verbatim, deduplicated. These
   become the copy in `ONBOARDING.md` §4 — only after the counts agree with them.
4. **Update the docs.** If H1 resolves, edit `PRD §3.2`, §4.2–4.5 with real inputs and change
   their labels from ASSUMPTION to MEASURED, citing participant count and date. **Leave the
   `BRIEF.md` conflict note in `PRD §0` until it is explicitly closed by team-lead.**
5. **Write the result into `docs/HANDOFF-product.md`** even if it is embarrassing — especially
   then. A killed hypothesis is the most valuable output this study has.

### 5.1 What N=12 cannot tell you (say this out loud in the readout)

- **No percentages.** "7 of 12" is a count of 12 people, not 58%. Never write 58%.
- **No segment sizing.** We cannot say how many sellers exist in each band. Still
  **UNMEASURED** (`PRD §4.6`) after this study.
- **No conversion prediction.** Stated WTP is not a purchase. The only WTP measurement that
  counts is `subscription_started` (`METRICS.md` §3).
- **No prioritisation by frequency.** 12 people cannot rank features by incidence; they can
  only reveal *mechanisms* and *failure modes*. Priors come from the gates, not the volume of
  enthusiasm.

### 5.2 Single-analyst guard — write your prior *before* you tabulate

Going from two people in the room to one moderator + a transcript (2026-10-28) also removed
the second reader at analysis time. One person reading 12 transcripts will find what they
expect to find. Cheap countermeasures, do all four:

1. **Before opening any transcript**, write down and timestamp: your predicted median F5, and
   which of H1–H6 you expect to fail. Keep it.
2. Tabulate from the transcripts only. Do not re-read your prediction until the counts are in.
3. **If your prediction matched, say so in the writeup.** A match is also a finding: it may
   mean the study confirmed a prior rather than discovered anything.
4. For the two highest-stakes fields — **F5 (minutes/item)** and **F10 (WTP)** — have a second
   person re-read 3 randomly chosen transcripts cold and re-score them. Disagreement > 20% on
   F5 ⇒ re-read all 12 together. This is the only place a second pair of eyes is still worth
   the cost.

---

## 6. Immediate asks before this can run

1. **Incentive budget — `AWAITING-APPROVAL`** (§1.4): $600 for 12 completed interviews. Not
   approved; team-lead cannot approve spend, it is with Bert. **Recruitment is blocked until
   this clears.**
2. **Recruitment channels** — which communities/DMs are cleared for outreach (§1.4). Needs
   team-lead. Clearance, not budget.
3. ~~A note-taker~~ — **resolved 2026-10-28**: single moderator + recording + local ASR
   transcription (§1.2, §1.3). The transcription audio-check pass replaces the note-taker.
   Accepted cost: any call without a usable recording is downgraded, not trusted.
4. **A transcription/privacy owner** — transcripts are personal data and must live only in
   `docs/research/` (git-ignored) under `METRICS.md` §4 rule 7. Someone has to confirm that
   folder is actually excluded before the first recording exists.
5. **A callback reminder in `PRD §0`**: until §5 step 4 happens, every number in the PRD is
   still a hypothesis with arithmetic attached.
