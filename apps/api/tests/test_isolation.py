"""Cross-user isolation.

The SQL-level guarantee is row-level security in `infra/migrations/0001_init.sql` (proven against a
real Postgres in `infra/migrations/verify/rls_isolation_check.sql`). This SQLite-backed build does
not have RLS, so the guarantee has to come from every query carrying `user_id` — which is exactly
why this test exists, and why it fails loudly if a future repository forgets a predicate.

A foreign id answers **404**, not 403: confirming that somebody else's item exists is itself a
leak, and the client has no use for the distinction.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.tests.conftest import create_item


def test_user_b_cannot_read_user_a_item(
    client: TestClient, headers_a: dict[str, str], headers_b: dict[str, str]
) -> None:
    ids = create_item(client, headers_a)
    response = client.get(f"/v1/items/{ids['item_id']}", headers=headers_b)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


def test_user_b_cannot_read_user_a_job(
    client: TestClient, headers_a: dict[str, str], headers_b: dict[str, str]
) -> None:
    ids = create_item(client, headers_a)
    assert client.get(f"/v1/jobs/{ids['job_id']}", headers=headers_b).status_code == 404


def test_user_b_cannot_patch_user_a_attributes(
    client: TestClient, headers_a: dict[str, str], headers_b: dict[str, str]
) -> None:
    ids = create_item(client, headers_a)
    response = client.patch(
        f"/v1/items/{ids['item_id']}/attributes",
        headers=headers_b,
        json={"category": "top", "colors": ["white"]},
    )
    assert response.status_code == 404

    # and the owner's row is untouched
    owned = client.get(f"/v1/items/{ids['item_id']}", headers=headers_a).json()
    assert owned["attributes"] is None


def test_user_b_cannot_delete_user_a_item(
    client: TestClient, headers_a: dict[str, str], headers_b: dict[str, str]
) -> None:
    ids = create_item(client, headers_a)
    assert client.delete(f"/v1/items/{ids['item_id']}", headers=headers_b).status_code == 404
    assert client.get(f"/v1/items/{ids['item_id']}", headers=headers_a).status_code == 200


def test_user_b_cannot_export_user_a_item(
    client: TestClient, headers_a: dict[str, str], headers_b: dict[str, str]
) -> None:
    ids = create_item(client, headers_a)
    response = client.post(
        "/v1/exports",
        headers=headers_b,
        json={"marketplace": "ebay", "item_ids": [ids["item_id"]]},
    )
    assert response.status_code == 404, response.text


def test_one_users_quota_does_not_leak_into_anothers(
    client: TestClient, headers_a: dict[str, str], headers_b: dict[str, str]
) -> None:
    for index in range(3):
        create_item(client, headers_a, filename=f"{index}.jpg")
    assert client.get("/v1/usage", headers=headers_a).json()["items_used"] == 3
    assert client.get("/v1/usage", headers=headers_b).json()["items_used"] == 0
    assert client.post("/v1/items", headers=headers_b, json={}).status_code == 202


def test_an_idempotency_key_is_scoped_to_its_owner(
    client: TestClient, headers_a: dict[str, str], headers_b: dict[str, str]
) -> None:
    key = {"idempotency-key": "shared-device-key"}
    from_a = client.post("/v1/items", headers={**headers_a, **key}, json={})
    from_b = client.post("/v1/items", headers={**headers_b, **key}, json={})
    assert from_a.status_code == 202 and from_b.status_code == 202
    assert from_a.json()["item_id"] != from_b.json()["item_id"], (
        "a reused key on a shared device must not hand B's request A's item"
    )
