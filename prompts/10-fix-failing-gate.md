You are the expert who owns the failing gate. Do not weaken the gate.

1. Run `make evals` and `make test`. Paste the full failure output.
2. Name the exact metric that missed, its threshold (evals/gates.json), and the actual value.
3. Inspect the failing fixtures individually and classify the cause:
   (a) fixture is mislabelled, (b) pipeline bug, (c) genuine model limitation, (d) threshold unrealistic.
4. Fix (a) and (b) in code. For (c), write it in docs/LIMITS.md with the failing examples and the affected
   category share — do not hide it by editing the fixture. For (d), you may propose a threshold change ONLY with
   a written justification and the old/new number in docs/HANDOFF-<role>.md; never change a gate silently.
5. Re-run and paste the new numbers beside the old ones.

FORBIDDEN: deleting fixtures, catching exceptions to make tests pass, skipping tests, rounding metrics,
changing gates.json without justification, reporting a number you did not print.
