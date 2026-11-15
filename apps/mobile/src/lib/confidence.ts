/**
 * Confidence constants shared by the publish gate and the human-confirmation path (E6-T3).
 *
 * `ATTR_CONFIDENCE_FLOOR` is the listing gate: a field at or above it may appear in the
 * title / description / keywords / CSV, below it the field is withheld. The number is
 * MIRRORED here rather than imported because this app builds without `@snaplist/shared`;
 * it must equal `ATTR_CONFIDENCE_FLOOR` in `packages/shared/src/listing/confidence.ts` and
 * `CONFIDENCE_FLOOR` in `packages/vision/attrs_adapters/base.py`, and
 * `packages/vision/tests/test_attrs.py::test_the_publish_gate_matches_the_engine_floor`
 * asserts all three agree. If they drift, an inferred fibre — confidence 0.55 — would ship
 * in the listing as if it had been observed.
 *
 * `ATTR_CONFIRMED_CONFIDENCE` is what a seller's confirmation is worth. A confirmation is
 * the strongest evidence this system can have: a person looked at the garment and said so.
 * It is therefore 1.0, not a nudge above the floor — anything lower would make "confirmed"
 * and "barely publishable" the same number, and the two need to be distinguishable when
 * something later has to decide whether a value was observed or merely inferred.
 *
 * The invariant `ATTR_CONFIRMED_CONFIDENCE > ATTR_CONFIDENCE_FLOOR` is asserted alongside
 * the three-way mirror test: a confirmation that did not clear the gate would leave the
 * field withheld, i.e. confirming would change nothing on the listing.
 */

/** Listing gate: at or above this, a field may be published. Mirrors `@snaplist/shared`. */
export const ATTR_CONFIDENCE_FLOOR = 0.6;

/** What a seller's own confirmation is worth — the strongest evidence available. */
export const ATTR_CONFIRMED_CONFIDENCE = 1.0;
