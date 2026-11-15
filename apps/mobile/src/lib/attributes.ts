/**
 * Applying a seller's human confirmation to an attribute set (E6-T3).
 *
 * The publish gate (`packages/shared/src/listing/copy.ts`) withholds any field whose
 * confidence sits below `ATTR_CONFIDENCE_FLOOR`. That is the right default for an
 * inference: a fibre guessed from a photo should not be printed as fact. But a seller
 * looking at the garment and confirming "cotton" is evidence of a completely different
 * kind, and before this module the app had no way to say so — the edit changed the value
 * and left the confidence untouched, so a confirmed field stayed below the floor and was
 * still withheld from the title, description, keywords and CSV. The seller confirmed it
 * and the listing still did not mention it.
 *
 * A confirmation therefore raises the field to `ATTR_CONFIRMED_CONFIDENCE` (1.0) and takes
 * it off `needs_review`. Both halves matter: the confidence is what unblocks the gate, and
 * `needs_review` is the list the UI renders as outstanding work.
 *
 * Pure: takes the attributes, returns a new object, never mutates the argument. The
 * caller owns the optimistic-update / rollback dance; this function only computes the
 * next state, which is also what makes it testable without a renderer.
 */

import { ATTR_CONFIRMED_CONFIDENCE } from "./confidence";
import { ItemAttributes } from "./types";

/**
 * The fields the Review screen lets a seller edit, and therefore the only ones a
 * confirmation can be about.
 *
 * Deliberately narrower than `keyof ItemAttributes`: `confidence` and `needs_review` are
 * bookkeeping this function OWNS, and `applyHumanConfirmation(attrs, "confidence", …)`
 * would overwrite the whole map with a single value. Excluding them makes that call
 * impossible to write rather than merely discouraged.
 */
export type ConfirmableAttribute =
  | "category"
  | "colors"
  | "pattern"
  | "material"
  | "fit"
  | "condition";

/**
 * Applies a human confirmation of `field = value`.
 *
 * @param attrs The current attributes. Not mutated.
 * @param field The attribute the seller confirmed.
 * @param value The value they confirmed it to be.
 * @returns A new attribute set with the value applied, that field's confidence raised to
 *   `ATTR_CONFIRMED_CONFIDENCE`, and the field removed from `needs_review`.
 */
export function applyHumanConfirmation(
  attrs: ItemAttributes,
  field: ConfirmableAttribute,
  value: string | string[],
): ItemAttributes {
  // Shallow copy, then replace the two bookkeeping entries with new objects: the argument's
  // `confidence` map and `needs_review` array must come out untouched.
  const next: ItemAttributes = { ...attrs };
  (next as unknown as Record<string, unknown>)[field] = value;
  next.confidence = {
    ...attrs.confidence,
    [field]: ATTR_CONFIRMED_CONFIDENCE,
  };
  next.needs_review = attrs.needs_review.filter((needs) => needs !== field);
  return next;
}
