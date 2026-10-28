# SnapList — expert-group prompt pack

Ten role prompts that turn the WorkBuddy CLI into a small software company, plus two operating-loop prompts.
Every prompt assumes the shared brief in `BRIEF.md`; they are written to be self-contained for an agent that has
never seen this conversation.

## Prerequisite (one time, interactive)
```bash
coder wb "hi"        # then type: /login        # or: export CODEBUDDY_API_KEY=...
coder wb "Reply with exactly: READY"
```
`run-group.sh` probes this and aborts with exit 2 rather than burning ten runs on an auth error.

## Three ways to run it

**1. One role, one session (most control, best for debugging):**
```bash
cd ~/Documents/Projects/SnapList
coder -y wb "$(cat prompts/00-lead.md)"
```

**2. Whole company, sequential, stop on first failure (recommended for the first build):**
```bash
cd ~/Documents/Projects/SnapList && ./run-group.sh
./run-group.sh vision backend          # or a subset, in the order you give
AGENT=whale ./run-group.sh             # fall back to another installed agent
```
Logs land in `logs/`, and the run stops if a role writes `BLOCKED.md`.

**3. Parallel teammates in one process (after the lead's plan exists, for independent epics):**
```bash
cd ~/Documents/Projects/SnapList
coder -y wb --swarm --agents "$(cat agents.json)" \
  "Act as lead. Read BRIEF.md and docs/PLAN.md. Dispatch vision, backend, growth and legal in parallel.
   Do not let two teammates touch the same directory. Merge, then run the integrator pass yourself."
```
Use swarm only for epics whose file scopes do not overlap — the vision, backend, growth and legal scopes are
disjoint by design; vision and vton are not (both edit `packages/vision/`), so run those two sequentially.

## Roles and the epics they own

| # | Prompt | Role | Owns | Blocks on |
|---|---|---|---|---|
| 00 | `prompts/00-lead.md` | Technical lead | `docs/PLAN.md`, `docs/CONTRACTS.md`, repo scaffold | — |
| 01 | `prompts/01-product.md` | Product | PRD, metrics, onboarding, interviews | — |
| 02 | `prompts/02-vision.md` | CV | `packages/vision/`, `evals/` | 00 |
| 03 | `prompts/03-vton.md` | VTON | `packages/vision/vton.py` | 02 (shares dir — run after) |
| 04 | `prompts/04-backend.md` | Backend | `apps/api/`, migrations | 00 contracts |
| 05 | `prompts/05-mobile.md` | Mobile | `apps/mobile/` | 04 endpoints |
| 06 | `prompts/06-growth.md` | Growth | `packages/shared/src/marketplaces`, `apps/web` | 02 attributes |
| 07 | `prompts/07-qa.md` | QA/DevOps | CI, gates, cost dashboard | 00–06 |
| 08 | `prompts/08-legal.md` | Legal/trust | `docs/LEGAL.md`, templates | — (can run early) |
| 09 | `prompts/09-integrator.md` | Integration | `STATUS.md`, `DEFECTS.md`, `DEMO.md` | all |

Recommended order: **lead → product → vision → backend → vton → growth → mobile → legal → qa → integrator.**

## The seven rules every prompt carries
1. Run the commands you claim to have run; paste real output into `docs/HANDOFF-<role>.md`.
2. Never fabricate metrics, screenshots, test output or file contents. Unmeasured ⇒ `UNMEASURED`.
3. Blocked ⇒ `BLOCKED.md` with the verbatim error, then stop. No improvising around missing credentials.
4. Stay inside your scope. Cross-scope needs go in the handoff file as a request.
5. New dependency ⇒ row in `docs/DEPS.md` (name, version, licence, reason).
6. Boring solution. No new framework, no rewrite, no speculative abstraction.
7. End with a 10-line summary: what works, what is stubbed, next command, open questions.

## Operating loops (run these, not the build prompts, once v1 exists)
```bash
coder -y wb "$(cat prompts/10-fix-failing-gate.md)"   # a gate went red
coder -y wb "$(cat prompts/11-cost-cut.md)"           # $/item is too high
```

## What to check yourself before believing a run
```bash
cd ~/Documents/Projects/SnapList
ls docs/ prompts/ evals/reports/            # artifacts actually exist
cat docs/STATUS.md                          # every epic named with a proving file
git diff --stat                             # scope discipline: nothing outside the role
make test evals && cat evals/reports/cost.md # the numbers, not the summary
```
Agents report success they did not achieve. The integrator prompt is written to resist that, but the last five
minutes of checking are yours.
