"""ADR-010 — `evals/mask_provenance.py` unit tests, four-consumer source guards, D3 regression.

What is pinned here:

* the three tiers and the sceptical default (`TIER_EXAMPLES` walked in both directions);
* the strict `ai-assisted:<slug>+point-reviewed` full match, including the forbidden `.`/`,`
  characters that would corrupt `regression_gate.mask_sources()`;
* the marker vocabulary (`ai:` AND `ai-assisted:`) vs the classifier, so the two cannot drift;
* **D3**: `ai-assisted:sam2+point-reviewed` must still trip `has_machine_references` and be
  refused by `--update-baseline`, or the relaxed tier would silently become a baseline;
* the four consumers all import the shared module and no longer carry an inline
  `startswith("hand")` or a local markers tuple.

`tmp_path` is deliberately NOT used: on this host the sandbox raises EEXIST for pytest's temp
root. `tempfile.TemporaryDirectory()` is used instead, as the repo's own harness tests do.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

from evals import mask_provenance as mp
from evals.mask_provenance import MaskSourceTier, classify, is_hand_made, is_machine_reference

REPO_ROOT = Path(__file__).resolve().parents[2]
GATES = REPO_ROOT / "infra" / "ci" / "gates"
if str(REPO_ROOT) not in sys.path:
    # regression_gate.py is loaded by path from any cwd; its top-level import of
    # evals.mask_provenance needs the repo root available before exec_module().
    sys.path.insert(0, str(REPO_ROOT))

FOUR_CONSUMERS = (
    "evals/run.py",
    "evals/tools/seg_iou.py",
    "evals/tools/validate_golden.py",
    "infra/ci/gates/regression_gate.py",
)
#: Deliberately excluded from the source guard: a provisional-set literal, a fixture builder,
#: test fixtures, README examples and manifest values are not provenance decision points.
LITERAL_WHITELIST = (
    "evals/tools/fetch_web_images.py",
    "evals/fixtures/prettify/build_fixtures.py",
    "evals/tests/",
    "infra/ci/gates/tests/",
    "evals/golden-review/README.md",
    "evals/golden/manifest.json",
)


def _load_regression_gate():
    """Load the gate by path under a unique name to avoid clashing with its own test suite."""
    name = "regression_gate_for_mask_provenance"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, GATES / "regression_gate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rg = _load_regression_gate()


# --------------------------------------------------------------------------- classifier


def test_tier_examples_are_bidirectional() -> None:
    """tier -> examples -> tier, and every declared example lands back in its own tier."""
    for tier, examples in mp.TIER_EXAMPLES.items():
        assert examples, tier
        for value in examples:
            got = classify(value)
            assert got.tier is tier, (value, got)
            assert got.raw == mp.normalise(value)
    reverse: dict[str, MaskSourceTier] = {}
    for tier, examples in mp.TIER_EXAMPLES.items():
        for value in examples:
            assert value not in reverse, f"{value!r} declared in two tiers"
            reverse[value] = tier
    for value, tier in reverse.items():
        assert classify(value).tier is tier


def test_sceptical_default_is_machine() -> None:
    for value in (None, "", "   ", "unspecified", "UNSPECIFIED", "unknown"):
        got = classify(value)
        assert got.tier is MaskSourceTier.MACHINE, (value, got)
        assert got.is_default is True
        assert is_hand_made(value) is False
    assert mp.normalise(None) == mp.UNSPECIFIED
    assert mp.normalise("  hand  ") == "hand"


def test_hand_prefix_keeps_its_original_meaning() -> None:
    for value in ("hand", "Hand", "HAND-DRAWN", "hand:photoshop"):
        got = classify(value)
        assert got.tier is MaskSourceTier.HAND, value
        assert got.model_slug is None
        assert got.is_default is False
        assert is_hand_made(value) is True
        assert is_machine_reference(value) is False


def test_ai_assisted_requires_a_full_match() -> None:
    good = "ai-assisted:sam2+point-reviewed"
    got = classify(good)
    assert got.tier is MaskSourceTier.AI_ASSISTED_REVIEWED
    assert got.model_slug == "sam2"
    assert got.is_default is False
    assert is_hand_made(good) is False
    assert mp.parse_model_slug("AI-ASSISTED:Nano_Banana+POINT-REVIEWED") == "nano_banana"

    for bad in (
        "ai-assisted:sam2",  # proposal, no recorded review
        "ai-assisted:sam2+point-reviewed+extra",
        "ai-assisted:sam2.1+point-reviewed",  # '.' truncates _MASK_SOURCE_RE
        "ai-assisted:sam2,foo+point-reviewed",  # ',' splits mask_sources()
        "ai:sam2+point-reviewed",
    ):
        assert classify(bad).tier is MaskSourceTier.MACHINE, bad
        assert mp.parse_model_slug(bad) is None
    # A `hand` prefix still wins by the historical rule — which is exactly why ADR-010 makes
    # "AI proposal + hand prefix" a hard CONFLICT in the register validator.
    assert classify("hand:ai-assisted:sam2+point-reviewed").tier is MaskSourceTier.HAND


def test_explicit_machine_values_are_not_defaults() -> None:
    for value in ("algorithmic:matting", "ai:google-ai-mode-nano-banana", "provisional"):
        got = classify(value)
        assert got.tier is MaskSourceTier.MACHINE, value
        assert got.is_default is False
        assert is_machine_reference(value) is True
    # Positive assertion for the whitelisted provisional literal: an algorithmic mask is
    # machine-made even though the string is not in this module's example table.
    assert classify("algorithmic:background_colour_distance").tier is MaskSourceTier.MACHINE
    assert classify("algorithmic:background_colour_distance").is_default is False


def test_tier_labels_are_distinct() -> None:
    labels = {tier: mp.tier_label(tier) for tier in MaskSourceTier}
    assert len(set(labels.values())) == 3
    assert all(labels.values())


# --------------------------------------------------- vocabulary <-> markers consistency


def test_marker_vocabulary_and_tier_examples_agree() -> None:
    assert "ai:" in mp.MACHINE_REFERENCE_MARKERS
    assert "ai-assisted:" in mp.MACHINE_REFERENCE_MARKERS
    assert "not hand-drawn" in mp.MACHINE_REFERENCE_MARKERS
    assert "algorithmic" in mp.MACHINE_REFERENCE_MARKERS
    assert "provisional" in mp.MACHINE_REFERENCE_MARKERS
    flat = [value for examples in mp.TIER_EXAMPLES.values() for value in examples]
    for marker in ("algorithmic", "ai:", "ai-assisted:", "provisional"):
        assert any(marker in value for value in flat), f"no example exercises {marker!r}"
    for value in mp.TIER_EXAMPLES[MaskSourceTier.HAND]:
        assert is_machine_reference(value) is False, value
    for value in mp.TIER_EXAMPLES[MaskSourceTier.AI_ASSISTED_REVIEWED]:
        assert is_machine_reference(value) is True, value


def test_regression_gate_uses_the_identical_markers_object() -> None:
    assert rg.MACHINE_REFERENCE_MARKERS is mp.MACHINE_REFERENCE_MARKERS
    assert rg.has_machine_references is not None


def test_d3_ai_assisted_value_is_not_silently_baselined() -> None:
    """D3: `ai:` does not match `ai-assisted:`, so the new marker is load-bearing."""
    value = "ai-assisted:sam2+point-reviewed"
    assert "ai:" not in value.lower()
    assert is_machine_reference(value) is True
    entry = rg.Entry(
        value=0.93,
        n=20,
        set_name="golden",
        provenance=f"mask source: {value}; median 0.98",
    )
    assert rg.has_machine_references(entry) is True
    assert rg.mask_sources(entry) == [value]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "baseline.json"
        code, messages = rg.update_baseline(
            path,
            {"segmentation mean IoU": entry},
            git_sha="test-sha",
            now="2026-11-08T00:00:00+00:00",
            default_tolerance=0.01,
        )
        assert code == rg.EXIT_INCONCLUSIVE
        assert any("refusing to write a baseline" in message for message in messages)
        assert not path.exists()


def test_model_slug_with_a_version_does_not_pollute_mask_sources() -> None:
    """A slug with '2.1' would be cut by `_MASK_SOURCE_RE`; the register owns the version."""
    assert classify("ai-assisted:sam2_1+point-reviewed").tier is (
        MaskSourceTier.AI_ASSISTED_REVIEWED
    )
    entry = rg.Entry(
        value=0.93,
        n=20,
        set_name="golden",
        provenance="mask source: ai-assisted:sam2_1+point-reviewed; median 0.98",
    )
    assert rg.mask_sources(entry) == ["ai-assisted:sam2_1+point-reviewed"]


# --------------------------------------------------------------- four-consumer source guard


def test_four_consumers_share_one_source_of_truth() -> None:
    for relative in FOUR_CONSUMERS:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "mask_provenance" in text, f"{relative} does not import the shared module"
        assert 'startswith("hand")' not in text, f"{relative} still has an inline hand literal"
    gate_text = (REPO_ROOT / "infra/ci/gates/regression_gate.py").read_text(encoding="utf-8")
    assert "MACHINE_REFERENCE_MARKERS: tuple" not in gate_text
    assert "MACHINE_REFERENCE_MARKERS: Final" not in gate_text


def test_source_guard_whitelist_is_explicit_and_excluded() -> None:
    assert set(LITERAL_WHITELIST).isdisjoint(FOUR_CONSUMERS)
    assert "evals/tools/fetch_web_images.py" in LITERAL_WHITELIST
    assert "evals/fixtures/prettify/build_fixtures.py" in LITERAL_WHITELIST
    # The whitelisted provisional literal is untouched but classifies correctly anyway.
    assert classify("algorithmic:background_colour_distance").tier is MaskSourceTier.MACHINE


@pytest.mark.parametrize("relative", FOUR_CONSUMERS)
def test_each_consumer_delegates_to_the_shared_module(relative: str) -> None:
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    assert "mask_provenance" in text
    assert 'startswith("hand")' not in text
