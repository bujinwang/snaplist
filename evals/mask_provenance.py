"""ADR-010 — the single source of truth for `mask_source` provenance classification.

    from evals.mask_provenance import classify, is_hand_made, is_machine_reference

Four consumers used to each carry their own `source.strip().lower().startswith("hand")`
(`evals/run.py`, `evals/tools/seg_iou.py`, `evals/tools/validate_golden.py`,
`infra/ci/gates/regression_gate.py`). A vocabulary change in one of them would have let the
harness row, the segmentation report, the validator tally and the baseline guard disagree
about the *same* mask. They all import from here now, and `evals/tests/test_mask_provenance.py`
pins that with source-level guards.

Three tiers, not two (ADR-010 / Bert ruling 3):

=========================  =======================================  =====================
tier                       example `mask_source`                    meaning
=========================  =======================================  =====================
HAND                       `hand`, `hand-drawn`, `hand:photoshop`   drawn by a human
AI_ASSISTED_REVIEWED       `ai-assisted:sam2+point-reviewed`       AI proposal, human
                                                                    point review recorded
MACHINE                    `algorithmic:matting`, `ai:nano-banana`,  no human review
                           `provisional`, `unspecified`
=========================  =======================================  =====================

`hand` keeps its old meaning: any string starting with `hand` (case-insensitive). It is
**forbidden** to label an AI-assisted mask with a `hand` prefix. The new tier has its own
prefix (`ai-assisted:`) and a mandatory `+point-reviewed` suffix; a bare `ai-assisted:sam2`
is deliberately MACHINE, because a proposal without a recorded review is not a review.

The model slug is restricted to `[a-z0-9_-]+`. `regression_gate.py` derives a measurement's
sources with `_MASK_SOURCE_RE = r"mask source:\\s*([^.;]+)"` and splits the captured text on
`,`: a `.` or `;` truncates the capture, a `,` invents an extra source, and `:` is reserved by
the prefix grammar. A full version string such as `sam2.1` in the prefix would corrupt
`mask_sources()`'s coverage check. Full version strings belong in the register's
`proposal.model_version`, never in the prefix.

**Sceptical default (unchanged from the old inline code):** a missing, empty, whitespace-only
or unknown value is MACHINE with `is_default=True`. A set that stays quiet about its
provenance must not be able to certify itself. `row.get("mask_source", "unspecified")` keeps
its existing shape at the call sites.

Pure stdlib, no I/O, no credentials, no network. This module classifies strings; it does not
read the point-review register and it does not decide whether evidence exists. That second
condition lives in `evals/tools/validate_point_reviews.py` — ADR-010's two-condition check is
deliberately split across the two layers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

__all__ = [
    "AI_ASSISTED_PREFIX",
    "AI_ASSISTED_RE",
    "HAND_PREFIX",
    "HAND_PREFIX_RE",
    "MACHINE_REFERENCE_MARKERS",
    "POINT_REVIEWED_SUFFIX",
    "TIER_EXAMPLES",
    "UNSPECIFIED",
    "Classification",
    "MaskSourceTier",
    "ReviewState",
    "classify",
    "is_hand_made",
    "is_machine_reference",
    "normalise",
    "parse_model_slug",
    "tier_label",
]


class MaskSourceTier(str, Enum):  # noqa: UP042 - the contract names the (str, Enum) form
    """The three provenance tiers a report must keep distinct."""

    HAND = "hand"
    AI_ASSISTED_REVIEWED = "ai_assisted_reviewed"
    MACHINE = "machine"


HAND_PREFIX: Final[str] = "hand"
AI_ASSISTED_PREFIX: Final[str] = "ai-assisted:"
POINT_REVIEWED_SUFFIX: Final[str] = "+point-reviewed"
UNSPECIFIED: Final[str] = "unspecified"

HAND_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^hand", re.IGNORECASE)
AI_ASSISTED_RE: Final[re.Pattern[str]] = re.compile(
    r"^ai-assisted:(?P<model>[a-z0-9_-]+)\+point-reviewed$", re.IGNORECASE
)

#: Substrings in a Measurement note / set name that mark references as not human-made.
#: `ai:` and `ai-assisted:` are BOTH required: the frozen manifest still carries old `ai:*`
#: values, and `ai-assisted:sam2+point-reviewed` does NOT contain `ai:` (the character after
#: `ai` is `-`). Dropping either marker would let that value slip past the baseline guard.
MACHINE_REFERENCE_MARKERS: Final[tuple[str, ...]] = (
    "not hand-drawn",
    "algorithmic",
    "ai:",
    "ai-assisted:",
    "provisional",
)

#: Example values for every tier. `evals/tests/test_mask_provenance.py` walks this dict in
#: both directions (tier -> examples -> tier), and the regression-gate test uses it to prove
#: the marker vocabulary and the classifier cannot drift apart.
TIER_EXAMPLES: Final[dict[MaskSourceTier, tuple[str, ...]]] = {
    MaskSourceTier.HAND: ("hand", "hand-drawn", "hand:photoshop"),
    MaskSourceTier.AI_ASSISTED_REVIEWED: (
        "ai-assisted:sam2+point-reviewed",
        "ai-assisted:nano-banana+point-reviewed",
    ),
    MaskSourceTier.MACHINE: (
        "algorithmic:matting",
        "ai:google-ai-mode-nano-banana",
        "ai-assisted:sam2",  # proposal without a recorded review is MACHINE
        "provisional",
        UNSPECIFIED,
        "unknown",
    ),
}


@dataclass(frozen=True, slots=True)
class Classification:
    """The outcome of classifying one `mask_source` string.

    Attributes:
        raw: the normalised string (`UNSPECIFIED` when the input was missing/blank).
        tier: one of the three ADR-010 tiers.
        model_slug: the `[a-z0-9_-]+` model slug for AI_ASSISTED_REVIEWED, else `None`.
        is_default: True when the input was missing, blank, or an explicit unknown value and
            the sceptical MACHINE default therefore applied. An explicit machine value such as
            `algorithmic:matting` is MACHINE but NOT a default.
    """

    raw: str
    tier: MaskSourceTier
    model_slug: str | None
    is_default: bool


@dataclass(frozen=True, slots=True)
class ReviewState:
    """An immutable summary of the point-review register, for report renderers.

    Reports receive this and nothing else: `seg_iou.render()` must not read the register or
    judge evidence. `u1_met` is computed by `validate_point_reviews`, never here.
    """

    reviewed: int
    slots: int
    u1_met: bool
    threshold_status: str


def normalise(value: str | None) -> str:
    """Strip a provenance value; missing/empty/whitespace becomes `UNSPECIFIED`.

    Case is preserved on purpose: both regexes are IGNORECASE and the raw string is what the
    report prints, so lower-casing here would change the artifact without changing a decision.
    """
    if value is None:
        return UNSPECIFIED
    stripped = str(value).strip()
    return stripped if stripped else UNSPECIFIED


def parse_model_slug(value: str | None) -> str | None:
    """The model slug of a fully qualified AI-assisted point-reviewed value, else `None`."""
    match = AI_ASSISTED_RE.fullmatch(normalise(value))
    if match is None:
        return None
    return match.group("model").lower()


def classify(value: str | None) -> Classification:
    """Classify one `mask_source` value into exactly one of the three tiers.

    Order matters: the strict AI-assisted pattern is tested first (it is the only pattern that
    carries a model slug), then the historical `hand` prefix, then everything else is MACHINE.
    """
    raw = normalise(value)
    is_default = raw.lower() in {UNSPECIFIED, "unknown"}
    match = AI_ASSISTED_RE.fullmatch(raw)
    if match is not None and not is_default:
        return Classification(
            raw=raw,
            tier=MaskSourceTier.AI_ASSISTED_REVIEWED,
            model_slug=match.group("model").lower(),
            is_default=False,
        )
    if not is_default and HAND_PREFIX_RE.match(raw):
        return Classification(
            raw=raw,
            tier=MaskSourceTier.HAND,
            model_slug=None,
            is_default=False,
        )
    return Classification(
        raw=raw,
        tier=MaskSourceTier.MACHINE,
        model_slug=None,
        is_default=is_default,
    )


def is_hand_made(value: str | None) -> bool:
    """Whether the value claims a human drew the mask. AI-assisted is NOT hand."""
    return classify(value).tier is MaskSourceTier.HAND


def is_machine_reference(provenance: str, set_name: str = "") -> bool:
    """Whether a measurement note/set name says its references are not human-made.

    Substring matching over the lower-cased haystack, exactly as the gate's original
    `has_machine_references()` did; the semantics are unchanged, only the vocabulary moved
    here. Note that the AI-assisted tier also matches (`ai-assisted:` marker), which is what
    keeps `--update-baseline` refusing a run whose register has not been signed off.
    """
    haystack = f"{provenance} {set_name}".lower()
    return any(marker in haystack for marker in MACHINE_REFERENCE_MARKERS)


def tier_label(tier: MaskSourceTier) -> str:
    """Human-readable tier name used by the three-tier report lines."""
    return {
        MaskSourceTier.HAND: "hand-drawn",
        MaskSourceTier.AI_ASSISTED_REVIEWED: "AI-assisted point-reviewed",
        MaskSourceTier.MACHINE: "machine-made",
    }[tier]
