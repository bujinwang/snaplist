"""E3-T2 provider-independent machinery: schema enforcement, one repair retry, evidence gating.

Nothing in this file knows which VLM produced the text. It takes raw response text and returns
a validated `ItemAttributes`, so it can be tested against on-disk fixtures with zero network
and zero credentials — while the acceptance path itself always uses a real provider call.

Why the rules are here and not in the prompt
-------------------------------------------
A prompt asking "please don't invent a brand" is a wish; `ItemAttributes(extra="forbid")` plus
the evidence gate below is a guarantee. The VLM returns an envelope:

    {"attributes": {...ItemAttributes...}, "evidence": {"brand": "...", "material": ["..."]}}

`evidence` is a *claim*, not proof, but requiring it means brand/material can never be emitted
as a silent hallucination: without the key the value is dropped and the field is marked for
review. Confidence defaults to 0.0 for every field the VLM did not score — UNMEASURED, never a
flattering guess — and sub-threshold fields land in `needs_review`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from apps.api.schemas import ItemAttributes
from packages.vision.attrs import AttributeExtractionFailed

CONFIDENCE_FLOOR = 0.60
_TRACKED_FIELDS = (
    "category",
    "subcategory",
    "colors",
    "pattern",
    "material",
    "fit",
    "era",
    "brand",
    "size_label",
    "condition",
    "defects",
    "measurements",
    "keywords",
)

SYSTEM_PROMPT = """You extract resale-listing attributes from ONE garment photo.

Return ONLY a JSON object with exactly two keys and no markdown fence:
  "attributes": an object matching the ItemAttributes schema
  "evidence": an object with optional string keys "brand" and "material"

Hard rules:
- "brand": set attributes.brand ONLY when a logo/label is legible in the photo. When you do,
  put the legible text you read into evidence.brand. Otherwise use null and omit the key.
- "material": list fibres ONLY with visual or care-label evidence. Put what you saw into
  evidence.material. Otherwise use an empty list.
- Never guess measurements, era, size_label or condition from nothing; omit them.
- Give a "confidence" object mapping each attribute field name to a number in 0.0..1.0.
  Fields you did not assess must be scored 0.0, not a hopeful middle value.
- "colors": 1-3 lowercase colour names, primary first.
- "keywords": at most 10 short lowercase strings.

ItemAttributes fields: category (top|bottom|dress|outerwear|shoe|accessory|other),
subcategory, colors, pattern, material, fit, era, brand, size_label, condition, defects,
measurements, keywords, confidence, needs_review, model_version.
No extra keys are allowed anywhere.
"""

REPAIR_PROMPT = """Your previous response failed validation. Return ONLY the corrected JSON
object (same two keys: "attributes" and "evidence"), with no prose and no markdown fence.

Validation error:
{error}

Original response (for reference):
{original}
"""


class Evidence(BaseModel):
    """What the VLM says it actually saw. A claim, used only to gate brand/material."""

    model_config = ConfigDict(extra="forbid")

    brand: str | None = None
    material: str | None = None


class VlmEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attributes: ItemAttributes
    evidence: Evidence = Evidence()


@dataclass(slots=True)
class ExtractionOutcome:
    attributes: ItemAttributes
    attempts: int
    repaired: bool
    dropped_fields: tuple[str, ...]


def build_prompt(repair: tuple[str, str] | None = None) -> str:
    """System+user instruction text. `repair` = (validation error, original response)."""
    if repair is None:
        return SYSTEM_PROMPT
    return REPAIR_PROMPT.format(error=repair[0], original=repair[1][:2000])


def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Accept either the envelope or a bare attributes object. Strips markdown fences."""
    if set(raw) <= {"attributes", "evidence"} and "attributes" in raw:
        return raw
    return {"attributes": raw, "evidence": {}}


def _parse(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.lstrip().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
        cleaned = cleaned.rsplit("```", 1)[0]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("VLM response must be a JSON object")
    return _normalise(parsed)


def _apply_evidence_and_confidence(
    env: VlmEnvelope, model_version: str
) -> tuple[ItemAttributes, tuple[str, ...]]:
    """Drop unevidenced brand/material, fill confidence/needs_review, stamp model_version."""
    attrs = env.attributes
    dropped: list[str] = []
    brand = attrs.brand
    material = list(attrs.material)
    if brand and not (env.evidence.brand or "").strip():
        brand = None
        dropped.append("brand")
    if material and not (env.evidence.material or "").strip():
        material = []
        dropped.append("material")

    confidence: dict[str, float] = {}
    for field in _TRACKED_FIELDS:
        score = attrs.confidence.get(field, 0.0)
        confidence[field] = round(max(0.0, min(1.0, float(score))), 4)
    for field in dropped:
        confidence[field] = 0.0

    review = set(attrs.needs_review)
    for field, score in confidence.items():
        if score < CONFIDENCE_FLOOR:
            review.add(field)

    return (
        attrs.model_copy(
            update={
                "brand": brand,
                "material": material,
                "confidence": confidence,
                "needs_review": sorted(review),
                "model_version": model_version,
            }
        ),
        tuple(dropped),
    )


def extract_from_response(
    complete: Callable[[str], str],
    *,
    model_version: str,
    image_prompt_hint: str = "",
) -> ExtractionOutcome:
    """Run `complete(prompt) -> raw text` with exactly one repair retry.

    `complete` is the only seam to the provider. In production it makes a real HTTP call; in
    unit tests it replays an on-disk fixture. Schema errors (including `extra="forbid"`
    violations from ItemAttributes) trigger ONE repair attempt, then fail as
    `low_confidence`. Two attempts total, never more — the retry budget is a contract.
    """
    last_error = ""
    original = ""
    for attempt in (1, 2):
        prompt = build_prompt(None if attempt == 1 else (last_error, original))
        if image_prompt_hint:
            prompt = f"{prompt}\n\nPhoto context: {image_prompt_hint}"
        raw_text = complete(prompt)
        if not original:
            original = raw_text
        try:
            envelope = VlmEnvelope.model_validate(_parse(raw_text))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        attrs, dropped = _apply_evidence_and_confidence(envelope, model_version)
        return ExtractionOutcome(attrs, attempt, attempt == 2, dropped)

    raise AttributeExtractionFailed(
        f"VLM output failed ItemAttributes validation after one repair retry: {last_error}",
        attempts=2,
    )
