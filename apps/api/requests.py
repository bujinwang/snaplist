"""Request bodies that are NOT part of the frozen wire contract.

`apps/api/schemas.py` is normative and its model set is pinned by
`apps/api/tests/test_contract_parity.py` (a model added there without a zod mirror fails the
build). Request bodies the *implementation* needs therefore live here, so "the contract" stays
exactly the ten models in CONTRACTS.md and an internal shape can change in a patch release.

Anything a client sends that also comes back on the wire (`ItemAttributes`,
`ExportRequest`) stays in `schemas.py`. This module holds only inbound-only shapes.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CreateItemRequest(BaseModel):
    """Optional metadata for `POST /v1/items`.

    The bytes themselves are **not** uploaded here: direct upload needs a signed Supabase
    Storage URL, which is E5-T4 and currently 501. What this slice accepts is the description
    of the photo the client has already queued locally, which is enough to create the item and
    the job row deterministically.

    `source_sha256` is the dedupe key E3-T4 will use; accepting it now means the column and
    the client contract do not change when dedupe lands.
    """

    model_config = ConfigDict(extra="forbid")

    filename: str | None = Field(default=None, max_length=255)
    content_type: str | None = Field(default=None, max_length=100)
    byte_size: int | None = Field(default=None, ge=0, le=50_000_000)
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
