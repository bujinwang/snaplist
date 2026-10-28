"""Bridge to the **one** CSV implementation, which lives in TypeScript.

Why the API shells out to node instead of re-implementing the mapper
-------------------------------------------------------------------
`packages/shared/src/marketplaces/` is the single source of truth for the eBay / Poshmark /
Vinted column names, the condition tables, the refusal rules and the `requires_manual_columns`
derivation (E7-T1/T2). A second copy in Python would drift the first time somebody fixes a
header, and the drift would show up as a seller uploading a broken CSV — the exact failure the
mappers exist to prevent. So the export endpoint runs the compiled shared package in a
short-lived `node` process and pipes JSON in, CSV (plus the batch report) out.

What that costs, stated plainly
-------------------------------
* `node` and a built `packages/shared/dist` must exist in the API image. `pnpm -w build` is part
  of `make install`; the container image must do the same.
* ~30-60ms of process startup per export. Exports are a per-item user action, not a hot path.
* The endpoint degrades honestly: no node or no build ⇒ **503** with `internal` and
  `details.missing_dependency`, never an empty CSV that looks like a successful export of zero
  items. (A missing binary is not a missing credential, so it does not borrow that detail key —
  the two would send whoever fixes it to different places.)

The alternative (a Python copy) was rejected on purpose. If a later ticket moves exports to a
queue worker (job kind `export`), that worker calls this same function.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.api import errors
from apps.api.schemas import Marketplace

ROOT = Path(__file__).resolve().parents[2]
MARKETPLACE_ENTRY = ROOT / "packages" / "shared" / "dist" / "src" / "marketplaces" / "index.js"
NODE_TIMEOUT_SECONDS = 20

# Reads the payload from stdin so nothing user-supplied lands on a command line, and prints a
# single JSON document:
#   {csv, exported:[{item_id,row_index}], refused:[{item_id,reason,fields}],
#    requires_manual_columns:[...], warnings:[...]}
NODE_SCRIPT = """
const fs = require("node:fs");
const mod = require(process.argv[1]);
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const batch = mod.exportRows(payload.marketplace, payload.items);
process.stdout.write(
  JSON.stringify({
    csv: batch.csv,
    exported: batch.exported,
    refused: batch.refused,
    requires_manual_columns: batch.requires_manual_columns,
    warnings: batch.warnings,
  }),
);
"""


@dataclass(frozen=True, slots=True)
class ExportedRow:
    """One row that made it into the CSV, and where to find it (0-based, header excluded)."""

    item_id: str
    row_index: int


@dataclass(frozen=True, slots=True)
class RefusedRow:
    """One item the mapper would not export, and the attributes that blocked it.

    `reason` is `insufficient_data` and is **not** a `Job.reason_code` — see
    `packages/shared/src/marketplaces/types.ts` for why the two vocabularies must stay apart.
    """

    item_id: str
    fields: tuple[str, ...] = ()
    reason: str = "insufficient_data"


@dataclass(frozen=True, slots=True)
class ExportBatch:
    """A real CSV, what went into it, what did not, and what the seller still has to fill in."""

    csv_text: str
    exported: tuple[ExportedRow, ...] = ()
    refused: tuple[RefusedRow, ...] = ()
    requires_manual_columns: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def line_count(self) -> int:
        """Header + data rows. Used by tests and diagnostics, not by the wire."""
        return len([line for line in self.csv_text.splitlines() if line]) if self.csv_text else 0


class ExportUnavailable(RuntimeError):
    """The mapper could not be executed. Always a 503, never an empty success."""

    def __init__(self, message: str, *, missing: str) -> None:
        super().__init__(message)
        #: What is absent, for `details.missing_dependency` — "node" or the dist entry path.
        self.missing = missing


def _node_binary() -> str:
    node = shutil.which("node")
    if node is None:
        raise ExportUnavailable(
            "`node` is not on PATH, so the marketplace mapper cannot run. Install node >= 20.",
            missing="node",
        )
    return node


def _ensure_built() -> Path:
    if not MARKETPLACE_ENTRY.exists():
        raise ExportUnavailable(
            f"{MARKETPLACE_ENTRY.relative_to(ROOT)} is missing. Run `pnpm -w build` (or "
            "`make install`) before exporting.",
            missing=str(MARKETPLACE_ENTRY.relative_to(ROOT)),
        )
    return MARKETPLACE_ENTRY


def serialize_items(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build the JSON the TS mapper expects. One place, so the shape cannot drift."""
    items: list[dict[str, Any]] = []
    for record in records:
        items.append(
            {
                "item_id": str(record["item_id"]),
                "attributes": record.get("attributes"),
                "prettified_url": record.get("prettified_url"),
                "on_model_urls": list(record.get("on_model_urls") or []),
                # Per item, never per batch: a batch-level price would price every garment the
                # same and nothing would error. `None` means "not supplied" (cell left empty).
                "price_cents": record.get("price_cents"),
            }
        )
    return items


def build_export(
    marketplace: Marketplace,
    records: Sequence[Mapping[str, Any]],
) -> ExportBatch:
    """Run the TS mapper over `records`. Pure I/O boundary; no mapping logic lives here."""
    if not records:
        raise errors.bad_request("No items to export.")

    node = _node_binary()
    entry = _ensure_built()
    payload = {"marketplace": marketplace.value, "items": serialize_items(records)}

    try:
        completed = subprocess.run(
            [node, "-e", NODE_SCRIPT, str(entry)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=NODE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExportUnavailable(
            f"marketplace mapper did not finish within {NODE_TIMEOUT_SECONDS}s",
            missing="node-process",
        ) from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip().splitlines()
        tail = " | ".join(stderr[-3:])[:500] if stderr else "(no stderr)"
        raise ExportUnavailable(
            f"marketplace mapper failed: {tail}", missing="marketplace-mapper"
        )

    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ExportUnavailable(
            "marketplace mapper returned a non-JSON document", missing="marketplace-mapper"
        ) from exc

    return ExportBatch(
        csv_text=str(parsed.get("csv", "")),
        exported=tuple(
            ExportedRow(
                item_id=str(row.get("item_id", "")),
                row_index=int(row.get("row_index", 0)),
            )
            for row in parsed.get("exported", [])
        ),
        refused=tuple(
            RefusedRow(
                item_id=str(row.get("item_id", "")),
                fields=tuple(str(name) for name in row.get("fields", [])),
                reason=str(row.get("reason", "insufficient_data")),
            )
            for row in parsed.get("refused", [])
        ),
        requires_manual_columns=tuple(
            str(name) for name in parsed.get("requires_manual_columns", [])
        ),
        warnings=tuple(str(name) for name in parsed.get("warnings", [])),
    )
