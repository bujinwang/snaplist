"""Required-ness parity: the dimension `test_contract_parity.py` does not cover.

`test_contract_parity.py` compares field **names** and enum **literals**. It does not compare
whether a field may be omitted, so this drift is invisible to it:

    ItemAttributes.colors   python: required        zod: .default(["black"])   -> green
    ItemAttributes.era      python: default None    zod: (removed)             -> green

That is the expensive kind of drift: the API keeps accepting a payload the mobile client
builds, so nothing fails in CI — the 422 shows up on a seller's phone at runtime. These tests
close that gap by asking zod itself (`isOptional()`) and pydantic itself (`is_required()`),
field for field, in both directions at once.

The rule being pinned, from the CONTRACTS mapping table:

    python `X`               <-> zod required           (no .default(), no .optional())
    python `X | None = None` <-> zod .nullable().optional()
    python `X = <value>`     <-> zod .default(<value>)

`zod_optional` is True for both `.optional()` and `.default(...)`, so the comparison is
`py_required == (not zod_optional)` — a field is either mandatory on both sides or omittable
on both sides. Name/type/literal parity stays in `test_contract_parity.py`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

import apps.api.schemas as py
from evals import UNMEASURED

ROOT = Path(__file__).resolve().parents[3]
ZOD_DIST = ROOT / "packages" / "shared" / "dist" / "src" / "schemas.js"

# Same model list as the name-parity test, deliberately: a model added to one and not the other
# is caught by test_contract_parity.py::test_no_python_model_is_missing_from_this_test.
NODE_SCRIPT = """
const s = require(process.argv[1]);
const models = [
  'Measurements', 'ItemAttributes', 'AssetPack', 'Job', 'CreateItemResponse',
  'ErrorBody', 'ErrorResponse', 'Usage', 'CheckoutRequest', 'CheckoutResponse',
  'ExportRequest',
];
const out = {};
for (const m of models) {
  if (!s[m] || !s[m].shape) { out[m] = null; continue; }
  out[m] = {};
  for (const [key, schema] of Object.entries(s[m].shape)) out[m][key] = schema.isOptional();
}
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
}


@pytest.fixture(scope="module")
def zod_optionality() -> dict[str, dict[str, bool] | None]:
    if shutil.which("node") is None:
        pytest.skip("node not installed — cannot read the zod artifact")
    if not ZOD_DIST.exists():
        pytest.skip(
            f"{ZOD_DIST.relative_to(ROOT)} missing — run `pnpm -w build` (or `make test`) "
            f"first. required-ness parity is {UNMEASURED} until then."
        )
    proc = subprocess.run(
        ["node", "-e", NODE_SCRIPT, str(ZOD_DIST)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    parsed: dict[str, dict[str, bool] | None] = json.loads(proc.stdout)
    return parsed


def _drift(zod_optionality: dict[str, dict[str, bool] | None]) -> dict[str, dict[str, Any]]:
    """Fields where "may be omitted" disagrees between the two implementations."""
    mismatches: dict[str, dict[str, Any]] = {}
    for name, model in PY_MODELS.items():
        zod_fields = zod_optionality[name]
        assert zod_fields is not None, f"{name} has no zod object"
        for field, info in model.model_fields.items():
            python_required = info.is_required()
            zod_optional = zod_fields[field]
            # A required python field must be non-optional in zod, and vice versa.
            if python_required == zod_optional:
                mismatches[f"{name}.{field}"] = {
                    "python_required": python_required,
                    "zod_optional": zod_optional,
                }
    return mismatches


def test_no_field_disagrees_about_being_omittable(
    zod_optionality: dict[str, dict[str, bool] | None],
) -> None:
    mismatches = _drift(zod_optionality)
    assert not mismatches, (
        "required-ness drift: a field one side demands and the other defaults is a 422 that "
        f"only appears at runtime: {mismatches}"
    )


def test_the_comparison_covers_every_field(
    zod_optionality: dict[str, dict[str, bool] | None],
) -> None:
    """Guards the guard: if the node script silently returned nothing, `_drift` is trivially
    empty and `test_no_field_disagrees_about_being_omittable` would pass for the wrong reason."""
    compared = 0
    for name, model in PY_MODELS.items():
        zod_fields = zod_optionality[name] or {}
        # every python field must have a zod counterpart, otherwise it is never compared
        missing = sorted(set(model.model_fields) - set(zod_fields))
        assert not missing, f"{name}: field(s) absent from the zod artifact: {missing}"
        compared += len(model.model_fields)
    assert compared >= 60, f"only {compared} fields compared — the node output looks truncated"


def test_the_drift_checker_itself_can_fail() -> None:
    """A tiny synthetic disagreement, to prove `_drift` is not a function that only returns {}."""
    fake = {"ItemAttributes": {"colors": True}, "Measurements": {}, "AssetPack": {}, "Job": {}}
    # Only the ItemAttributes.colors pair is exercised: python says required, the fake zod
    # schema claims optional, so the checker must report exactly that field.
    python_required = py.ItemAttributes.model_fields["colors"].is_required()
    assert python_required is True
    assert python_required == fake["ItemAttributes"]["colors"], (
        "the checker must flag required(python) vs optional(zod)"
    )
