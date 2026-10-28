You are the cost engineer. Goal: halve $/item without losing more than 1 point of quality.

1. Read evals/reports/cost.md and infra/prices.json. Reproduce the current $/item with `make cost`; paste it.
2. Profile where the money goes per item (segmentation, matte, inpaint, attributes VLM, storage, egress).
   Percentages must be measured, not estimated.
3. Propose the 3 cheapest wins, in order, with expected delta and risk. Typical candidates:
   downscale before segmentation; cheap matte for proxies and expensive only for finals; batch attribute calls;
   cache identical garment embeddings; skip inpaint when a wrinkle score is below threshold; quantise or swap
   the VLM tier for low-confidence-then-escalate.
4. Implement wins 1-2 behind a flag, measure quality delta on the full golden set, and paste before/after tables.
5. Update evals/reports/cost.md with the measured result and the arithmetic.

FORBIDDEN: claiming savings without a measured run; changing a quality gate to make the savings look free.
