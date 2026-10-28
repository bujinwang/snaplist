"""`GET /v1/usage` — the current period, derived from the ledger.

Why this is always a 200
-------------------------
A seller at their ceiling still needs to read the number, and the mobile client reads
`items_included` / `items_remaining` from here to draw the "2 of 3 left" line. The **402** lives
at the enqueue points (`POST /v1/items`, `POST /v1/exports`, on-model), which is exactly where
E8-T3 in docs/PLAN.md puts it: enforced at enqueue so a double-tap cannot spend two units.

Nothing here is cached and nothing here is a counter column: `used` is `-sum(units)` over the
ledger for the current calendar month, so a refund is visible immediately and a lost API
process cannot lose a number.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from apps.api import usage, web
from apps.api.auth import Caller, current_caller
from apps.api.schemas import Usage

router = APIRouter(prefix="/v1", tags=["usage"])


@router.get("/usage", response_model=Usage, summary="Quota consumption for the current period")
def get_usage(request: Request, caller: Caller = Depends(current_caller)) -> Usage:
    repository = web.repository_of(request)
    limits = usage.limits_from_settings(web.settings_of(request))
    return usage.usage_snapshot(repository, caller.user_id, limits=limits)
