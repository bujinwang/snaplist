/**
 * Comps-based price suggestion (E7-T4), offline and credential-free.
 *
 * The rule this module exists to enforce is a refusal, not a number:
 *
 * > Fewer than 5 comparable sold listings ⇒ `insufficient_data`. Never a guess.
 *
 * A price with four comps behind it is a coin flip, and a coin flip that *looks* like advice is
 * worse than no advice — the seller cannot tell the difference between "we know" and "we
 * assumed". So the return type is a discriminated union and the happy path cannot be reached
 * without `MIN_COMPS` observations.
 *
 * No network call lives here on purpose. `E7-T4`'s eBay Browse API adapter needs an OAuth token
 * we do not have; the median maths, the threshold and the shape are the parts that *can* be
 * proven today, so they are the parts that are implemented and tested. When the adapter lands it
 * returns `Comp[]` and calls `suggestPrice` — it does not re-implement the rule.
 */

/** Comps needed before a median is worth showing. Frozen: the paywall copy quotes it. */
export const MIN_COMPS = 5;

export interface Comp {
  /** Sold price in cents. Positive integers only; a 0 is a data error, not a free item. */
  price_cents: number;
  /** ISO 8601 date the comp sold, when the source provides one. */
  sold_at?: string;
  /** Source URL, for the seller to check. Never rendered as advice on its own. */
  url?: string;
}

export interface PriceSuggestion {
  status: "ok";
  median_cents: number;
  comp_count: number;
  currency: "USD";
  min_cents: number;
  max_cents: number;
}

export interface InsufficientData {
  status: "insufficient_data";
  comp_count: number;
  required: number;
}

export type PricingResult = PriceSuggestion | InsufficientData;

function usable(comp: Comp): boolean {
  return Number.isFinite(comp.price_cents) && comp.price_cents > 0;
}

/** Median of an odd-length list is the middle; even-length averages the two middles. */
export function medianCents(values: readonly number[]): number {
  if (values.length === 0) throw new Error("medianCents requires at least one value");
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[middle] as number;
  const lower = sorted[middle - 1] as number;
  const upper = sorted[middle] as number;
  return Math.round((lower + upper) / 2);
}

/**
 * Suggest a price from sold comps, or refuse.
 *
 * `currency` is fixed to USD because every marketplace mapper and every comp fixture is USD in
 * v1; a multi-currency comp set would need an FX rate and an FX rate at a point in time is
 * exactly the kind of number this file refuses to invent.
 */
export function suggestPrice(comps: readonly Comp[]): PricingResult {
  const prices = comps.filter(usable).map((comp) => comp.price_cents);
  if (prices.length < MIN_COMPS) {
    return { status: "insufficient_data", comp_count: prices.length, required: MIN_COMPS };
  }
  return {
    status: "ok",
    median_cents: medianCents(prices),
    comp_count: prices.length,
    currency: "USD",
    min_cents: Math.min(...prices),
    max_cents: Math.max(...prices),
  };
}
