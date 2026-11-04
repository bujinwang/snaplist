"""`/v1/items` + `/v1/usage` behaviour: create, idempotency, validation, delete, quota.

These are the promises E5-T2 and E8-T3 make in docs/PLAN.md, tested against a real app object and
a real (in-memory) database — not against a mock of one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from apps.api.tests.conftest import MISSING, create_item

EPOCH_ANY_START = datetime(2000, 1, 1, tzinfo=UTC)
EPOCH_ANY_END = datetime(2100, 1, 1, tzinfo=UTC)


def test_create_returns_202_with_item_and_job(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    response = client.post("/v1/items", headers=headers_a, json={"filename": "tee.jpg"})
    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == {"item_id", "job_id", "upload_url"}
    # E5-T4: the URL is a signed capability for the key this item will own. It is asserted in
    # full (and exercised end to end) in apps/api/tests/test_storage.py; here the point is
    # only that create no longer hands back `null` on a process that has a storage backend.
    upload_url = body["upload_url"]
    assert isinstance(upload_url, str), "a configured LocalStorage must hand out a URL"
    assert f"/v1/uploads/items/{headers_a['x-snaplist-user-id']}/{body['item_id']}/" in upload_url
    assert "sig=" in upload_url and "exp=" in upload_url
    assert response.headers["x-snaplist-auth-mode"] == "local_dev"


def test_fresh_item_is_an_assetpack_with_five_keys_and_nulls(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    ids = create_item(client, headers_a)
    response = client.get(f"/v1/items/{ids['item_id']}", headers=headers_a)
    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(body) == [
        "attributes",
        "cutout_url",
        "item_id",
        "on_model_urls",
        "prettified_url",
    ]
    # "not ready yet" is null / [] inside a 200 — never a 404, never a missing key
    assert body["attributes"] is None
    assert body["prettified_url"] is None
    assert body["cutout_url"] is None
    assert body["on_model_urls"] == []


def test_job_starts_queued_and_is_pollable(client: TestClient, headers_a: dict[str, str]) -> None:
    ids = create_item(client, headers_a)
    response = client.get(f"/v1/jobs/{ids['job_id']}", headers=headers_a)
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["state"] == "queued"
    assert job["reason_code"] is None
    assert job["attempts"] == 0
    assert job["kind"] == "full"


def test_idempotency_key_replays_the_same_item_and_spends_once(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    key = {"idempotency-key": "upload-42-attempt-1"}
    first = client.post("/v1/items", headers={**headers_a, **key}, json={"filename": "a.jpg"})
    second = client.post("/v1/items", headers={**headers_a, **key}, json={"filename": "a.jpg"})
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["item_id"] == second.json()["item_id"]
    assert first.json()["job_id"] == second.json()["job_id"]
    usage = client.get("/v1/usage", headers=headers_a).json()
    assert usage["items_used"] == 1, "a retried upload must not spend two units"


def test_two_different_keys_create_two_items(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    one = client.post("/v1/items", headers={**headers_a, "idempotency-key": "key-one-aaa"}, json={})
    two = client.post("/v1/items", headers={**headers_a, "idempotency-key": "key-two-bbb"}, json={})
    assert one.json()["item_id"] != two.json()["item_id"]
    assert client.get("/v1/usage", headers=headers_a).json()["items_used"] == 2


def test_short_idempotency_key_is_a_400_with_details(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    response = client.post("/v1/items", headers={**headers_a, "idempotency-key": "abc"}, json={})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_request"


def test_patch_attributes_persists_a_validated_payload(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    ids = create_item(client, headers_a)
    payload = {
        "category": "top",
        "subcategory": "crewneck tee",
        "colors": ["black"],
        "material": ["cotton"],
        "size_label": "M",
        "condition": "good",
    }
    patched = client.patch(
        f"/v1/items/{ids['item_id']}/attributes", headers=headers_a, json=payload
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["subcategory"] == "crewneck tee"
    assert patched.json()["pattern"] == "unknown", "defaults are filled, not omitted"

    fetched = client.get(f"/v1/items/{ids['item_id']}", headers=headers_a).json()
    assert fetched["attributes"]["material"] == ["cotton"]


def test_patch_rejects_an_unknown_enum_with_the_standard_envelope(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    ids = create_item(client, headers_a)
    response = client.patch(
        f"/v1/items/{ids['item_id']}/attributes",
        headers=headers_a,
        json={"category": "hoodie", "colors": ["black"]},
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "invalid_request"
    assert "body.category" in (body["error"]["details"] or {})


def test_patch_rejects_an_unknown_field(client: TestClient, headers_a: dict[str, str]) -> None:
    ids = create_item(client, headers_a)
    response = client.patch(
        f"/v1/items/{ids['item_id']}/attributes",
        headers=headers_a,
        json={"category": "top", "colors": ["black"], "smell": "none"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_request"


def test_patch_rejects_empty_colors(client: TestClient, headers_a: dict[str, str]) -> None:
    ids = create_item(client, headers_a)
    response = client.patch(
        f"/v1/items/{ids['item_id']}/attributes",
        headers=headers_a,
        json={"category": "top", "colors": []},
    )
    assert response.status_code == 422, response.text


def test_delete_is_204_and_the_item_stops_resolving(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    ids = create_item(client, headers_a)
    assert client.delete(f"/v1/items/{ids['item_id']}", headers=headers_a).status_code == 204
    assert client.get(f"/v1/items/{ids['item_id']}", headers=headers_a).status_code == 404
    # deleting twice is a 404, not a 500
    assert client.delete(f"/v1/items/{ids['item_id']}", headers=headers_a).status_code == 404


def test_unknown_and_malformed_ids_are_404(client: TestClient, headers_a: dict[str, str]) -> None:
    assert client.get(f"/v1/items/{MISSING}", headers=headers_a).status_code == 404
    assert client.get("/v1/items/not-a-uuid", headers=headers_a).status_code == 404
    assert client.get(f"/v1/jobs/{MISSING}", headers=headers_a).status_code == 404


def test_free_plan_fourth_item_is_402_with_a_machine_readable_body(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    for index in range(3):
        create_item(client, headers_a, filename=f"{index}.jpg")
    response = client.post("/v1/items", headers=headers_a, json={"filename": "4th.jpg"})
    assert response.status_code == 402, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "quota_exceeded", "mobile keys its upsell sheet off this"
    assert body["error"]["details"] == {"plan": "free", "limit": "3", "used": "3"}
    assert body["error"]["message"] == "Free plan allows 3 items per month."


def test_usage_reports_the_current_period_and_remaining_items(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    create_item(client, headers_a)
    usage = client.get("/v1/usage", headers=headers_a).json()
    assert usage["plan"] == "free"
    assert usage["items_included"] == 3
    assert usage["items_used"] == 1
    assert usage["items_remaining"] == 2
    assert usage["period_start"].endswith("T00:00:00Z")
    assert usage["period_end"] > usage["period_start"]


def test_quota_is_per_user(
    client: TestClient, headers_a: dict[str, str], headers_b: dict[str, str]
) -> None:
    for index in range(3):
        create_item(client, headers_a, filename=f"{index}.jpg")
    # B is untouched by A's spending
    assert client.get("/v1/usage", headers=headers_b).json()["items_used"] == 0
    assert client.post("/v1/items", headers=headers_b, json={}).status_code == 202


def test_attributes_edit_is_recorded_in_the_ledger(
    client: TestClient, headers_a: dict[str, str], repository
) -> None:
    ids = create_item(client, headers_a)
    client.patch(
        f"/v1/items/{ids['item_id']}/attributes",
        headers={**headers_a, "x-snaplist-fields-touched": "5"},
        json={"category": "top", "colors": ["black"], "size_label": "M"},
    )
    rows = repository.count_events(
        UUID(headers_a["x-snaplist-user-id"]),
        "attributes_edited",
        start=EPOCH_ANY_START,
        end=EPOCH_ANY_END,
    )
    assert rows == 1, "an edit must leave one attributes_edited row, not zero and not two"


def test_provider_backed_routes_still_answer_501(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    ids = create_item(client, headers_a)
    for path in (
        f"/v1/items/{ids['item_id']}/rerender",
        f"/v1/items/{ids['item_id']}/on-model",
    ):
        response = client.post(path, headers=headers_a)
        assert response.status_code == 501, (path, response.text)
        assert response.json()["error"]["code"] == "not_implemented"


def test_binary_upload_is_refused_as_501_not_as_a_fake_202(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    """The bytes go to `upload_url`, not here. The 501 must say so, or the client sits on a
    multipart request the server will never accept."""
    response = client.post(
        "/v1/items",
        headers={**headers_a, "content-type": "multipart/form-data; boundary=xyz"},
        content=b"--xyz--",
    )
    assert response.status_code == 501, response.text
    message = response.json()["error"]["message"]
    assert "two-step" in message
    assert "upload_url" in message
    assert "/v1/uploads/" in message
