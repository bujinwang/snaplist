/**
 * The publish gate for attribute values that could be guesses (E3-T3).
 *
 * `material` may now be inferred from a category/structure prior (Bert ruling 2026-11-14), which
 * means an attribute can exist without anybody having observed it. The inference is recorded in
 * `attributes.material` with its confidence pinned to `CONFIDENCE_FLOOR - 0.05` (0.55) by
 * `packages/vision/attrs_adapters/base.py`. That number does two jobs: it puts the field in
 * `needs_review`, and it sits below this floor so the listing generator will not print it.
 *
 * So this constant is the only thing standing between an inference and a published fact. It must
 * stay equal to `CONFIDENCE_FLOOR` in `packages/vision/attrs_adapters/base.py`, and to the copy in
 * `apps/mobile/src/lib/csv.ts` (which duplicates it deliberately — the mobile app builds without
 * `@snaplist/shared`). `packages/vision/tests/test_attrs.py::test_the_publish_gate_matches_the_engine_floor`
 * asserts all three agree; if they drift, the gate opens silently and a guess becomes a listing.
 */
export const ATTR_CONFIDENCE_FLOOR = 0.6;
