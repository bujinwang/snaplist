"""Proves the vision scaffold is importable without any model weights, GPU or network.

The first test is the one the lead asked for: import every module in the package. The rest
guard the stub contract so a later role cannot quietly change a public function signature
that apps/api or evals already call.
"""

import importlib
import inspect
import pkgutil

import pytest

import packages.vision as vision_pkg

MODULES = [
    "segment", "matte", "rectify", "prettify", "attrs",
    "vton", "dedupe", "cli", "types", "runtime",
]


def test_every_vision_module_imports() -> None:
    discovered = {m.name for m in pkgutil.iter_modules(vision_pkg.__path__)}
    missing = [m for m in MODULES if m not in discovered]
    assert not missing, f"module files missing from packages/vision: {missing}"
    for name in MODULES:
        importlib.import_module(f"packages.vision.{name}")


def test_module_names_are_snake_case_files() -> None:
    for name in MODULES:
        assert name == name.lower()
        assert "-" not in name


def test_pipeline_stage_order_is_frozen() -> None:
    assert vision_pkg.PIPELINE_STAGES == ("segment", "matte", "rectify", "prettify", "attrs")


def test_stubs_raise_stage_not_implemented() -> None:
    from packages.vision import StageNotImplemented
    from packages.vision.cli import main
    from packages.vision.vton import get_adapter

    # `segment` left this list when E1-T2 landed: an empty-bytes call is now a ValueError
    # (input validation) and an unavailable backend raises SegmentBackendUnavailable, which
    # is still a StageNotImplemented. Both are covered in test_segment.py.
    # `dedupe` left this list when E3-T4 landed, for the same reason — an empty-bytes call is
    # a ValueError, covered in test_dedupe.py::test_empty_query_bytes_raise. `vton` is still
    # a stub, so it carries this assertion.
    with pytest.raises(StageNotImplemented):
        get_adapter("fashn")
    assert main(["--in", "x.jpg", "--out", "/tmp/snaplist-cli"]) == 2


def test_public_signatures_are_stable() -> None:
    from packages.vision.attrs import get_extractor
    from packages.vision.dedupe import find_duplicate
    from packages.vision.matte import matte
    from packages.vision.prettify import prettify
    from packages.vision.rectify import rectify
    from packages.vision.segment import segment
    from packages.vision.vton import get_adapter

    expected = {
        segment: ["image", "backend"],
        matte: ["image", "mask_png"],
        rectify: ["image", "alpha_png", "category"],
        # `protect` joined the frozen list with E2-T2: it is a default-`None` keyword, so
        # every existing caller compiles and behaves exactly as before, and the entry here is
        # the acknowledgement that the contract gained an optional input — not a silent change.
        prettify: ["flat_png", "alpha_png", "mode", "protect"],
        get_extractor: ["name"],
        find_duplicate: ["user_id", "image", "candidates"],
        get_adapter: ["name"],
    }
    for fn, params in expected.items():
        assert list(inspect.signature(fn).parameters) == params, fn.__name__

    signature = inspect.signature(prettify)
    assert signature.parameters["protect"].default is None, "protect must stay optional"


def test_vton_is_documented_as_single_owner_file() -> None:
    import packages.vision.vton as vton_module

    doc = vton_module.__doc__ or ""
    assert "ONE FILE, ONE OWNER" in doc


def test_eval_relevant_types_are_present() -> None:
    from packages.vision.types import PrettifyMode, SegmentBackend, StageMeta

    assert SegmentBackend.UNMEASURED in tuple(SegmentBackend)
    assert PrettifyMode.PASS_THROUGH in tuple(PrettifyMode)
    meta = StageMeta(stage="segment")
    # None means UNMEASURED. A placeholder 0.0 would be a lie in the cost report.
    assert meta.seconds is None
    assert meta.cost_usd is None
