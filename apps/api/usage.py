"""Quota metering (E8-T3) — the ledger, not a counter.

The rules this module implements, all of them testable:

1. A unit is spent **at enqueue**, inside the same transaction that creates the item and the
   job. A double-tap cannot spend two units, because the second call sees the idempotency key
   or, failing that, the fresh balance.
2. A terminal failure refunds **exactly one** unit per job. The refund is a `+1` row in
   `usage_events` guarded by a unique partial index on `(job_id)`; appending it twice fails in
   the database, not in a retry branch.
3. A refund never mints quota: if no `item_enqueued` row exists for that job, nothing is
   refunded. Otherwise a client that could reach the refund path would get free units.
4. "Used" is derived: `-sum(units)` over the current period for the two ledger events. There
   is no `items_used` column to drift.

## Plan ceilings: one code-side source, one runtime override

The number 3 used to exist in three places (`Settings`, this module, and the `plan_limits`
table). Three copies of a number that must agree is a bug waiting for a busy afternoon: change
one, forget two, and **no test goes red** because each copy is individually consistent.

Now there is exactly one resolution order, implemented once in `plan_ceiling`:

1. **`plan_limits` row** — authoritative. Ops can raise a ceiling without a deploy, which is the
   whole reason it is a table.
2. **`Settings`** (`free_items_per_month` / …) — the compiled default, used when the row is
   missing.
3. Nothing else. `PlanLimits` has no defaults, so a caller that forgets to supply one gets a
   `TypeError` at import-adjacent time instead of a silent 3 from nowhere.

`test_usage.py` pins the `plan_limits` seed against the `Settings` defaults, so the two cannot
drift silently, and pins the precedence itself (DB says 5 while Settings says 3 ⇒ the 6th create
is the one that gets a 402).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from apps.api.repository import (
    Repository,
    UsageEventRecord,
    utcnow,
)
from apps.api.schemas import PlanTier, Usage
from apps.api.settings import Settings, get_settings

# Metric event names, frozen in packages/shared/src/analytics.ts and mirrored in the
# `usage_events.event` CHECK constraint of infra/migrations/0001_init.sql.
EVENT_ITEM_ENQUEUED = "item_enqueued"
EVENT_JOB_REFUNDED = "job_refunded"
EVENT_EXPORT_CREATED = "export_created"
EVENT_ATTRIBUTES_EDITED = "attributes_edited"
EVENT_FREE_TIER_ITEM_LIMIT = "free_tier_item_limit"


@dataclass(frozen=True, slots=True)
class PlanLimits:
    """Items per calendar month, per plan. **No defaults on purpose.**

    A default here would be a third copy of the pricing table: `PlanLimits()` would silently
    answer 3/100/400 even after `Settings` or `plan_limits` changed, and nothing would fail. The
    only constructor is `limits_from_settings`, which reads the compiled configuration.
    """

    free: int
    starter: int
    pro: int

    def for_plan(self, plan: PlanTier) -> int:
        if plan is PlanTier.STARTER:
            return self.starter
        if plan is PlanTier.PRO:
            return self.pro
        return self.free

    def on_model_for_plan(self, plan: PlanTier) -> int:
        """On-model renders included. 0 everywhere until E4/E8 price them — no invention."""
        return 0 if plan is PlanTier.FREE else 0


def limits_from_settings(settings: Settings | None = None) -> PlanLimits:
    """The compiled defaults. Runtime overrides live in the `plan_limits` table."""
    source = settings or get_settings()
    return PlanLimits(
        free=source.free_items_per_month,
        starter=source.starter_items_per_month,
        pro=source.pro_items_per_month,
    )


def plan_ceiling(repository: Repository, plan: PlanTier, *, limits: PlanLimits) -> int:
    """The ceiling actually enforced: DB row first, compiled `Settings` second.

    One function so the precedence is written once. A caller that reads `limits.for_plan(plan)`
    directly would ignore the table and quietly reintroduce the split-brain this replaces.
    """
    override = repository.plan_limit(plan)
    if override is not None:
        return override
    return limits.for_plan(plan)


def period_for(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The current billing period: the calendar month, UTC, half-open [start, end)."""
    moment = (now or utcnow()).astimezone(UTC)
    start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


