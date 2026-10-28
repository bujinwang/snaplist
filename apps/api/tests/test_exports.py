"""`POST /v1/exports` against the real TypeScript mapper, through the node bridge.

This is the only test in the Python suite that shells out to another runtime. It is skipped with a
reason when `node` or the built `packages/shared/dist` is missing, because `make test` builds the
shared package first and skipping is honest — silently passing with an empty CSV would not be.

What is pinned here, and why each one exists:

* the **JSON envelope is the default** — a client must learn which columns are its job *before*
  the seller downloads a file (`requires_manual_columns`);
* **`Accept: text/csv`** still returns a real file, with the same information in a header as an
  alignment aid only;
* **all-refused is 422, partial is 200** — the two must never blur into each other;
* **`price_cents: 0` renders `"0.00"` and is not a manual column**, because a zero-price listing
  is legal and telling the seller to fill in a price they already filled destroys trust;
* **export refusals are not `Job.reason_code`** — one vocabulary, one meaning.
"""

from __future__ import annotations

import shutil
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from apps.api.exporters import MARKETPLACE_ENTRY
from apps.api.repository import SqliteRepository
from apps.api.schemas import ReasonCode
from apps.api.tests.conftest import create_item

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not MARKETPLACE_ENTRY.exists(),
    reason="node or packages/shared/dist missing — run `pnpm -w build` (make test does it first)",
)

ATTRIBUTES = {
    "category": "top",
    "subcategory": "crewneck tee",
    "colors": ["black"],
    "pattern": "solid",
    "material": ["cotton"],
    "fit": "regular",
    "size_label": "M",
    "condition": "good",
    "measurements": {"chest_cm": 52, "length_cm": 68.5, "source": "seller_provided"},
}

CSV_ACCEPT = {"accept": "text/csv"}


def _prepared_item(
    client: TestClient,
    headers: dict[str, str],
    repository: SqliteRepository,
    attributes: dict[str, object] | None = None,
) -> str:
    ids = create_item(client, headers)
    patched = client.patch(
        f"/v1/items/{ids['item_id']}/attributes",
        headers=headers,
        json=attributes if attributes is not None else ATTRIBUTES,
    )
    assert patched.status_code == 200, patched.text
    repository.set_asset(
        UUID(ids["item_id"]),
        user_id=UUID(headers["x-snaplist-user-id"]),
        kind="prettified",
        storage_path="https://example.test/items/1/prettified.png",
    )
    return ids["item_id"]


