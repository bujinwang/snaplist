"""Cross-language contract parity: pydantic (apps/api/schemas.py) vs zod
(packages/shared/src/schemas.ts).

The zod side is read out of the compiled JS by a child `node` process, so this compares the
two real artifacts rather than two copies of a hardcoded list. If packages/shared has not
been built yet the test skips with a reason instead of lying either way — `make test` builds
it first, so in CI this test always runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel

import apps.api.responses as pr
import apps.api.schemas as py

ROOT = Path(__file__).resolve().parents[3]
ZOD_DIST = ROOT / "packages" / "shared" / "dist" / "src" / "schemas.js"

NODE_SCRIPT = """
const s = require(process.argv[1]);
const models = [
  'Measurements', 'ItemAttributes', 'AssetPack', 'Job', 'CreateItemResponse',
  'ErrorBody', 'ErrorResponse', 'Usage', 'CheckoutRequest', 'CheckoutResponse',
  'ExportRequest',
  'ExportedRowModel', 'RefusedRowModel', 'ExportBatchResponse',
];
const enums = [
  'GARMENT_CATEGORIES', 'PATTERNS', 'FITS', 'CONDITIONS', 'DEFECTS',
  'MEASUREMENT_SOURCES', 'JOB_KINDS', 'JOB_STATES', 'REASON_CODES', 'ERROR_CODES',
  'PLAN_TIERS', 'MARKETPLACES', 'EXPORT_REFUSAL_REASONS',
];
const out = { fields: {}, enums: {}, version: s.CONTRACT_VERSION };
for (const m of models) {
  if (!s[m] || !s[m].shape) { out.fields[m] = null; continue; }
  out.fields[m] = Object.keys(s[m].shape).sort();
}
for (const e of enums) out.enums[e] = s[e] ? Array.from(s[e]) : null;
process.stdout.write(JSON.stringify(out));
"""

PY_MODELS: dict[str, type[BaseModel]] = {
    "Measurements": py.Measurements,
    "ItemAttributes": py.ItemAttributes,
    "AssetPack": py.AssetPack,
    "Job": py.Job,
    "CreateItemResponse": py.CreateItemResponse,
    "ErrorBody": py.ErrorBody,
    "ErrorResponse": py.ErrorResponse,
    "Usage": py.Usage,
    "CheckoutRequest": py.CheckoutRequest,
    "CheckoutResponse": py.CheckoutResponse,
    "ExportRequest": py.ExportRequest,
    # The JSON body of POST /v1/exports. These live in responses.py, not schemas.py: schemas.py is
    # the frozen wire contract's home, and mixing a v1 addition into it would make "frozen" a word
    # without a boundary. Mapped by TS name -> python model, so the key names differ on purpose.
    "ExportedRowModel": pr.ExportedRowModel,
    "RefusedRowModel": pr.RefusedRowModel,
    "ExportBatchResponse": pr.ExportBatchResponse,
}

PY_ENUMS: dict[str, object] = {
    "GARMENT_CATEGORIES": py.GarmentCategory,
    "PATTERNS": py.Pattern,
    "FITS": py.Fit,
    "CONDITIONS": py.Condition,
    "DEFECTS": py.Defect,
    "MEASUREMENT_SOURCES": py.MeasurementSource,
    "JOB_KINDS": py.JobKind,
    "JOB_STATES": py.JobState,
    "REASON_CODES": py.ReasonCode,
    "ERROR_CODES": py.ErrorCode,
    "PLAN_TIERS": py.PlanTier,
    "MARKETPLACES": py.Marketplace,
    "EXPORT_REFUSAL_REASONS": pr.ExportRefusalReason,
}


@pytest.fixture(scope="module")
def zod() -> dict:
    # FAIL-CLOSED, deliberately. These used to `pytest.skip`, which reported green for a check
    # that never ran. A verification that could not run must look different from one that passed
    # — that distinction is the whole point of this file. If the build is missing, fix the build;
    # do not read the green as parity.
    if shutil.which("node") is None:
        pytest.fail(
            "node is not installed, so the zod mirror cannot be checked. "
            "Contract parity is UNVERIFIED — install node, then re-run."
        )
    if not ZOD_DIST.exists():
        pytest.fail(
            f"{ZOD_DIST.relative_to(ROOT)} missing — the zod mirror has never been built, so "
            f"parity is UNMEASURED, not passed. Run `pnpm -w build` (or `make test`, which "
            f"builds before running pytest), then re-run this file."
        )
    proc = subprocess.run(
        ["node", "-e", NODE_SCRIPT, str(ZOD_DIST)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_contract_version_matches(zod: dict) -> None:
    assert zod["version"] == py.CONTRACT_VERSION


def test_every_model_has_a_zod_mirror(zod: dict) -> None:
    missing = [name for name, fields in zod["fields"].items() if fields is None]
    assert not missing, f"no zod object exported for: {missing}"


def test_field_names_match_field_for_field(zod: dict) -> None:
    mismatches = {}
    for name, model in PY_MODELS.items():
        py_fields = sorted(model.model_fields)
        ts_fields = zod["fields"][name]
        if py_fields != ts_fields:
            mismatches[name] = {
                "python_only": sorted(set(py_fields) - set(ts_fields)),
                "zod_only": sorted(set(ts_fields) - set(py_fields)),
            }
    assert not mismatches, mismatches


def test_enum_literals_and_order_match(zod: dict) -> None:
    mismatches = {}
    for name, enum_cls in PY_ENUMS.items():
        py_vals = [m.value for m in enum_cls]  # type: ignore[attr-defined]
        ts_vals = zod["enums"][name]
        if py_vals != ts_vals:
            mismatches[name] = {"python": py_vals, "zod": ts_vals}
    assert not mismatches, mismatches


def test_no_python_model_is_missing_from_this_test() -> None:
    # Both modules: `schemas.py` is the frozen wire contract, `responses.py` holds the v1 export
    # response models. A model in either one that is absent from PY_MODELS is unguarded, which is
    # how the export response shipped without a parity check in the first place.
    exported = {
        name
        for module in (py, pr)
        for name, obj in vars(module).items()
        if isinstance(obj, type) and hasattr(obj, "model_fields") and obj is not BaseModel
    }
    assert exported == set(PY_MODELS), (
        "a pydantic model exists that nobody mirrored in zod. "
        f"unmirrored: {sorted(exported - set(PY_MODELS))}; "
        f"stale registry entries: {sorted(set(PY_MODELS) - exported)}"
    )
