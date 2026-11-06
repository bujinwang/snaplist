/**
 * Seller-facing failure copy for every `reason_code` (docs/CONTRACTS.md §6).
 *
 * A `reason_code` is a contract/log value — it must NEVER be shown to the seller.
 * This table is the single source for the human strings so two screens cannot
 * describe the same failure differently. The mobile app only ever renders the
 * `title` / `body` / `action` below, never the enum.
 */

import { ReasonCode } from "./types";

export interface FailureCopy {
  title: string;
  body: string;
  action: string;
}

export const FAILURE_COPY: Record<ReasonCode, FailureCopy> = {
  bad_photo: {
    title: "This photo is hard to use",
    body: "Too dark, blurry, or the garment is cut off at the edge.",
    action: "Retake",
  },
  no_garment: {
    title: "No garment found",
    body: "We could not find a piece of clothing in this photo. Lay it flat and fill the frame.",
    action: "Retake",
  },
  low_confidence: {
    title: "We are not sure about this item",
    body: "Some details did not come through clearly. Check the labels before you export.",
    action: "Review attributes",
  },
  vton_rejected: {
    title: "This one cannot go on a model",
    body: "The garment or the chosen model did not work together. Your credit was not used.",
    action: "Pick another model",
  },
  internal: {
    title: "Something broke on our side",
    body: "This is our fault, not yours. Your credit was not used and we have been notified.",
    action: "Try again",
  },
};

/**
 * Resolve a `reason_code` to seller copy. Unknown codes degrade to the `internal`
 * copy (never blame the seller, never show the enum).
 */
export function failureCopy(code: ReasonCode | undefined): FailureCopy {
  if (code && FAILURE_COPY[code]) return FAILURE_COPY[code];
  return FAILURE_COPY.internal;
}
