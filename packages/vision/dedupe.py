"""STAGE 5 — near-duplicate detection. Owner: CV role (E3, ticket E3-T4).

Not implemented. Stops a seller re-uploading the same black tee from creating item #4.

    find_duplicate(user_id: str, image: bytes, candidates: Sequence[str]) -> DedupeResult

`DedupeResult.match_item_id` is None when nothing is close enough. The false-merge rate is
measured on a 20-pair fixture and reported in evals/reports/. A false merge destroys a
seller's listing, so the threshold is deliberately conservative.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from packages.vision import StageNotImplemented


@dataclass(slots=True)
class DedupeResult:
    match_item_id: str | None = None
    similarity: float | None = None  # None => UNMEASURED, not 0.0


def find_duplicate(
    user_id: str, image: bytes, candidates: Sequence[str] = ()
) -> DedupeResult:
    raise StageNotImplemented(
        "packages.vision.dedupe.find_duplicate — see docs/PLAN.md ticket E3-T4"
    )
