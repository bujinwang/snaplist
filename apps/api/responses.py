"""Response bodies that are not part of the frozen wire contract.

`apps/api/schemas.py` is normative and its model set is pinned by
`apps/api/tests/test_contract_parity.py`. The export report below is newer than CONTRACTS v1.0.0
(it exists because a seller must learn which columns to fill *before* uploading), so it lives
here rather than in `schemas.py`: adding a model there without a zod mirror fails the parity
test, and adding a mirrored model would be a contract version bump for a shape that has no
external consumer yet.

It is still a real contract on the wire, just not a *frozen* one: `apps/api/tests/test_exports.py`
asserts its keys field by field. When it graduates to CONTRACTS.md, move it to `schemas.py` and
mirror it in zod — that is the ADR, not this file.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ExportRefusalReason(StrEnum):
    """Why a marketplace mapper refused an item.

    Deliberately its own enum, not a subset of `apps.api.schemas.ReasonCode`. They look alike and
    answer different questions (see `docs/CONTRACTS.md` §5, "Export refusals are not job failures"):
    a `Job` failure is something the pipeline can retry, an export refusal is something the seller
    fixes in the attributes sheet. Sharing one enum would make `insufficient_data` inherit
    retry semantics it does not have, and would let a future value silently exist in both domains.
    """

    INSUFFICIENT_DATA = "insufficient_data"


class ExportedRowModel(BaseModel):
    """One row in the CSV, and the item it came from."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    #: Zero-based index among data rows (the header is not counted), so a client can point at
    #: "row 3 of the file" without re-deriving the order from the request.
    row_index: int = Field(ge=0)


class RefusedRowModel(BaseModel):
    """One item the mapper would not export.

    `reason` is an `ExportRefusalReason`. It is deliberately **not** named `reason_code`: that name
    is taken by the frozen `Job` failure enum, and an export refusal is a different domain.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    reason: ExportRefusalReason
    fields: list[str] = Field(default_factory=list)


class ExportBatchResponse(BaseModel):
    """The JSON body of `POST /v1/exports` (the default representation).

    Why JSON is the default and CSV is the opt-in (`Accept: text/csv`): the "these columns are
    yours to fill" decision has to reach the client as structured data, before the seller taps
    download. A header would do for debugging, but headers are the first thing a proxy, a CDN or
    a logging layer drops.
    """

    model_config = ConfigDict(extra="forbid")

    #: The CSV document itself, so a client that wants the file does not need a second request.
    csv: str
    #: Columns a seller must fill by hand before the file is uploadable. **Empty array, never
    #: omitted** — "nothing to fill" and "we did not tell you" must be distinguishable.
    requires_manual_columns: list[str] = Field(default_factory=list)
    exported: list[ExportedRowModel] = Field(default_factory=list)
    #: Non-empty on a partial export, which is still a 200. Only "every row refused" is a 422.
    refused: list[RefusedRowModel] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