class QuotaExceeded(Exception):
    """Raised inside the enqueue transaction. The router turns this into a 402."""

    def __init__(self, *, plan: PlanTier, limit: int, used: int) -> None:
        super().__init__(f"{plan.value} plan allows {limit} items per month (used {used}).")
        self.plan = plan
        self.limit = limit
        self.used = used


def usage_snapshot(
    repository: Repository,
    user_id: UUID,
    *,
    limits: PlanLimits | None = None,
    now: datetime | None = None,
) -> Usage:
    """`GET /v1/usage`. Always a 200 — a seller at their ceiling can still read the number."""
    resolved = limits or limits_from_settings()
    start, end = period_for(now)
    plan = repository.get_plan(user_id)
    included = plan_ceiling(repository, plan, limits=resolved)
    used = repository.quota_units_used(user_id, start=start, end=end)
    return Usage(
        plan=plan,
        period_start=start,
        period_end=end,
        items_included=included,
        items_used=used,
        items_remaining=max(included - used, 0),
        on_model_included=resolved.on_model_for_plan(plan),
        on_model_used=0,
    )


def spend_one(
    repository: Repository,
    user_id: UUID,
    *,
    job_id: UUID,
    item_id: UUID,
    limits: PlanLimits | None = None,
    now: datetime | None = None,
) -> int:
    """Spend exactly one item unit or raise `QuotaExceeded`.

    Caller must already be inside `repository.transaction()` together with the item/job
    insert, so either all three rows exist or none do.
    """
    resolved = limits or limits_from_settings()
    moment = now or utcnow()
    start, end = period_for(moment)
    plan = repository.get_plan(user_id)
    limit = plan_ceiling(repository, plan, limits=resolved)
    used = repository.quota_units_used(user_id, start=start, end=end)
    if used >= limit:
        # The free-limit hit is a metric, not just a rejection: the paywall's whole funnel
        # depends on knowing how many sellers reach it (docs/METRICS.md §3).
        if plan is PlanTier.FREE:
            repository.append_usage_event(
                UsageEventRecord(
                    user_id=user_id,
                    event=EVENT_FREE_TIER_ITEM_LIMIT,
                    units=1,
                    plan=plan,
                    metadata={"limit": str(limit), "used": str(used)},
                    created_at=moment,
                )
            )
        raise QuotaExceeded(plan=plan, limit=limit, used=used)
    repository.append_usage_event(
        UsageEventRecord(
            user_id=user_id,
            event=EVENT_ITEM_ENQUEUED,
            units=-1,
            job_id=job_id,
            item_id=item_id,
            plan=plan,
            created_at=moment,
        )
    )
    return used + 1


def refund_one(
    repository: Repository,
    user_id: UUID,
    *,
    job_id: UUID,
    now: datetime | None = None,
) -> bool:
    """Refund one unit for a terminal failure. Idempotent by `job_id`.

    Returns True when this call wrote the refund row, False when there was nothing to refund
    (already refunded, or the job never spent a unit). Never raises: a failure path must not
    fail while cleaning up.
    """
    if not repository.has_event_for_job(job_id, EVENT_ITEM_ENQUEUED):
        # Nothing was spent, so nothing is owed. Inventing a +1 here would be free quota.
        return False
    if repository.has_event_for_job(job_id, EVENT_JOB_REFUNDED):
        return False
    try:
        repository.append_usage_event(
            UsageEventRecord(
                user_id=user_id,
                event=EVENT_JOB_REFUNDED,
                units=1,
                job_id=job_id,
                created_at=now or utcnow(),
            )
        )
    except Exception:  # noqa: BLE001 - the unique index is the last line of defence
        # A concurrent refund already landed. Losing this race is the correct outcome.
        return False
    return True
