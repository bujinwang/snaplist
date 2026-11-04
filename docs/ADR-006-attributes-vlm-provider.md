# ADR-006 — attributes VLM provider: DeepSeek V4.1 Flash

- **Status:** accepted
- **Date:** 2026-11-04
- **Deciders:** Bert (product owner; RUNBOOK §6 reserved this decision for him), CV role (wiring)
- **Affects:** E3-T1/T2/T3, E7 (listing copy consumes attributes), E8 (cost ceiling), legal subprocessor register
- **Supersedes:** the "undecided" status in `docs/RUNBOOK.md §6`

## Decision

Use **DeepSeek V4.1 Flash** as the single VLM behind `AttributeExtractor`. Concretely:

| config | value |
|---|---|
| API model id | `deepseek-flash` (served by `DeepSeek-V4.1-Flash-1017`) |
| OpenAI-compatible base URL | `https://api.deepseek.com` (no `/v1`; adapter appends `/chat/completions`) |
| Vision | native multimodal; `image_url` base64 data URL in a `user` message |
| Structured output | `response_format={"type":"json_object"}` |
| Thinking | `{"thinking":{"type":"disabled"}}` — off, since default-on emits CoT and ignores `temperature` |
| Key env | `VLM_API_KEY` |

Note the model id is **`deepseek-flash`**, not `deepseek-v4.1-flash`. "V4.1 Flash" is the product
name; the API id is short. The legacy ids `deepseek-v4-flash` and `deepseek-v4-flash-vision-exp`
are retired and routed to the same model, but we use the current id so a later rename is visible.

Swapping providers remains a **config change**: all DeepSeek-specific request fields live in
`SNAPLIST_VLM_EXTRA_BODY` (`.env.example`), not in Python. No vendor name appears in
`packages/vision/`. This preserves E3-T1's DoD.

## Why this provider

- Native vision + JSON Output on the same OpenAI-compatible endpoint the adapter already speaks;
  zero code change to switch from the abstract protocol (verified against official docs, see Sources).
- Cost headroom against BRIEF.md's **≤ $0.03/item** ceiling for E1+E2+E3. One garment image is
  capped at **1024 input tokens** by the provider's own resize rule, plus ~700 tokens of prompt;
  a ~350-token JSON reply. At off-peak cache-miss rates that is roughly **$0.0005–0.001/item**,
  and a repair retry doubles it to ~$0.002 — far under the ceiling. **This is arithmetic from
  published rates, not a measurement.** Actual `cost_usd` stays `None` until a real call records
  token counts in `StageMeta.notes`; BRIEF.md forbids reporting unmeasured numbers.
- E7 (listing copy) was blocked on attributes sitting on the critical path; a cheap multimodal
  model unblocks the vertical slice without a GPU or a hosted-Modal dependency.

## Price and licence (RUNBOOK §6 requires both recorded)

Official DeepSeek Models & Pricing page, USD per 1M tokens, `deepseek-flash`:

| | off-peak | peak |
|---|---|---|
| input, cache miss | $0.15 | $0.30 |
| input, cache hit | $0.003 | $0.006 |
| output | $0.60 | $1.20 |

Off-peak = half of peak. Peak hours are **01:00–04:00 and 06:00–10:00 UTC, Mon–Fri, excluding
Chinese public holidays**; weekends and Chinese holidays are off-peak. DeepSeek states prices may
change and recommends re-checking the page. No accuracy figure is recorded here: E3-T3 accuracy
is **UNMEASURED** until E0-T1's 40 real photos / 20 hand masks exist. Vendor-published vision
benchmarks (MMMU-Pro 56.5, CVBench 77.9, DocVQA 95.6, RefCOCO 86.0) are the vendor's own claims,
not our measurement, and are deliberately not used as an acceptance criterion.

**Licence / terms:** this is a hosted API, not redistributed weights, so no model-weights licence
is added to `docs/DEPS.md`. Use is governed by the *DeepSeek Open Platform Terms of Service* and
the *DeepSeek Privacy Policy* (Sources). Nothing in this ADR is a legal conclusion.

## OPEN items — legal must resolve before seller photos are sent

`prompts/08-legal.md` §7 requires a vendor/DPA list with **region**, and §5 a data map plus
deletion flow. The following are recorded, not resolved. Jurisdiction-specific items are
**[COUNSEL]**:

1. **[COUNSEL][OPEN — owner: legal]** The DeepSeek Privacy Policy states personal data is
   "collect[ed], process[ed] and store[d] ... in the People's Republic of China", and the Open
   Platform Terms are governed by PRC law with disputes in Hangzhou. Seller photos are personal
   data. Cross-border transfer / GDPR / Canadian PIPEDA adequacy for Canadian and EU sellers is
   not assessed here. Add DeepSeek to the subprocessor table with region = PRC.
2. **[COUNSEL][OPEN — owner: legal]** A third-party DPA tracker (Oct 2026) reports "DeepSeek API:
   no DPA, data stored in China" and notes the API terms allow de-identified inputs/outputs to
   improve models unless opted out; the privacy policy also describes using input data to train
   and improve models. Confirm the current terms directly with DeepSeek and whether an API-level
   training opt-out / DPA exists. This is a third-party report, not verified by us.
3. **[OPEN — owner: legal]** Whether the privacy policy / DSAR deletion flow can cover an eval
   fixture derived from a seller image. `docs/METRICS.md` §4 rule 7 already keeps attributable
   research data out of the repo.
4. **[OPEN — owner: CV]** E3-T3 accuracy report. Blocked on E0-T1's golden set; until then all
   accuracy numbers are `UNMEASURED`.

**Constraint on the acceptance path:** until a real key is configured, `python -m
packages.vision.cli` writes an explicit `UNMEASURED` marker. BRIEF.md forbids mocked model calls
in the acceptance path; no stub may be substituted to make it look green.

## Consequences

- `docs/RUNBOOK.md §6` status is superseded; the blocking matrix entry for `VLM_API_KEY` now has
  a named provider.
- `docs/DEPS.md` gains a hosted-service row (no Python package is added; `httpx` 0.27.2 was
  already listed and is imported lazily).
- `.env.example` carries the exact config. `.env` itself stays gitignored (`gitignore:19`).
- Cost is measured, not estimated: `prompt_tokens` / `completion_tokens` are summed across the
  repair retry into `StageMeta.notes`.

## Sources (all fetched 2026-11-04)

- https://api-docs.deepseek.com/ — model id, base URL, first-call example
- https://api-docs.deepseek.com/quick_start/pricing — rate card, peak hours
- https://api-docs.deepseek.com/guides/vision — image formats, 48 MiB body limit, ≤1024 tokens/image
- https://api-docs.deepseek.com/guides/json_mode — `response_format`, "json" in prompt, blank-content behaviour
- https://api-docs.deepseek.com/guides/thinking_mode — default-on thinking, `thinking.type=disabled`
- https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html — PRC storage, retention
- https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html — governing law
