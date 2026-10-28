"""Quota arithmetic, the plan-ceiling resolution order, and the billing-period boundary.

The ceiling used to exist in three places: `Settings`, a `PlanLimits` default, and the
`plan_limits` table. Three copies of a number that must agree means changing one and forgetting
two leaves every test green. These tests exist so that cannot happen again:

* `Settings` is the compiled default (pinned to the numbers in BRIEF.md);
* `plan_limits` is the runtime override (pinned to the same numbers, so the seed cannot drift);
* the resolution order is pinned by behaviour: DB says 5 while Settings says 3 ⇒ the 6th create
  is the one that gets a 402.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from apps.api import usage
from apps.api.repository import Repository
from apps.api.schemas import JobKind, PlanTier
from apps.api.settings import Settings
from apps.api.tests.conftest import create_item

STARTER_USER = UUID("55555555-5555-4555-8555-555555555555")
FREE_USER = UUID("66666666-6666-4666-8666-666666666666")


def _spend(repository: Repository, user: UUID, plan_limit: usage.PlanLimits) -> None:
    item = repository.create_item(user_id=user)
    job = repository.create_job(item_id=item.id, user_id=user, kind=JobKind.FULL)
    usage.spend_one(repository, user, job_id=job.id, item_id=item.id, limits=plan_limit)


# ---------------------------------------------------------------------------------------
# One code-side source, one runtime override
# ---------------------------------------------------------------------------------------
def test_settings_plan_limits_are_the_numbers_in_the_brief() -> None:
    settings = Settings()
    assert (
        settings.free_items_per_month,
        settings.starter_items_per_month,
        settings.pro_items_per_month,
    ) == (3, 100, 400)


def test_plan_limits_cannot_be_constructed_without_values() -> None:
    """No defaults, on purpose.

    A default would be a third copy of the pricing table: `PlanLimits()` would answer 3/100/400
    even after Settings changed, and nothing would fail. Forgetting an argument must be loud.
    """
    with pytest.raises(TypeError):
        usage.PlanLimits()  # type: ignore[call-arg]


def test_plan_limits_come_from_settings_not_from_a_second_constant() -> None:
    custom = Settings(free_items_per_month=1, starter_items_per_month=7, pro_items_per_month=9)
    limits = usage.limits_from_settings(custom)
    assert (limits.free, limits.starter, limits.pro) == (1, 7, 9)


def test_plan_limits_seed_matches_settings(repository: Repository) -> None:
    """The DB seed and the compiled defaults must agree, or "which one is live" becomes a
    question nobody can answer without reading three files."""
    limits = usage.limits_from_settings()
    assert repository.plan_limit(PlanTier.FREE) == limits.free
    assert repository.plan_limit(PlanTier.STARTER) == limits.starter
    assert repository.plan_limit(PlanTier.PRO) == limits.pro


class _NoOverrides:
    """Two-line test double for the fallback branch only.

    The real table is seeded, so "row absent" cannot be reached without deleting data — and
    deleting seed rows to test a fallback would itself depend on the seed. `plan_ceiling` reads
    exactly one method from the repository, so supplying that one method is a smaller lie than
    `DELETE FROM plan_limits` in a test.
    """

    def plan_limit(self, plan: PlanTier) -> int | None:  # noqa: ARG002 - signature compatibility
        return None


def test_a_missing_row_falls_back_to_settings() -> None:
    """The fallback path: no `plan_limits` row means the compiled Settings value applies."""
    limits = usage.PlanLimits(free=3, starter=100, pro=400)
    assert usage.plan_ceiling(_NoOverrides(), PlanTier.FREE, limits=limits) == 3  # type: ignore[arg-type]
    assert usage.plan_ceiling(_NoOverrides(), PlanTier.PRO, limits=limits) == 400  # type: ignore[arg-type]


def test_the_database_ceiling_wins_over_settings(
    client: TestClient, headers_a: dict[str, str], repository: Repository
) -> None:
    """The whole point of the table: ops raises a ceiling, the running API obeys, no deploy.

    Settings says 3 for the free plan. The table is set to 5. The 5th create must succeed and the
    6th must be the 402 — if Settings won, the 4th would already fail.
    """
    repository.set_plan_limit(PlanTier.FREE, 5)
    for index in range(5):
        response = client.post("/v1/items", headers=headers_a, json={"filename": f"{index}.jpg"})
        assert response.status_code == 202, (
            f"create #{index + 1} should be allowed (limit is 5, not Settings' 3): {response.text}"
        )

    sixth = client.post("/v1/items", headers=headers_a, json={"filename": "6.jpg"})
    assert sixth.status_code == 402, sixth.text
    assert sixth.json()["error"]["details"]["limit"] == "5"


def test_the_usage_endpoint_reports_the_overridden_ceiling(
    client: TestClient, headers_a: dict[str, str], repository: Repository
) -> None:
    """The read path and the enforcement path must agree, or the UI promises what the API denies."""
    repository.set_plan_limit(PlanTier.FREE, 7)
    create_item(client, headers_a)
    body = client.get("/v1/usage", headers=headers_a).json()
    assert body["items_included"] == 7
    assert body["items_remaining"] == 6


# ---------------------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------------------
def test_period_is_the_calendar_month_in_utc() -> None:
    start, end = usage.period_for(datetime(2026, 10, 28, 15, 30, tzinfo=UTC))
    assert start == datetime(2026, 10, 1, tzinfo=UTC)
    assert end == datetime(2026, 11, 1, tzinfo=UTC)


def test_period_rolls_over_the_year_at_december() -> None:
    start, end = usage.period_for(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
    assert start == datetime(2026, 12, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


def test_period_is_half_open(repository: Repository) -> None:
    """A spend at exactly `end` belongs to the next period, not this one."""
    user = UUID("33333333-3333-4333-8333-333333333333")
    start, end = usage.period_for(datetime(2026, 10, 15, tzinfo=UTC))
    repository.append_usage_event(
        usage.UsageEventRecord(
            user_id=user,
            event=usage.EVENT_ITEM_ENQUEUED,
            units=-1,
            created_at=end,
        )
    )
    assert repository.quota_units_used(user, start=start, end=end) == 0


def test_a_stray_refund_cannot_produce_negative_usage(repository: Repository) -> None:
    """A negative 'used' would be free quota; the ledger read must clamp at zero."""
    user = UUID("44444444-4444-4444-8444-444444444444")
    start, end = usage.period_for(datetime(2026, 10, 15, tzinfo=UTC))
    repository.append_usage_event(
        usage.UsageEventRecord(
            user_id=user,
            event=usage.EVENT_JOB_REFUNDED,
            units=1,
            created_at=datetime(2026, 10, 15, tzinfo=UTC),
        )
    )
    assert repository.quota_units_used(user, start=start, end=end) == 0


# ---------------------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------------------
def test_starter_plan_allows_more_than_free(repository: Repository) -> None:
    limits = usage.limits_from_settings()
    repository.set_plan(STARTER_USER, PlanTier.STARTER)
    for _ in range(4):
        _spend(repository, STARTER_USER, limits)
    snapshot = usage.usage_snapshot(repository, STARTER_USER, limits=limits)
    assert snapshot.plan is PlanTier.STARTER
    assert snapshot.items_used == 4
    assert snapshot.items_included == 100
    assert snapshot.items_remaining == 96


def test_free_plan_raises_at_the_ceiling(repository: Repository) -> None:
    limits = usage.limits_from_settings()
    for _ in range(3):
        _spend(repository, FREE_USER, limits)

    item = repository.create_item(user_id=FREE_USER)
    job = repository.create_job(item_id=item.id, user_id=FREE_USER, kind=JobKind.FULL)
    with pytest.raises(usage.QuotaExceeded) as excinfo:
        usage.spend_one(repository, FREE_USER, job_id=job.id, item_id=item.id, limits=limits)
    assert excinfo.value.plan is PlanTier.FREE
    assert excinfo.value.limit == 3
    assert excinfo.value.used == 3


def test_hitting_the_free_ceiling_is_recorded_as_a_metric(repository: Repository) -> None:
    """The paywall funnel needs to know how many sellers reached the wall, not just that one did."""
    limits = usage.limits_from_settings()
    for _ in range(3):
        _spend(repository, FREE_USER, limits)

    item = repository.create_item(user_id=FREE_USER)
    job = repository.create_job(item_id=item.id, user_id=FREE_USER, kind=JobKind.FULL)
    with pytest.raises(usage.QuotaExceeded):
        usage.spend_one(repository, FREE_USER, job_id=job.id, item_id=item.id, limits=limits)

    start, end = usage.period_for()
    assert (
        repository.count_events(
            FREE_USER, usage.EVENT_FREE_TIER_ITEM_LIMIT, start=start, end=end
        )
        == 1
    )