# ---------------------------------------------------------------------------------------
# Default representation: the JSON envelope
# ---------------------------------------------------------------------------------------
def test_default_response_is_a_json_envelope_with_manual_columns(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    item_id = _prepared_item(client, headers_a, repository)
    response = client.post(
        "/v1/exports", headers=headers_a, json={"marketplace": "ebay", "item_ids": [item_id]}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["vary"] == "Accept, Accept-Encoding"

    body = response.json()
    assert set(body) == {
        "csv",
        "requires_manual_columns",
        "exported",
        "refused",
        "warnings",
    }
    # No price was supplied → both eBay columns are the seller's job.
    assert body["requires_manual_columns"] == ["ebay_category_id", "ebay_start_price"]
    assert body["exported"] == [{"item_id": item_id, "row_index": 0}]
    assert body["refused"] == []
    assert body["warnings"], "the unverified-column warnings must survive to the client"
    assert body["csv"].startswith("*Action(SiteID=US")
    assert "crewneck tee" in body["csv"]


def test_empty_manual_columns_is_an_empty_array_never_omitted(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    """`[]` means "nothing to fill in". A missing key would mean "we did not tell you" — and a
    client that cannot tell those apart either nags the seller or stays silent wrongly."""
    item_id = _prepared_item(
        client, headers_a, repository, attributes={**ATTRIBUTES, "brand": "Nike"}
    )
    response = client.post(
        "/v1/exports",
        headers=headers_a,
        json={"marketplace": "poshmark", "item_ids": [item_id], "prices": {item_id: 2500}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "requires_manual_columns" in body
    assert body["requires_manual_columns"] == []


# ---------------------------------------------------------------------------------------
# Opt-in representation: the raw CSV
# ---------------------------------------------------------------------------------------
def test_text_csv_accept_returns_the_file_and_an_alignment_header(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    item_id = _prepared_item(client, headers_a, repository)
    response = client.post(
        "/v1/exports",
        headers={**headers_a, **CSV_ACCEPT},
        json={"marketplace": "ebay", "item_ids": [item_id]},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="snaplist-ebay.csv"' in response.headers["content-disposition"]
    assert response.headers["vary"] == "Accept, Accept-Encoding"
    # The same information as the JSON body, in a header, for curl and for humans only.
    assert (
        response.headers["x-snaplist-requires-manual-columns"]
        == "ebay_category_id,ebay_start_price"
    )

    lines = response.text.split("\r\n")
    assert lines[0].startswith("*Action(SiteID=US"), "the eBay action header must lead"
    assert "*Category" in lines[0]
    assert "Chest: 52 cm" in response.text
    # The brand column exists; its *value* must be empty because the item has none.
    assert "Nike" not in response.text and "Burberry" not in response.text


# ---------------------------------------------------------------------------------------
# prices: per item, and 0 is not null
# ---------------------------------------------------------------------------------------
def test_a_supplied_price_fills_startprice_and_drops_the_manual_column(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    item_id = _prepared_item(client, headers_a, repository)
    response = client.post(
        "/v1/exports",
        headers=headers_a,
        json={"marketplace": "ebay", "item_ids": [item_id], "prices": {item_id: 1900}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requires_manual_columns"] == ["ebay_category_id"], "the price is no longer manual"
    assert "19.00" in body["csv"]


def test_a_zero_price_renders_000_and_is_not_a_manual_column(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    """`0` is a real price (giveaway / bundle filler). Rendering it as empty would ask the seller
    to fill in a field they already filled — and silently turn a giveaway into a missing price."""
    item_id = _prepared_item(client, headers_a, repository)
    response = client.post(
        "/v1/exports",
        headers=headers_a,
        json={"marketplace": "ebay", "item_ids": [item_id], "prices": {item_id: 0}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "0.00" in body["csv"]
    assert "ebay_start_price" not in body["requires_manual_columns"]


def test_a_partially_priced_batch_still_reports_the_price_column(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    """The quantifier is "any row empty", not "all rows empty".

    Two of three items priced still leaves one row a seller must fill; an all-empty rule would
    hide that behind a mostly-filled column — the hardest gap to notice.
    """
    first = _prepared_item(client, headers_a, repository)
    second = _prepared_item(client, headers_a, repository)
    third = _prepared_item(client, headers_a, repository)
    response = client.post(
        "/v1/exports",
        headers=headers_a,
        json={
            "marketplace": "ebay",
            "item_ids": [first, second, third],
            "prices": {first: 1900, second: 2400},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["exported"]) == 3
    assert body["requires_manual_columns"] == ["ebay_category_id", "ebay_start_price"]


@pytest.mark.parametrize(
    ("prices", "expected_fragment"),
    [
        ({"11111111-1111-4111-8111-111111111111": 1900}, "not in `item_ids`"),
        ({"not-a-uuid": 1900}, "not an item uuid"),
    ],
)
def test_prices_naming_an_unknown_item_is_422(
    client: TestClient,
    headers_a: dict[str, str],
    repository: SqliteRepository,
    prices: dict[str, int],
    expected_fragment: str,
) -> None:
    item_id = _prepared_item(client, headers_a, repository)
    response = client.post(
        "/v1/exports",
        headers=headers_a,
        json={"marketplace": "ebay", "item_ids": [item_id], "prices": prices},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_request"
    assert expected_fragment in response.json()["error"]["message"]


@pytest.mark.parametrize("cents", [-1, 10_000_001, 19.5, "1900", True])
def test_malformed_prices_are_422(
    client: TestClient,
    headers_a: dict[str, str],
    repository: SqliteRepository,
    cents: object,
) -> None:
    """Silently ignoring these would let the seller believe a price was applied."""
    item_id = _prepared_item(client, headers_a, repository)
    response = client.post(
        "/v1/exports",
        headers=headers_a,
        json={"marketplace": "ebay", "item_ids": [item_id], "prices": {item_id: cents}},
    )
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------------------
# Refusals: all vs partial
# ---------------------------------------------------------------------------------------
def test_poshmark_refuses_a_missing_brand_and_says_which_field(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    item_id = _prepared_item(client, headers_a, repository)
    response = client.post(
        "/v1/exports", headers=headers_a, json={"marketplace": "poshmark", "item_ids": [item_id]}
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    assert body["error"]["details"]["reason"] == "insufficient_data"
    assert body["error"]["details"]["missing_fields"] == "brand"
    assert body["error"]["details"]["item_id"] == item_id


def test_a_partial_export_is_200_with_the_refused_rows_kept(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    """One exportable item plus one without a brand must not destroy the whole batch, and the
    refusal must not be hidden either."""
    good = _prepared_item(
        client, headers_a, repository, attributes={**ATTRIBUTES, "brand": "Nike"}
    )
    bad = _prepared_item(client, headers_a, repository)  # brand is null

    response = client.post(
        "/v1/exports",
        headers=headers_a,
        json={
            "marketplace": "poshmark",
            "item_ids": [good, bad],
            "prices": {good: 2500, bad: 2500},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_id"] for row in body["exported"]] == [good]
    assert body["exported"][0]["row_index"] == 0
    assert body["refused"] == [{"item_id": bad, "reason": "insufficient_data", "fields": ["brand"]}]
    assert "Nike" in body["csv"]


def test_vinted_always_reports_the_package_size_column(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    item_id = _prepared_item(
        client, headers_a, repository, attributes={**ATTRIBUTES, "brand": "Nike"}
    )
    response = client.post(
        "/v1/exports", headers=headers_a, json={"marketplace": "vinted", "item_ids": [item_id]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["requires_manual_columns"] == ["vinted_package_size"]


def test_a_two_item_export_of_unexportable_rows_is_422(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    first = _prepared_item(client, headers_a, repository)
    second = _prepared_item(client, headers_a, repository)
    response = client.post(
        "/v1/exports",
        headers=headers_a,
        json={"marketplace": "vinted", "item_ids": [first, second]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["refused_rows"] == "2"


# ---------------------------------------------------------------------------------------
# Request validation and ownership
# ---------------------------------------------------------------------------------------
def test_export_of_an_unknown_item_is_404(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    response = client.post(
        "/v1/exports",
        headers=headers_a,
        json={
            "marketplace": "ebay",
            "item_ids": ["00000000-0000-4000-8000-000000000000"],
        },
    )
    assert response.status_code == 404


def test_an_unknown_marketplace_is_422(client: TestClient, headers_a: dict[str, str]) -> None:
    response = client.post(
        "/v1/exports",
        headers=headers_a,
        json={"marketplace": "etsy", "item_ids": ["00000000-0000-4000-8000-000000000000"]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_an_empty_item_list_is_422(client: TestClient, headers_a: dict[str, str]) -> None:
    response = client.post(
        "/v1/exports", headers=headers_a, json={"marketplace": "ebay", "item_ids": []}
    )
    assert response.status_code == 422


def test_an_export_without_a_body_is_422(client: TestClient, headers_a: dict[str, str]) -> None:
    response = client.post("/v1/exports", headers=headers_a)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


# ---------------------------------------------------------------------------------------
# Two vocabularies that must never merge
# ---------------------------------------------------------------------------------------
def test_export_refusal_reasons_are_not_job_reason_codes() -> None:
    """`reason: "insufficient_data"` is a different domain from `Job.reason_code`.

    This guards the *action*, not a typo: somebody folding the export refusal into the frozen
    `ReasonCode` enum has to add `insufficient_data` to it first, and that is exactly what this
    fails on. The two enums describe different things — a job that failed to render, and a row
    that cannot be mapped — and one grep hitting both vocabularies makes every future reading of
    either one wrong.
    """
    assert "insufficient_data" not in {code.value for code in ReasonCode}


def test_prices_is_not_yet_part_of_the_frozen_export_request() -> None:
    """The router lifts `prices` out of the body *before* the frozen model validates the rest.

    That shim is correct today, but it becomes a silent bug the day somebody adds `prices` to
    `ExportRequest`: the pop already happened, so the new model field would never be seen and the
    two code paths would disagree about who owns the key. This test fails at that moment, which is
    the point — the fix is to delete the shim (and mirror the field in zod), not to delete this.
    """
    from apps.api.schemas import ExportRequest

    assert "prices" not in ExportRequest.model_fields, (
        "ExportRequest gained `prices`: remove the shim in routers/exports.py::_parse_body, add "
        "the zod mirror, and bump CONTRACT_VERSION per docs/CONTRACTS.md"
    )
