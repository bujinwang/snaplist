"""`POST /v1/exports` — real CSV text plus the batch report, from the single TS mapper.

Two representations, one resource
---------------------------------
* **default** — `200 application/json`, an `ExportBatchResponse`:
  `{csv, requires_manual_columns, exported, refused, warnings}`. This is the default because the
  "these columns are yours to fill" decision must reach the client as structured data *before* the
  seller downloads anything. A seller who uploads a category-less CSV has cost themselves the
  sale, and a header is the first thing a proxy or a log drops.
* **`Accept: text/csv`** — `200 text/csv` with `content-disposition`, for a direct download and for
  curl debugging. It still carries `x-snaplist-requires-manual-columns`, but that header is an
  *alignment aid*, never the only place the information exists.

Both send `Vary: Accept, Accept-Encoding`. Without it a cache can serve the JSON body to a client
that asked for the file (or the reverse), and that failure is invisible locally.

Status codes, fixed so the shape cannot drift
--------------------------------------------
* every row refused → **422** with the standard error envelope and `reason=insufficient_data`
  naming the fields; a batch where nothing could be exported is not a success;
* some rows refused → **200**, with `refused` non-empty. Partial success must not be destroyed by
  a 422, and must not be disguised as a clean run;
* everything exported → **200** with `requires_manual_columns: []` (present, not omitted).

`prices` lives outside the frozen contract
------------------------------------------
`ExportRequest` is a frozen model with `extra="forbid"` and a zod mirror, so `prices` is parsed
here, separately, and the rest of the body is validated by the frozen model. When `prices`
graduates into CONTRACTS.md, delete this shim — do not grow it.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from apps.api import errors, exporters, usage, web
from apps.api.auth import Caller, current_caller
from apps.api.repository import Repository
from apps.api.responses import ExportBatchResponse, ExportedRowModel, RefusedRowModel
from apps.api.schemas import ErrorResponse, ExportRequest, Marketplace

router = APIRouter(prefix="/v1", tags=["exports"])

#: One price for one item. `$100,000` is a typo threshold, not a pricing opinion: above it the
#: number is far more likely to be cents-where-dollars-were-meant than a real listing.
MAX_PRICE_CENTS = 10_000_000

VARY_HEADERS = {"vary": "Accept, Accept-Encoding"}
CSV_MEDIA_TYPE = "text/csv; charset=utf-8"


def _wants_csv(request: Request) -> bool:
    return "text/csv" in (request.headers.get("accept") or "").lower()


def _parse_prices(raw_prices: Any, item_ids: list[UUID]) -> dict[UUID, int]:
    """Validate the optional `prices` map. Every rejection is a 422, never a silent drop.

    Silently ignoring a price the client sent would make the seller believe it was applied — the
    same class of bug as emitting an empty cell and calling the export a success.
    """
    if raw_prices is None:
        return {}
    if not isinstance(raw_prices, dict):
        raise errors.unprocessable(
            "`prices` must be an object mapping item_id to a price in integer cents."
        )

    allowed = set(item_ids)
    prices: dict[UUID, int] = {}
    for raw_id, raw_cents in raw_prices.items():
        try:
            item_id = UUID(str(raw_id))
        except ValueError as exc:
            raise errors.unprocessable(
                "`prices` contains a key that is not an item uuid.",
                details={"prices": str(raw_id)[:80]},
            ) from exc
        if item_id not in allowed:
            # Covers both "an item belonging to someone else" and "an id not in this batch":
            # either way the client's state is wrong, and a price on a row that will not be
            # exported is a misunderstanding worth failing on.
            raise errors.unprocessable(
                "`prices` references an item that is not in `item_ids` for this account.",
                details={"item_id": str(item_id)},
            )
        if isinstance(raw_cents, bool) or not isinstance(raw_cents, int):
            raise errors.unprocessable(
                "`prices` values must be integers (cents), not strings or floats.",
                details={"item_id": str(item_id)},
            )
        if raw_cents < 0 or raw_cents > MAX_PRICE_CENTS:
            raise errors.unprocessable(
                f"`prices` values must be between 0 and {MAX_PRICE_CENTS} cents.",
                details={"item_id": str(item_id), "cents": str(raw_cents)},
            )
        prices[item_id] = raw_cents
    return prices


async def _parse_body(request: Request) -> tuple[ExportRequest, dict[UUID, int]]:
    raw = await request.body()
    if not raw:
        raise errors.unprocessable("Request body is required.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise errors.unprocessable(
            "Request body is not valid JSON.",
            details={"body": f"{exc.msg} at line {exc.lineno} column {exc.colno}"},
        ) from exc
    if not isinstance(payload, dict):
        raise errors.unprocessable("Request body must be a JSON object.")

    # Lift the non-contract key out before the frozen model sees it (`extra="forbid"`).
    prices_raw = payload.pop("prices", None)
    frozen_part = web.parse_json_body(request, json.dumps(payload).encode(), ExportRequest)
    return frozen_part, _parse_prices(prices_raw, frozen_part.item_ids)


@router.post(
    "/exports",
    responses={
        200: {
            "content": {
                "application/json": {"schema": ExportBatchResponse.model_json_schema()},
                "text/csv": {"schema": {"type": "string"}},
            },
            "description": "The CSV plus what the seller still has to fill in.",
        },
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    summary="Build a marketplace CSV for up to 200 of the caller's items",
)
async def create_export(
    request: Request,
    caller: Caller = Depends(current_caller),
) -> Response:
    """Fetch the caller's items, hand them to the TS mapper, return CSV + report."""
    repository: Repository = web.repository_of(request)
    body, prices = await _parse_body(request)

    records = []
    for item_id in body.item_ids:
        record = repository.get_item(item_id, user_id=caller.user_id)
        if record is None:
            # 404 rather than silently skipping: exporting a subset the seller did not ask for
            # is how a seller ends up listing three of four items.
            raise errors.not_found(f"No item {item_id} for this account.")
        records.append(
            {
                "item_id": record.id,
                "attributes": (
                    record.attributes.model_dump(mode="json") if record.attributes else None
                ),
                "prettified_url": record.path_for("prettified"),
                "on_model_urls": record.on_model_paths(),
                "price_cents": prices.get(item_id),
            }
        )

    try:
        batch = exporters.build_export(body.marketplace, records)
    except exporters.ExportUnavailable as exc:
        raise errors.unavailable(
            str(exc), details={errors.DETAIL_MISSING_DEPENDENCY: exc.missing}
        ) from exc

    if not batch.exported:
        first = batch.refused[0] if batch.refused else None
        raise errors.unprocessable(
            "No row could be exported for this marketplace; the mapper refused every item.",
            details={
                "reason": "insufficient_data",
                "item_id": first.item_id if first else "",
                "missing_fields": ",".join(first.fields) if first else "attributes",
                "refused_rows": str(len(batch.refused)),
            },
        )

    with repository.transaction():
        repository.append_usage_event(
            usage.UsageEventRecord(
                user_id=caller.user_id,
                event=usage.EVENT_EXPORT_CREATED,
                units=1,
                marketplace=body.marketplace.value,
                metadata={
                    "rows": str(len(batch.exported)),
                    "refused": str(len(batch.refused)),
                    "manual_columns": ",".join(batch.requires_manual_columns)[:200],
                },
                created_at=usage.utcnow(),
            )
        )

    if _wants_csv(request):
        return _csv_response(body.marketplace, batch)
    return _json_response(batch)


