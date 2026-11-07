"""E8-T4 acceptance: sticky server-assigned `paywall_variant` and the Pro-only on-model gate.

* The variant is assigned by the server from the user id and never changes for that account
  (sticky), and a caller cannot choose it.
* `POST /v1/items/{id}/on-model` is Pro-only: a Free/Starter seller gets a 402 that is
  distinguishable from the item-quota 402 by `error.details.reason == "plan_required"`.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.repository import SqliteRepository
from apps.api.schemas import PlanTier
from apps.api.settings import Settings
from apps.api.tests.conftest import USER_A, USER_B


def test_paywall_variant_is_server_assigned_and_sticky(repository: SqliteRepository) -> None:
    first = repository.assign_paywall_variant(USER_A)
    assert first in ("a", "b", "c")
    # Re-assigning returns the same value: the assignment is sticky for the account's life.
    assert repository.assign_paywall_variant(USER_A) == first
    assert repository.get_paywall_variant(USER_A) == first
    # A different account gets its own stable variant.
    other = repository.assign_paywall_variant(USER_B)
    assert repository.get_paywall_variant(USER_B) == other


def _client_with_plan(plan: PlanTier) -> TestClient:
    settings = Settings(env="local", supabase_jwt_secret="")
    store = SqliteRepository(":memory:")
    store.set_plan(USER_A, plan)
    app = create_app(repository=store, settings=settings)
    return TestClient(app)


def test_on_model_requires_pro_plan() -> None:
    client = _client_with_plan(PlanTier.FREE)
    response = client.post(
        "/v1/items/00000000-0000-4000-8000-000000000000/on-model",
        headers={"x-snaplist-user-id": str(USER_A)},
    )
    assert response.status_code == 402, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == "plan_required"
    assert details["required_plan"] == "pro"
    assert details["current_plan"] == "free"


def test_pro_user_still_gets_honest_fashn_501() -> None:
    client = _client_with_plan(PlanTier.PRO)
    response = client.post(
        "/v1/items/00000000-0000-4000-8000-000000000000/on-model",
        headers={"x-snaplist-user-id": str(USER_A)},
    )
    # Plan gate passed, but FASHN is not implemented — the honest 501, not a fake queue entry.
    assert response.status_code == 501, response.text