def _csv_headers(batch: exporters.ExportBatch) -> dict[str, str]:
    headers = {
        **VARY_HEADERS,
        # Alignment aid for humans and curl; the JSON body is the authoritative carrier.
        "x-snaplist-requires-manual-columns": ",".join(batch.requires_manual_columns),
        "x-snaplist-export-rows": str(len(batch.exported)),
        "x-snaplist-export-refused": str(len(batch.refused)),
    }
    if batch.refused:
        blocking: list[str] = []
        for refused in batch.refused:
            blocking.extend(refused.fields)
        headers["x-snaplist-export-refused-fields"] = ",".join(sorted(set(blocking)))[:500]
    return headers


def _csv_response(marketplace: Marketplace, batch: exporters.ExportBatch) -> Response:
    headers = _csv_headers(batch)
    headers["content-disposition"] = f'attachment; filename="snaplist-{marketplace.value}.csv"'
    return Response(content=batch.csv_text, media_type=CSV_MEDIA_TYPE, headers=headers)


def _json_response(batch: exporters.ExportBatch) -> JSONResponse:
    body = ExportBatchResponse(
        csv=batch.csv_text,
        requires_manual_columns=list(batch.requires_manual_columns),
        exported=[
            ExportedRowModel(item_id=row.item_id, row_index=row.row_index)
            for row in batch.exported
        ],
        refused=[
            RefusedRowModel(item_id=row.item_id, reason=row.reason, fields=list(row.fields))
            for row in batch.refused
        ],
        warnings=list(batch.warnings),
    )
    return JSONResponse(content=body.model_dump(mode="json"), headers=dict(VARY_HEADERS))
