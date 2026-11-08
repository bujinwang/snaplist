"""`POST /v1/events` — the frozen vocabulary, the closed payload shape, and idempotency.

What these tests are actually defending
---------------------------------------
1. **Seven events, no eighth.** `test_python_models_mirror_the_frozen_typescript_vocabulary`
   parses `packages/shared/src/analytics.ts` and compares it, event by event and field by
   field, against the pydantic models in `apps/api/routers/events.py`. No list is copied by
   hand into this file: a test that compares two transcriptions of the same list only proves
   the transcriptions agree, which is exactly the failure mode it was supposed to catch.
2. **No free text anywhere.** `test_events_table_has_no_free_text_column` asserts the column
   whitelist of `public.events` and that every `text` column is constrained. It goes red the
   day somebody adds `metadata jsonb` "for context", which is the only realistic way PII ends
   up in an events table.
3. **Idempotency is per client-generated id**, and a *reused* id with different content is a
   409 rather than a silent overwrite (`test_replay_*`). The row count is read from a second
   SQLite connection to the same file, so "one row" is a fact about the database and not about
   a repository method choosing to answer 1.
4. **A caller cannot file events against another account** — `subject` is bound to the
   authenticated uuid (`test_subject_must_name_the_caller`), and so is `item_id`
   (`test_a_foreign_item_id_is_404`). The second one is the E5-T6b addition: `item_id` is the
   join key every per-item report uses, so a forged one does more damage than a forged count.
5. **`item_id` is graded per event, not globally optional** (ADR-008). Five events require it,
   `paywall_impression` may omit it, `subscription_started` must not carry it. Each of those
   three grades is a test, and the last one is tested twice — through the route (422) and
   straight into `append_analytics_event()` (IntegrityError), because a rule that only the
   route enforces is a rule that a background job will eventually break.
6. **A hard delete never rewrites an event row** (ADR-008 Corrigendum, 2026-11-05). The first
   version of 0003 put `ON DELETE SET NULL` on `events.item_id`; that is an implicit UPDATE and
   collides with the forward CHECK. QA measured it: the five item events refused the DELETE and
   `paywall_impression` was silently rewritten. Every test that proved the FK existed was a
   **text** assertion, so the suite was green. The tests below do a real
   `delete from items` and compare the whole `EventRecord` before and after, introspect the
   live table with `pragma foreign_key_list`, and assert an explicit
   `update events set item_id = null` still fails. Text assertions are now only a cheap guard
   against re-adding the string.

Everything runs against the SQLite mirror of the table (`apps/api/repository.py`). The Postgres
migrations are checked at the text level only; there is no Postgres in this environment and the
honesty notes in `0002_events.sql` / `0003_events_item_id.sql` say so.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api import repository as repository_module
from apps.api.main import create_app
from apps.api.repository import EventRecord, Repository, SqliteRepository
from apps.api.routers import events as ev
from apps.api.settings import Settings
from apps.api.tests.conftest import USER_A, USER_B, create_item

# ---------------------------------------------------------------------------------------
# Backend-agnostic helpers. The suite runs against both SQLite and Postgres when
# SNAPLIST_TEST_PG is set, and the cross-backend invariants below (append-only events, the
# item_id grade CHECKs) must be proven on *both* engines rather than skipped on one. The two
# drivers differ in (a) their positional placeholder (`?` vs `%s`) and (b) the exception class a
# CHECK violation raises; these helpers paper over exactly those two differences.
# ---------------------------------------------------------------------------------------
try:
    import psycopg  # only present when the optional `pg` dependency group is installed

    from apps.api.repository import PostgresRepository as _PostgresRepository

    _HAS_PG = True
except Exception:  # pragma: no cover - lean checkout without psycopg
    psycopg = None
    _PostgresRepository = None
    _HAS_PG = False


#: Whether the suite is configured to also run against Postgres (mirrors conftest's switch).
IS_POSTGRES_CONFIGURED = bool(os.environ.get("SNAPLIST_TEST_PG"))


def _is_postgres(repository: object) -> bool:
    return _HAS_PG and _PostgresRepository is not None and isinstance(
        repository, _PostgresRepository
    )


def _placeholder(repository: object) -> str:
    """`?` for SQLite, `%s` for Postgres — the two drivers' positional markers."""
    return "%s" if _is_postgres(repository) else "?"


def _integrity_error() -> type[Exception] | tuple[type[Exception], ...]:
    """Either driver's integrity error, so a CHECK violation is caught on both backends."""
    if _HAS_PG and psycopg is not None:
        return (sqlite3.IntegrityError, psycopg.IntegrityError)
    return sqlite3.IntegrityError

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "infra" / "migrations" / "0002_events.sql"
MIGRATION_ITEM_ID = ROOT / "infra" / "migrations" / "0003_events_item_id.sql"
ANALYTICS_TS = ROOT / "packages" / "shared" / "src" / "analytics.ts"

ENDPOINT = "/v1/events"
ENVELOPE_KEYS = {"code", "message", "retry_after_seconds", "details"}

#: The five events that cannot be reported without an item (ADR-008). Written out rather than
#: derived from the models: this tuple *is* the assertion, and a list computed from the code it
#: is meant to police would agree with any change to that code.
ITEM_EVENTS = (
    "upload_started",
    "prettify_approved",
    "attributes_edited",
    "export_clicked",
    "export_delivered",
)

#: A well-formed uuid that is **not** in the database.
#:
#: Shape tests (`422`) never reach the ownership check — pydantic validates before any lookup —
#: so a placeholder is enough for them and saves every negative test from creating an item it
#: does not care about. Any test that expects a `202` must substitute an item that exists; a
#: forgotten substitution fails loudly with a 404 rather than passing quietly.
PLACEHOLDER_ITEM = str(uuid4())

#: Fields the API adds on top of the shared `Envelope`. **None**: `event_id` moved into the
#: contract itself in `EVENTS_VERSION = 1.3.0` (ADR-007), so the Python and TypeScript envelopes
#: are now supposed to be identical rather than "identical plus one". Left as an empty set so the
#: parity test keeps its shape and any future additive field has to be declared here on purpose.
API_ADDITIVE_FIELDS: frozenset[str] = frozenset()

#: The seven, derived from the union itself rather than retyped — see `_python_vocabulary`.
UNION_MEMBERS = get_args(get_args(ev.AnalyticsEvent)[0])


def _python_vocabulary() -> dict[str, type]:
    """`{"upload_started": UploadStarted, ...}`, read off the models' own `Literal` tags."""
    vocabulary: dict[str, type] = {}
    for model in UNION_MEMBERS:
        tag = get_args(model.model_fields["event"].annotation)[0]
        vocabulary[tag] = model
    return vocabulary


PY_EVENTS = _python_vocabulary()


# ---------------------------------------------------------------------------------------
# Bodies. One minimal legal payload per event, built from the frozen shapes.
# ---------------------------------------------------------------------------------------
def envelope(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "event_id": str(uuid4()),
        "client_ts": "2026-05-01T10:00:00Z",
        "session_id": "sess-1a2b3c",
        "subject": f"usr_{USER_A}",
        "app_version": "1.2.0",
        "platform": "ios",
    }
    body.update(overrides)
    return body


def bodies_by_event() -> dict[str, Callable[[], dict[str, object]]]:
    """Builders, not dicts: every call gets a fresh `event_id`, so two tests cannot collide.

    The five per-item events carry :data:`PLACEHOLDER_ITEM`; `paywall_impression` omits the
    field and `subscription_started` must omit it, so the builders also encode the grade.
    """
    return {
        "upload_started": lambda: envelope(
            event="upload_started",
            item_id=PLACEHOLDER_ITEM,
            source="camera",
            capture_mode="single",
        ),
        "prettify_approved": lambda: envelope(
            event="prettify_approved",
            item_id=PLACEHOLDER_ITEM,
            decision="approve",
            job_latency_ms=1200,
            prettify_latency_ms=8000,
            attrs_latency_ms=900,
        ),
        "attributes_edited": lambda: envelope(
            event="attributes_edited",
            item_id=PLACEHOLDER_ITEM,
            edited_field_count=2,
            fields_touched=5,
            field_names=["colors", "size_label"],
        ),
        "export_clicked": lambda: envelope(
            event="export_clicked",
            item_id=PLACEHOLDER_ITEM,
            marketplace="ebay",
            row_count=3,
            export_latency_ms=1200,
        ),
        "subscription_started": lambda: envelope(
            event="subscription_started",
            plan="pro",
            paywall_variant="c",
            amount_cents=1900,
            currency="USD",
        ),
        "paywall_impression": lambda: envelope(
            event="paywall_impression",
            paywall_variant="a",
            trigger="upload_4th",
            plan_offered="starter",
        ),
        "export_delivered": lambda: envelope(
            event="export_delivered",
            item_id=PLACEHOLDER_ITEM,
            marketplace="vinted",
            row_count=3,
            bytes=2048,
            delivery_latency_ms=400,
        ),
    }


BUILDERS = bodies_by_event()


def new_item(client: TestClient, headers: dict[str, str], **body: object) -> str:
    """Create a real item owned by `headers` and return its id.

    Real, not a random uuid: `POST /v1/events` answers 404 for an item that does not exist or
    is not the caller's, so a happy-path event needs a row to point at. Kept to one item per
    test — the free plan allows three a month and a helper that quietly spent four would turn a
    green suite into a 402 (which is how this was noticed).
    """
    return create_item(client, headers, **body)["item_id"]


def body_for(event_name: str, item_id: str) -> dict[str, object]:
    """`BUILDERS[event_name]()` with `item_id` pointed at a real item, where one belongs.

    Guarded by `"item_id" in body` rather than by the event name: the builders carry the
    per-event grade, so `subscription_started` and `paywall_impression` are left alone — adding
    an item to the first is a 422 and to the second would hide the "optional" case this suite
    is supposed to cover both ways.
    """
    body = BUILDERS[event_name]()
    if "item_id" in body:
        body["item_id"] = item_id
    return body


def post(client: TestClient, headers: dict[str, str], body: dict[str, object]):
    return client.post(ENDPOINT, headers=headers, json=body)


def stored_rows(database: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        return list(connection.execute("select * from events").fetchall())


# ---------------------------------------------------------------------------------------
# Happy paths — one per frozen event
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("event_name", sorted(BUILDERS))
def test_each_of_the_seven_events_is_accepted(
    client: TestClient, headers_a: dict[str, str], event_name: str
) -> None:
    # One item, created only for the five events that require one — see `new_item`.
    body = (
        body_for(event_name, new_item(client, headers_a))
        if event_name in ITEM_EVENTS
        else BUILDERS[event_name]()
    )
    response = post(client, headers_a, body)
    assert response.status_code == 202, response.text
    assert response.json() == {"accepted": True, "duplicate": False}


def test_the_union_has_exactly_seven_members() -> None:
    assert len(UNION_MEMBERS) == 7
    assert set(PY_EVENTS) == set(BUILDERS)


def test_a_stored_row_carries_only_its_own_payload_columns(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    """The reverse half of the payload matrix: `export_clicked` may not carry a plan.

    `_to_record` passes `None` for every column the event does not own, and the CHECK
    constraints in `0002_events.sql` refuse the row if it ever stops doing that. This asserts
    the same thing one layer up, so a bug surfaces as a readable test failure rather than as
    an `IntegrityError` from the database.
    """
    body = body_for("export_clicked", new_item(client, headers_a))
    assert post(client, headers_a, body).status_code == 202
    record = repository.get_analytics_event(UUID(str(body["event_id"])))
    assert record is not None
    assert record.marketplace == "ebay"
    assert record.row_count == 3
    assert record.export_latency_ms == 1200
    # the item is carried, and it is the one the caller posted against
    assert record.item_id == UUID(str(body["item_id"]))
    # nothing from another event leaked in
    assert record.plan is None
    assert record.paywall_variant is None
    assert record.currency is None
    assert record.bytes is None
    assert record.field_names is None
    assert record.decision is None


# ---------------------------------------------------------------------------------------
# The vocabulary and the shape are closed
# ---------------------------------------------------------------------------------------
def test_unknown_event_name_is_422(client: TestClient, headers_a: dict[str, str]) -> None:
    """`item_created` is E5-T6's own named negative: a plausible name nobody froze."""
    response = post(client, headers_a, envelope(event="item_created"))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_request"
    details = json.dumps(response.json()["error"]["details"]).lower()
    # the discriminated union names the seven tags it does accept, in the detail
    assert "input tag" in details and "upload_started" in details


def test_unknown_envelope_key_is_422(client: TestClient, headers_a: dict[str, str]) -> None:
    """The whole point of `.strict()` / `extra="forbid"`: `email` is refused, not stripped."""
    body = BUILDERS["upload_started"]()
    body["email"] = "seller@example.com"
    response = post(client, headers_a, body)
    assert response.status_code == 422, response.text
    assert "email" in json.dumps(response.json()["error"]["details"])


def test_unknown_payload_key_is_422(client: TestClient, headers_a: dict[str, str]) -> None:
    body = BUILDERS["upload_started"]()
    body["photo_url"] = "https://cdn.example.com/photo.jpg"
    response = post(client, headers_a, body)
    assert response.status_code == 422, response.text


def test_field_names_carrying_a_value_is_422(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    """`color:red` is a value glued onto a name — docs/CONTRACTS.md §7's exact example."""
    body = BUILDERS["attributes_edited"]()
    body["field_names"] = ["color:red"]
    response = post(client, headers_a, body)
    assert response.status_code == 422, response.text
    assert "field_names" in json.dumps(response.json()["error"]["details"])


def test_field_names_that_are_not_snake_case_are_422(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    body = BUILDERS["attributes_edited"]()
    body["field_names"] = ["Size Label"]
    assert post(client, headers_a, body).status_code == 422


def test_field_names_over_twenty_is_422(client: TestClient, headers_a: dict[str, str]) -> None:
    # The number is written twice on purpose. Deriving the payload from `ev.MAX_FIELD_NAMES`
    # makes this test assert "the limit is the limit": QA raised the constant to 21 and then to
    # 1 GiB and the whole file stayed green. A literal is the only version that can fail.
    assert ev.MAX_FIELD_NAMES == 20, "the DoD says twenty names; update the test and the docs"
    body = BUILDERS["attributes_edited"]()
    body["field_names"] = [f"field_{index}" for index in range(21)]
    response = post(client, headers_a, body)
    assert response.status_code == 422, response.text


def test_a_field_name_longer_than_the_cap_is_422(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    """The alphabet alone left a 7 KB lowercase text channel: `["a"*4000]` was accepted."""
    transport_holdout = BUILDERS["attributes_edited"]()
    transport_holdout["field_names"] = ["a" * 4000]
    assert post(client, headers_a, transport_holdout).status_code == 422

    at_cap = body_for("attributes_edited", new_item(client, headers_a))
    at_cap["field_names"] = ["a" * 64]
    assert post(client, headers_a, at_cap).status_code == 202


def test_values_that_used_to_be_503_are_422(client: TestClient, headers_a: dict[str, str]) -> None:
    """Client mistakes must not surface as "the server is broken".

    Each of these overflowed the Postgres/SQLite integer range (or, for the NUL currency,
    defeated SQLite's `length()`), and the driver exception escaped as a 503 `internal`.
    """
    over_range = BUILDERS["export_delivered"]()
    over_range["row_count"] = 10**19
    assert post(client, headers_a, over_range).status_code == 422

    overflow_bytes = BUILDERS["export_delivered"]()
    overflow_bytes["bytes"] = 2**63
    assert post(client, headers_a, overflow_bytes).status_code == 422

    huge_cents = BUILDERS["subscription_started"]()
    huge_cents["amount_cents"] = 10**30
    assert post(client, headers_a, huge_cents).status_code == 422

    huge_latency = BUILDERS["prettify_approved"]()
    huge_latency["job_latency_ms"] = 10**20
    assert post(client, headers_a, huge_latency).status_code == 422

    nul_currency = BUILDERS["subscription_started"]()
    nul_currency["currency"] = "\u0000\u0000\u0000"
    assert post(client, headers_a, nul_currency).status_code == 422


def test_field_names_is_optional_and_defaults_to_empty(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    """`attributes_edited` still fires when the seller opened the sheet and touched nothing."""
    body = body_for("attributes_edited", new_item(client, headers_a))
    del body["field_names"]
    assert post(client, headers_a, body).status_code == 202
    record = repository.get_analytics_event(UUID(str(body["event_id"])))
    assert record is not None
    assert record.fields_touched == 5
    assert record.field_names == ()


@pytest.mark.parametrize(
    ("event_name", "missing"),
    [
        ("upload_started", "source"),
        ("prettify_approved", "attrs_latency_ms"),
        ("attributes_edited", "fields_touched"),
        ("export_clicked", "row_count"),
        ("subscription_started", "currency"),
        ("paywall_impression", "trigger"),
        ("export_delivered", "bytes"),
    ],
)
def test_a_required_payload_field_cannot_be_omitted(
    client: TestClient, headers_a: dict[str, str], event_name: str, missing: str
) -> None:
    body = BUILDERS[event_name]()
    del body[missing]
    response = post(client, headers_a, body)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    ("event_name", "smuggled"),
    [
        # a payload field that belongs to another event, on the wrong event
        ("upload_started", {"marketplace": "ebay"}),
        ("export_clicked", {"plan": "pro"}),
        ("paywall_impression", {"fields_touched": 3}),
        ("subscription_started", {"bytes": 10}),
        ("export_delivered", {"export_latency_ms": 10}),
    ],
)
def test_a_payload_field_on_the_wrong_event_is_422(
    client: TestClient,
    headers_a: dict[str, str],
    event_name: str,
    smuggled: dict[str, object],
) -> None:
    body = BUILDERS[event_name]()
    body.update(smuggled)
    response = post(client, headers_a, body)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    ("event_name", "field", "value"),
    [
        ("prettify_approved", "job_latency_ms", -1),
        ("attributes_edited", "edited_field_count", -2),
        ("export_clicked", "row_count", 0),
        ("export_delivered", "bytes", -5),
        ("subscription_started", "amount_cents", -100),
    ],
)
def test_a_negative_or_zero_counter_is_422(
    client: TestClient,
    headers_a: dict[str, str],
    event_name: str,
    field: str,
    value: int,
) -> None:
    body = BUILDERS[event_name]()
    body[field] = value
    assert post(client, headers_a, body).status_code == 422


def test_an_unknown_enum_literal_is_422(client: TestClient, headers_a: dict[str, str]) -> None:
    body = BUILDERS["paywall_impression"]()
    body["trigger"] = "because_we_felt_like_it"
    assert post(client, headers_a, body).status_code == 422


def test_currency_is_three_characters_not_an_iso4217_pattern(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    """The frozen schema says `z.string().length(3)`, so `usd` is legal.

    Pinning the *weaker* rule here is deliberate: enforcing `^[A-Z]{3}$` in python or in the
    database would reject a payload the shared package validated as legal, i.e. the API would
    500/422 on something the client was told was fine. The consequence — a three-character
    text column — is recorded in docs/HANDOFF-engineer.md rather than papered over.
    """
    body = BUILDERS["subscription_started"]()
    body["currency"] = "usd"
    assert post(client, headers_a, body).status_code == 202


def test_client_ts_must_be_absolute(client: TestClient, headers_a: dict[str, str]) -> None:
    body = BUILDERS["upload_started"]()
    body["client_ts"] = "2026-05-01T10:00:00"  # no offset: zod's .datetime() refuses this too
    response = post(client, headers_a, body)
    assert response.status_code == 422, response.text
    assert "client_ts" in json.dumps(response.json()["error"]["details"])


# ---------------------------------------------------------------------------------------
# `subject` is bound to the caller
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("prefix", ["anon", "usr"])
def test_subject_may_name_the_caller_with_either_prefix(
    client: TestClient, headers_a: dict[str, str], prefix: str
) -> None:
    body = body_for("upload_started", new_item(client, headers_a))
    body["subject"] = f"{prefix}_{USER_A}"
    assert post(client, headers_a, body).status_code == 202


def test_subject_must_name_the_caller(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    """Otherwise `subject` is a claim, and the funnel is poisonable by one curl."""
    body = BUILDERS["upload_started"]()
    body["subject"] = f"usr_{USER_B}"
    response = post(client, headers_a, body)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"
    assert response.json()["error"]["details"]["reason"] == "subject_not_bound_to_caller"


def test_an_email_cannot_be_used_as_a_subject(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    body = BUILDERS["upload_started"]()
    body["subject"] = "seller@example.com"
    assert post(client, headers_a, body).status_code == 422


def test_missing_identity_is_401(client: TestClient) -> None:
    response = client.post(ENDPOINT, json=BUILDERS["upload_started"]())
    assert response.status_code == 401
    assert set(response.json()["error"]) <= ENVELOPE_KEYS


# ---------------------------------------------------------------------------------------
# `item_id` — graded per event (ADR-008), and bound to the caller
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("event_name", ITEM_EVENTS)
def test_an_item_event_without_an_item_id_is_422(
    client: TestClient, headers_a: dict[str, str], event_name: str
) -> None:
    """Without the id, `tool_time_ms` per item and activation-by-item stop being computable."""
    body = BUILDERS[event_name]()
    del body["item_id"]
    response = post(client, headers_a, body)
    assert response.status_code == 422, response.text
    assert "item_id" in json.dumps(response.json()["error"]["details"])


@pytest.mark.parametrize("event_name", ITEM_EVENTS)
def test_an_item_event_with_the_callers_item_is_202(
    client: TestClient, headers_a: dict[str, str], event_name: str
) -> None:
    item_id = new_item(client, headers_a)
    response = post(client, headers_a, body_for(event_name, item_id))
    assert response.status_code == 202, response.text


@pytest.mark.parametrize("event_name", ITEM_EVENTS)
def test_a_foreign_item_id_is_404(
    client: TestClient,
    headers_a: dict[str, str],
    headers_b: dict[str, str],
    repository: SqliteRepository,
    event_name: str,
) -> None:
    """B's item, A's event. 404 not 403: a 403 would confirm B's item exists.

    Two real accounts and a real item on each side — `test_isolation.py`'s shape — because the
    interesting failure is not "unknown uuid" but "uuid that exists and is not yours".
    """
    foreign_item = new_item(client, headers_b)
    assert new_item(client, headers_a)  # A owns an item too, so this is not a "no items" case

    body = body_for(event_name, foreign_item)
    response = post(client, headers_a, body)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["details"]["reason"] == "item_id_not_owned_by_caller"
    # and nothing was written: the refusal happens before the insert, not after it
    assert repository.get_analytics_event(UUID(str(body["event_id"]))) is None


def test_an_item_id_that_does_not_exist_is_404(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    response = post(client, headers_a, body_for("upload_started", str(uuid4())))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


def test_a_soft_deleted_item_still_owns_the_events_it_emitted(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    """Ownership is "was this ever yours", not "is it still live".

    Events are append-only history. If `DELETE /v1/items/{id}` made later events 404, a client
    retrying an offline-queued event after the seller tidied up would be refused for a step
    that was legal when it happened — and the retry would be the client's *only* pending item,
    so it would retry forever. Hence `include_deleted=True` in `bind_item`.
    """
    item_id = new_item(client, headers_a)
    assert client.delete(f"/v1/items/{item_id}", headers=headers_a).status_code in (200, 204)
    response = post(client, headers_a, body_for("attributes_edited", item_id))
    assert response.status_code == 202, response.text


def test_subscription_started_must_not_carry_an_item_id(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    """Stripe's webhook has no item. A plausible id here would attribute revenue to a garment."""
    body = dict(BUILDERS["subscription_started"](), item_id=new_item(client, headers_a))
    response = post(client, headers_a, body)
    assert response.status_code == 422, response.text
    assert "item_id" in json.dumps(response.json()["error"]["details"])


def test_paywall_impression_takes_an_item_id_or_not(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    """The one event where the field is optional, and both answers have to work.

    `trigger=on_model` / `export_limit` always carry an item; a paywall opened from the settings
    screen carries none. A test that pinned only one of the two would let the other regress.
    """
    without = BUILDERS["paywall_impression"]()
    assert "item_id" not in without
    assert post(client, headers_a, without).status_code == 202

    with_item = dict(
        BUILDERS["paywall_impression"](), item_id=new_item(client, headers_a)
    )
    assert post(client, headers_a, with_item).status_code == 202


def test_item_id_is_stored_and_read_back(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    """Round-trip: the column exists, it is written, and `EventRecord` carries it."""
    from dataclasses import fields

    assert "item_id" in {field.name for field in fields(EventRecord)}, (
        "EventRecord lost `item_id` — `get_analytics_event` would then have nothing to read"
    )

    item_id = new_item(client, headers_a)
    body = body_for("export_delivered", item_id)
    assert post(client, headers_a, body).status_code == 202
    record = repository.get_analytics_event(UUID(str(body["event_id"])))
    assert record is not None
    assert record.item_id == UUID(item_id)


def test_the_sqlite_mirror_refuses_a_subscription_with_an_item(
    repository: SqliteRepository,
) -> None:
    """Bypassing the route must not bypass the rule.

    A background job or a future internal writer does not go through `parse_event`, so the
    reverse CHECK has to hold on its own. The item is **real** even though there is no FK on
    `events.item_id` any more: it keeps this fixture honest about what a legal row looks like,
    and it means a future reader cannot "fix" this test by adding an existence constraint back.
    Only the CHECK may be the reason this raises — assert the message names one.
    """
    item = repository.create_item(user_id=USER_A)
    record = EventRecord(
        event_id=uuid4(),
        event="subscription_started",
        client_ts=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        received_at=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        session_id="sess-1a2b3c",
        subject=f"usr_{USER_A}",
        app_version="1.2.0",
        platform="ios",
        payload_sha256="0" * 64,
        plan="pro",
        paywall_variant="c",
        amount_cents=1900,
        currency="USD",
        item_id=item.id,
    )
    with pytest.raises(_integrity_error()) as excinfo:
        repository.append_analytics_event(record)
    assert "CHECK" in str(excinfo.value).upper()


def test_the_sqlite_mirror_refuses_an_item_event_without_an_item(
    repository: SqliteRepository,
) -> None:
    """The forward half of the same pair: the five require it, not just the route."""
    record = EventRecord(
        event_id=uuid4(),
        event="export_delivered",
        client_ts=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        received_at=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        session_id="sess-1a2b3c",
        subject=f"usr_{USER_A}",
        app_version="1.2.0",
        platform="ios",
        payload_sha256="0" * 64,
        marketplace="ebay",
        row_count=1,
        bytes=10,
        delivery_latency_ms=5,
        item_id=None,
    )
    with pytest.raises(_integrity_error()) as excinfo:
        repository.append_analytics_event(record)
    assert "CHECK" in str(excinfo.value).upper()


# ---------------------------------------------------------------------------------------
# A hard delete must never rewrite an event row (ADR-008 Corrigendum, 2026-11-05)
#
# The first version of 0003 put `ON DELETE SET NULL` on `events.item_id`. That is an implicit
# UPDATE, and a CHECK is re-evaluated on UPDATE: the five item events (required NOT NULL) made
# the DELETE fail, while `paywall_impression` (allowed NULL) was silently rewritten. The old
# test asserted only the SQL *text*, so 569 green tests proved nothing. Everything below hits
# a real table.
# ---------------------------------------------------------------------------------------
def hard_delete_item(repository: Repository, item_id: str) -> None:
    """A real `delete from items`, not the API's soft delete.

    This is the admin / erasure path the withdrawn FK was supposed to serve. It has to be a raw
    statement: `DELETE /v1/items/{id}` only sets `deleted_at`, which cannot exercise a delete
    action at all.
    """
    repository._execute(
        f"delete from items where id = {_placeholder(repository)}", (item_id,)
    )


def minimal_event_record(**overrides: object) -> EventRecord:
    """One legal `upload_started` row, built without the route or pydantic.

    `EventRecord` is a storage DTO with no runtime type enforcement, which is exactly what lets
    these tests put a bad-shaped `item_id` straight into the table — the same thing a buggy
    background writer would do.
    """
    fields: dict[str, object] = {
        "event_id": uuid4(),
        "event": "upload_started",
        "client_ts": datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        "received_at": datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        "session_id": "sess-1a2b3c",
        "subject": f"usr_{USER_A}",
        "app_version": "1.4.0",
        "platform": "ios",
        "payload_sha256": "0" * 64,
        "source": "camera",
        "capture_mode": "single",
        "item_id": uuid4(),
    }
    fields.update(overrides)
    return EventRecord(**fields)


@pytest.mark.parametrize("event_name", sorted(BUILDERS))
def test_hard_deleting_an_item_never_rewrites_an_event_row(
    client: TestClient,
    headers_a: dict[str, str],
    repository: SqliteRepository,
    event_name: str,
) -> None:
    """The decisive test: no implicit UPDATE of any kind may touch an append-only row.

    Whole frozen-dataclass equality (not just `item_id`) is the assertion, so a future FK that
    sets any other column to NULL fails here too. The `get_item(...) is None` check is what stops
    this from passing because "nothing happened" — the item really has to be gone.
    """
    item_id = new_item(client, headers_a)
    body = body_for(event_name, item_id)
    assert post(client, headers_a, body).status_code == 202

    event_id = UUID(str(body["event_id"]))
    expected = UUID(item_id) if "item_id" in body else None
    before = repository.get_analytics_event(event_id)
    assert before is not None
    assert before.item_id == expected

    # Deliberately **no try/except**: with the withdrawn FK this raises IntegrityError on the
    # five item events, which is the bug this test exists to catch.
    hard_delete_item(repository, item_id)
    assert repository.get_item(UUID(item_id), user_id=USER_A, include_deleted=True) is None

    after = repository.get_analytics_event(event_id)
    assert after is not None
    assert after == before
    assert after.item_id == expected


@pytest.mark.parametrize("event_name", ITEM_EVENTS)
def test_the_five_item_events_keep_their_join_key_after_a_hard_delete(
    client: TestClient,
    headers_a: dict[str, str],
    repository: SqliteRepository,
    event_name: str,
) -> None:
    """`tool_time_ms`, activation-by-item and the export-failure gap still need the id.

    An orphaned row that keeps its `item_id` is still attributable; an orphaned row with a NULL
    id is not. This is the exact reason `ON DELETE SET NULL` cannot be used.
    """
    item_id = new_item(client, headers_a)
    body = body_for(event_name, item_id)
    assert "item_id" in body
    assert post(client, headers_a, body).status_code == 202

    hard_delete_item(repository, item_id)
    assert repository.get_item(UUID(item_id), user_id=USER_A, include_deleted=True) is None
    record = repository.get_analytics_event(UUID(str(body["event_id"])))
    assert record is not None
    assert record.event in ITEM_EVENTS
    assert record.item_id == UUID(item_id)


@pytest.mark.parametrize("with_item", [True, False])
def test_a_paywall_impression_is_not_rewritten_by_a_hard_delete(
    client: TestClient,
    headers_a: dict[str, str],
    repository: SqliteRepository,
    with_item: bool,
) -> None:
    """The one event the withdrawn FK *did* rewrite silently: `item_id` became NULL.

    Both legal shapes are covered: `trigger=on_model` / `export_limit` carry an item, a
    settings-screen paywall does not. Neither may change when the item disappears.
    """
    item_id = new_item(client, headers_a)
    body = BUILDERS["paywall_impression"]()
    if with_item:
        body["item_id"] = item_id
    assert post(client, headers_a, body).status_code == 202

    event_id = UUID(str(body["event_id"]))
    before = repository.get_analytics_event(event_id)
    assert before is not None
    assert before.item_id == (UUID(item_id) if with_item else None)

    hard_delete_item(repository, item_id)
    assert repository.get_item(UUID(item_id), user_id=USER_A, include_deleted=True) is None
    after = repository.get_analytics_event(event_id)
    assert after == before
    assert after is not None
    assert after.item_id == (UUID(item_id) if with_item else None)


@pytest.mark.skipif(
    IS_POSTGRES_CONFIGURED,
    reason="SQLite-only: `pragma foreign_keys` / `pragma foreign_key_list` have no Postgres "
    "equivalent, and assets has no FK to items on Postgres. The append-only invariant this test "
    "anchors is proven cross-backend by the hard-delete behaviour tests below.",
)
def test_foreign_keys_are_enabled_and_events_item_id_has_no_delete_action(
    repository: SqliteRepository,
) -> None:
    """Schema introspection on the *live* table, not a claim about SQL text.

    `pragma foreign_keys` is per-connection and defaults OFF in SQLite, so the positive control
    matters: an `assets` row must cascade away on a hard delete. Without it, "the DELETE
    succeeded" could just mean no FK is being enforced at all and the introspection above would
    describe a decorative schema.
    """
    assert repository._conn.execute("pragma foreign_keys").fetchone()[0] == 1, (
        "SqliteRepository must enable FK enforcement, or the cascade positive control is a lie"
    )
    assert not any(
        row["from"] == "item_id"
        for row in repository._conn.execute("pragma foreign_key_list(events)").fetchall()
    ), "events.item_id must not carry any ON DELETE action (ADR-008 Corrigendum)"

    item = repository.create_item(user_id=USER_A)
    repository.set_asset(
        item.id, user_id=USER_A, kind="source", storage_path="items/x/source.jpg"
    )
    assert repository._query("select count(*) as n from assets")[0]["n"] == 1
    hard_delete_item(repository, str(item.id))
    assert repository._query("select count(*) as n from assets")[0]["n"] == 0, (
        "the assets cascade did not fire — pragma foreign_keys is not actually in force"
    )


def test_an_item_event_cannot_be_nulled_after_the_fact(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    """The anti-regression anchor: relaxing the forward CHECK would make this go green.

    It reproduces the *mechanism* of the bug directly — a CHECK re-evaluated on UPDATE —
    without depending on any FK existing. Any future "just allow NULL" fix dies here.
    """
    item_id = new_item(client, headers_a)
    body = body_for("export_delivered", item_id)
    assert post(client, headers_a, body).status_code == 202
    event_id = str(body["event_id"])

    with pytest.raises(_integrity_error()) as excinfo:
        repository._execute(
            f"update events set item_id = null where event_id = {_placeholder(repository)}",
            (event_id,),
        )
    assert "CHECK" in str(excinfo.value).upper()
    record = repository.get_analytics_event(UUID(event_id))
    assert record is not None
    assert record.item_id == UUID(item_id)


@pytest.mark.skipif(
    IS_POSTGRES_CONFIGURED,
    reason="SQLite-only mechanism: SQLite has no uuid type, so the mirror spells the shape out as "
    "a length + GLOB CHECK. Postgres enforces item_id shape via the `uuid` column type instead "
    "(strictly stronger); see the module docstring. The route layer still refuses a bad uuid on "
    "both backends.",
)
def test_the_sqlite_mirror_refuses_a_non_uuid_item_id(
    repository: SqliteRepository,
) -> None:
    """Dropping the FK must not leave `item_id` as an unconstrained text pocket.

    Postgres gets this from the `uuid` column type; SQLite has no such type, so the mirror spells
    it out as a length + GLOB CHECK. This is a raw repository insert — no route, no pydantic.
    """
    record = minimal_event_record(item_id="tee")
    with pytest.raises(sqlite3.IntegrityError) as excinfo:
        repository.append_analytics_event(record)
    assert "CHECK" in str(excinfo.value).upper()


#: The multi-line `item_id text check (...)` column definition inside `_SCHEMA`. Kept as one
#: compiled pattern so the two fixtures below cannot disagree about what a "column DDL" is.
ITEM_ID_COLUMN_DDL = re.compile(r"    item_id\s+text\s+check \(.*?\),\n", re.S)


def _pre_fix_schema_with_bad_fk() -> str:
    """`_SCHEMA` with the item_id column put back to the withdrawn `ON DELETE SET NULL` FK."""
    schema, count = ITEM_ID_COLUMN_DDL.subn(
        "    item_id              text    references items (id) on delete set null,\n",
        repository_module._SCHEMA,
        count=1,
    )
    assert count == 1, "_SCHEMA's item_id column moved; update this fixture deliberately"
    return schema


def _genuinely_pre_0003_schema() -> str:
    """`_SCHEMA` with the whole `events` table replaced by one that predates `item_id`.

    The per-item index is dropped from the *build* schema too: it references `item_id` and would
    fail before the migration helper could add the column. The real `_SCHEMA` creates it at
    instantiation, after `_migrate_event_item_column()` has run.
    """
    simple_events = """create table if not exists events (
    event_id         text primary key,
    event            text not null,
    client_ts        text not null,
    received_at      text not null,
    session_id       text not null,
    subject          text not null,
    app_version      text not null,
    platform         text not null,
    payload_sha256   text not null
);
"""
    schema, count = re.subn(
        r"create table if not exists events \(.*?\n\);\n",
        simple_events,
        repository_module._SCHEMA,
        count=1,
        flags=re.S,
    )
    assert count == 1, "could not locate the events table in _SCHEMA"
    schema, count = re.subn(
        r"create index if not exists events_item_event_received\s+on events \(.*?\);\n",
        "",
        schema,
        count=1,
        flags=re.S,
    )
    assert count == 1, "could not locate the per-item index in _SCHEMA"
    return schema


def test_a_pre_fix_file_database_with_the_bad_fk_is_refused_loudly() -> None:
    """A file built by the withdrawn FK must fail, not quietly keep the wrong behaviour.

    SQLite cannot drop a constraint with ALTER TABLE, so the honest remedy is a loud
    RuntimeError naming the problem and telling the operator to delete the dev database.
    """
    handle, database = tempfile.mkstemp(prefix="snaplist-prefix-events-", suffix=".db")
    os.close(handle)  # a zero-byte file is a valid empty SQLite database
    try:
        connection = sqlite3.connect(database)
        try:
            connection.executescript(_pre_fix_schema_with_bad_fk())
        finally:
            connection.close()

        with pytest.raises(RuntimeError) as excinfo:
            SqliteRepository(database)
        message = str(excinfo.value)
        assert "pre-fix" in message
        assert "ON DELETE SET NULL" in message
        assert "append-only" in message
        assert "make dev" in message
        assert "cannot" in message.lower() and "drop" in message.lower()
    finally:
        os.unlink(database)


def test_a_genuinely_pre_0003_file_database_gets_a_plain_column() -> None:
    """The migration helper adds `item_id` with **no FK** — and honestly stays looser.

    SQLite's `alter table` can add a column but not a CHECK, so this database lacks the uuid
    shape CHECK and both grade CHECKs; it enforces the grade only in the router. That residual
    is asserted here rather than left to prose in a handoff.
    """
    handle, database = tempfile.mkstemp(prefix="snaplist-pre0003-events-", suffix=".db")
    os.close(handle)  # a zero-byte file is a valid empty SQLite database
    try:
        connection = sqlite3.connect(database)
        try:
            connection.executescript(_genuinely_pre_0003_schema())
        finally:
            connection.close()

        repository = SqliteRepository(database)
        try:
            columns = {row["name"] for row in repository._query("pragma table_info(events)")}
            assert "item_id" in columns
            assert not any(
                row["from"] == "item_id"
                for row in repository._query("pragma foreign_key_list(events)")
            ), "the migration must NOT reintroduce the withdrawn FK"
            ddl = repository._query(
                "select sql from sqlite_master where type = 'table' and name = 'events'"
            )[0]["sql"]
            assert "item_id is null or event <> 'subscription_started'" not in ddl
            assert "or item_id is not null" not in ddl
            assert "not glob" not in ddl
        finally:
            repository.close()
    finally:
        os.unlink(database)


# ---------------------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------------------
@contextmanager
def file_backed_client() -> Iterator[tuple[TestClient, Path]]:
    """An app on a **file** database, so a second connection can count rows independently.

    `tempfile.mkdtemp` rather than pytest's `tmp_path`: on this machine `Path.mkdir` is
    intercepted by a sandbox shim that raises `FileExistsError` even with `exist_ok=True`, so
    the standard fixture cannot create its own directory.

    A file backend is what makes "exactly one row" a fact about the database rather than a
    fact about `SqliteRepository` choosing to answer 1 — the row count below is read by a
    plain `sqlite3` connection that knows nothing about this codebase.
    """
    directory = tempfile.mkdtemp(prefix="snaplist-events-")
    database = Path(directory) / "events.db"
    repository = SqliteRepository(database)
    app = create_app(
        repository=repository,
        settings=Settings(env="local", supabase_jwt_secret=""),
    )
    try:
        with TestClient(app) as client:
            yield client, database
    finally:
        repository.close()
        shutil.rmtree(directory, ignore_errors=True)


def test_replay_is_202_duplicate_and_writes_exactly_one_row() -> None:
    with file_backed_client() as (client, database):
        headers = {"x-snaplist-user-id": str(USER_A)}
        body = body_for("export_delivered", new_item(client, headers))
        first = post(client, headers, body)
        second = post(client, headers, body)
        rows = stored_rows(database)
    assert first.status_code == 202, first.text
    assert first.json() == {"accepted": True, "duplicate": False}
    assert second.status_code == 202, second.text
    assert second.json() == {"accepted": True, "duplicate": True}
    assert len(rows) == 1


def test_replay_with_a_different_payload_is_409() -> None:
    """A reused id is a client bug, and swallowing it would make the funnel quietly wrong."""
    with file_backed_client() as (client, database):
        headers = {"x-snaplist-user-id": str(USER_A)}
        body = body_for("export_delivered", new_item(client, headers))
        assert post(client, headers, body).status_code == 202
        changed = dict(body, row_count=99)
        response = post(client, headers, changed)
        rows = stored_rows(database)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_request"
    assert response.json()["error"]["details"]["event_id"] == body["event_id"]
    # the first payload still stands; the second was not written anywhere
    assert len(rows) == 1
    assert rows[0]["row_count"] == 3


def test_replay_is_recognised_across_timestamp_spellings() -> None:
    """`...T10:00:00Z` and `...T12:00:00+02:00` are the same instant, so not a new payload."""
    with file_backed_client() as (client, database):
        headers = {"x-snaplist-user-id": str(USER_A)}
        body = body_for("upload_started", new_item(client, headers))
        assert post(client, headers, body).status_code == 202
        respelled = dict(body, client_ts="2026-05-01T12:00:00+02:00")
        response = post(client, headers, respelled)
        rows = stored_rows(database)
    assert response.status_code == 202, response.text
    assert response.json()["duplicate"] is True
    assert len(rows) == 1


def test_a_different_event_id_is_a_second_row(
    client: TestClient, headers_a: dict[str, str], repository: SqliteRepository
) -> None:
    """Idempotency is per id, not per content: the same step twice is two rows on purpose."""
    body = body_for("prettify_approved", new_item(client, headers_a))
    assert post(client, headers_a, body).status_code == 202
    retried = dict(body, event_id=str(uuid4()))
    assert post(client, headers_a, retried).status_code == 202
    assert repository.get_analytics_event(UUID(str(body["event_id"]))) is not None
    assert repository.get_analytics_event(UUID(str(retried["event_id"]))) is not None


# ---------------------------------------------------------------------------------------
# Transport-level refusals
# ---------------------------------------------------------------------------------------
def test_a_body_over_eight_kilobytes_is_413(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    # Literal 8192, not `ev.MAX_EVENT_BYTES`: QA set the constant to 64 KiB and then 1 GiB and
    # this test kept passing, because it was asserting "the limit equals the limit".
    assert ev.MAX_EVENT_BYTES == 8192, "the DoD says 8 KB; update the test and the docs"
    body = BUILDERS["upload_started"]()
    body["padding"] = "x" * (8192 + 512)
    response = post(client, headers_a, body)
    assert response.status_code == 413, response.text
    assert response.json()["error"]["details"]["limit_bytes"] == "8192"


def test_a_body_at_the_ceiling_is_not_rejected_for_size(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    """8 KB *passes*; the refusal is `> 8 KB`, not `>=`. Pinned so the boundary cannot drift."""
    body = BUILDERS["upload_started"]()
    body["padding"] = ""
    padding = 8192 - len(json.dumps(body))
    body["padding"] = "x" * padding  # one byte in the JSON per padding character
    raw = json.dumps(body)
    assert len(raw) == 8192
    response = client.post(
        ENDPOINT, headers={**headers_a, "content-type": "application/json"}, content=raw
    )
    # 422 (unknown key `padding`) proves it got past the size gate; 413 would prove it did not.
    assert response.status_code == 422, response.text
    assert "padding" in json.dumps(response.json()["error"]["details"])


@pytest.mark.parametrize("payload", [b"not json at all", b"[1, 2, 3]", b""])
def test_a_body_that_is_not_a_json_object_is_422(
    client: TestClient, headers_a: dict[str, str], payload: bytes
) -> None:
    response = client.post(
        ENDPOINT, headers={**headers_a, "content-type": "application/json"}, content=payload
    )
    assert response.status_code == 422, response.text
    assert set(response.json()["error"]) <= ENVELOPE_KEYS


def test_get_is_405_because_the_read_side_does_not_exist(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    """Events are read by the backend, never by a client. 405, not 404: the path is real."""
    response = client.get(ENDPOINT, headers=headers_a)
    assert response.status_code == 405, response.text
    assert response.json()["error"]["code"] == "invalid_request"


def test_every_refusal_uses_the_contract_error_envelope(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    probes = [
        post(client, headers_a, envelope(event="nope")),
        post(client, headers_a, {**BUILDERS["upload_started"](), "email": "a@b"}),
        post(client, headers_a, {**BUILDERS["upload_started"](), "subject": f"usr_{USER_B}"}),
        client.get(ENDPOINT, headers=headers_a),
        client.post(ENDPOINT, json=BUILDERS["upload_started"]()),
    ]
    for response in probes:
        body = response.json()
        assert set(body) == {"error"}, response.text
        assert set(body["error"]) <= ENVELOPE_KEYS
        assert "detail" not in body, "a FastAPI `detail` body is a second error contract"


# ---------------------------------------------------------------------------------------
# The migration, checked as text (there is no Postgres in this environment)
# ---------------------------------------------------------------------------------------
TABLE_START = "create table if not exists public.events ("

#: Type keywords that may start a column definition. Used to tell a column line from a
#: continuation line of a CHECK expression, without a SQL parser.
SQL_TYPES = frozenset({"uuid", "text", "text[]", "timestamptz", "integer", "bigint"})

#: Every column `public.events` is allowed to have. **Adding to this list is a payload change**
#: — it needs a zod mirror, an `EVENTS_VERSION` bump and an ADR. That is why it is written out
#: in full here rather than computed: the list *is* the assertion.
EXPECTED_COLUMNS = frozenset(
    {
        "event_id",
        "event",
        "client_ts",
        "received_at",
        "session_id",
        "subject",
        "app_version",
        "platform",
        # 1. upload_started
        "source",
        "capture_mode",
        "batch_index",
        # 2. prettify_approved
        "decision",
        "job_latency_ms",
        "prettify_latency_ms",
        "attrs_latency_ms",
        # 3. attributes_edited
        "edited_field_count",
        "fields_touched",
        "field_names",
        # 4. export_clicked / 7. export_delivered
        "marketplace",
        "row_count",
        "export_latency_ms",
        "bytes",
        "delivery_latency_ms",
        # 5. subscription_started
        "plan",
        "paywall_variant",
        "amount_cents",
        "currency",
        # 6. paywall_impression
        "paywall_trigger",
        "plan_offered",
        # replay-vs-misuse discriminator
        "payload_sha256",
        # the join key every per-item report needs (ADR-008), added by 0003
        "item_id",
    }
)


def migration_text() -> str:
    assert MIGRATION.exists(), f"{MIGRATION} is missing — the table has no Postgres definition"
    return MIGRATION.read_text(encoding="utf-8")


def item_id_migration_text() -> str:
    assert MIGRATION_ITEM_ID.exists(), (
        f"{MIGRATION_ITEM_ID} is missing — `item_id` would exist in no Postgres definition"
    )
    return MIGRATION_ITEM_ID.read_text(encoding="utf-8")


def table_body(text: str) -> str:
    assert TABLE_START in text, "public.events is not created by this migration"
    return text.split(TABLE_START, 1)[1].split("\n);", 1)[0]


def parsed_columns(text: str) -> dict[str, str]:
    """`{column: type}` for the table, ignoring constraints and comment lines."""
    columns: dict[str, str] = {}
    for raw_line in table_body(text).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--"):
            continue
        if line.startswith("constraint") or line.startswith("check"):
            continue
        tokens = line.split()
        # `bytes bigint,` has the comma glued to the type; `event_id uuid primary key,` does not
        if len(tokens) >= 2 and tokens[1].rstrip(",") in SQL_TYPES:
            columns[tokens[0]] = tokens[1].rstrip(",")
    return columns


def test_migration_pins_all_seven_event_names() -> None:
    text = migration_text()
    check = re.search(r"constraint events_event_check check \(event in \((.*?)\)\)", text, re.S)
    assert check is not None, "the event vocabulary is not pinned by a CHECK"
    pinned = re.findall(r"'([a-z_]+)'", check.group(1))
    assert sorted(pinned) == sorted(PY_EVENTS), (
        "the migration's CHECK and the pydantic union disagree about the vocabulary"
    )


def test_migration_enables_rls_and_gives_clients_nothing() -> None:
    text = migration_text()
    assert "alter table public.events enable row level security;" in text
    # exactly one policy, for the service role — no policy may name a client role
    policies = re.findall(r"create policy (\w+) on public\.events\s+(.*?);", text, re.S)
    assert len(policies) == 1, policies
    name, definition = policies[0]
    assert name == "events_service_role_all"
    assert "to service_role" in definition
    for client_role in ("authenticated", "anon"):
        assert f"to {client_role}" not in definition
    # and the reason is written down where the next reader will look
    assert "service role" in text and "no permission" in text.lower()


def test_events_table_has_no_free_text_column() -> None:
    """The structural half of the PII ban: there is nowhere for a value to land.

    Two assertions, and the second is the one that keeps working after this ticket:
    **every** `text`/`text[]` column must be mentioned in a CHECK. A future column added
    without a constraint (the way free text arrives: `metadata jsonb`, `note text`,
    `context jsonb`) fails here even if somebody also updated `EXPECTED_COLUMNS` to allow it.
    """
    text = migration_text()
    columns = parsed_columns(text)
    # 0003 is an `alter table ... add column`, not part of the create statement `parsed_columns`
    # reads, so the whitelist check has to account for it explicitly. Asserting the exact
    # spelling here is what makes "0003 adds exactly one column, of exactly this type" a fact
    # rather than an assumption.
    added = re.search(
        r"add column if not exists item_id\s+uuid", item_id_migration_text()
    )
    assert added is not None, "0003 does not add `item_id uuid` — see ADR-008"
    columns["item_id"] = "uuid"

    assert set(columns) == set(EXPECTED_COLUMNS), {
        "unexpected": sorted(set(columns) - set(EXPECTED_COLUMNS)),
        "missing": sorted(set(EXPECTED_COLUMNS) - set(columns)),
    }

    body = table_body(text).lower()
    for banned in ("jsonb", " json ", "hstore", "bytea"):
        assert banned not in body, f"`{banned}` is a free-form column and has no business here"

    check_lines = "\n".join(
        line for line in text.splitlines() if "check" in line.lower()
    )
    unconstrained = {
        name
        for name, type_name in columns.items()
        if type_name.startswith("text")
        and re.search(rf"\b{re.escape(name)}\b", check_lines) is None
    }
    assert not unconstrained, (
        "these text columns have no CHECK, so they can hold free text: "
        f"{sorted(unconstrained)}"
    )


def test_migration_requires_a_payload_digest() -> None:
    """Without it, "same id, different content" and "genuine retry" are indistinguishable."""
    columns = parsed_columns(migration_text())
    assert columns["payload_sha256"] == "text"
    assert "payload_sha256        text        not null" in migration_text()
    assert "events_payload_sha256_is_hex" in migration_text()


def test_field_names_regex_is_identical_in_three_places() -> None:
    """One regex, three copies (TS schema, python model, SQL function). Pin them together."""
    ts_pattern = re.search(r"regex\(/(.+?)/\)", ANALYTICS_TS.read_text(encoding="utf-8"))
    assert ts_pattern is not None
    assert ts_pattern.group(1) == ev.FIELD_NAME_PATTERN
    assert ts_pattern.group(1) in migration_text()


# ---------------------------------------------------------------------------------------
# `0003_events_item_id.sql`, checked as text for the same reason
# ---------------------------------------------------------------------------------------
def test_migration_0003_does_not_add_a_delete_action() -> None:
    """The column must be a bare `uuid`, with **no** `references` clause at all.

    `ON DELETE SET NULL` is an implicit UPDATE and collides with the forward CHECK that requires
    `item_id` on five events (ADR-008 Corrigendum); `CASCADE` deletes evidence; `NO ACTION`
    refuses the delete. The assertion is scoped to the **DDL statement**, not the whole file —
    the corrected file's comments describe the rejected mechanisms in prose, and a full-text
    `"on delete" not in text` assertion would be red for the wrong reason.

    This is a cheap guard against re-adding the string. The *behaviour* is proven by the hard
    delete tests above against the real SQLite table.
    """
    text = item_id_migration_text()
    assert re.search(r"add column if not exists item_id\s+uuid", text) is not None, (
        "0003 no longer adds `item_id uuid`"
    )
    add_column = re.search(r"add column if not exists item_id\s+uuid[^;]*;", text, re.S)
    assert add_column is not None, "could not isolate the item_id DDL statement"
    statement = add_column.group(0).lower()
    assert "references" not in statement, (
        "0003 reintroduced a foreign key on item_id — it will rewrite append-only history"
    )
    assert "on delete" not in statement
    assert "jsonb" not in statement and " text" not in statement


def test_migration_0003_pins_the_grade_in_both_directions() -> None:
    """A forward CHECK alone lets a Stripe row carry an item; a reverse one alone lets an
    `export_delivered` row arrive with none. Both are asserted by name, so deleting one fails
    here rather than shipping."""
    text = item_id_migration_text()

    reverse = re.search(
        r"events_item_id_forbidden_on_subscription_started\s+check \((.*?)\)", text, re.S
    )
    assert reverse is not None, "no reverse CHECK: subscription_started could carry an item_id"
    assert "item_id is null" in reverse.group(1)
    assert "'subscription_started'" in reverse.group(1)

    forward = re.search(
        r"events_item_id_required_for_item_events\s+check \((.*?)\)", text, re.S
    )
    assert forward is not None, "no forward CHECK: the five events could arrive without an item"
    assert "item_id is not null" in forward.group(1)
    for event_name in ITEM_EVENTS:
        assert f"'{event_name}'" in forward.group(1), (
            f"{event_name} is not in the forward CHECK, so it does not actually require an item"
        )
    # and the sixth event is deliberately absent from the required list
    assert "'paywall_impression'" not in forward.group(1)


def test_migration_0003_adds_the_per_item_index() -> None:
    """`(item_id, event, received_at desc)` — the shape of "one item's timeline, by step"."""
    text = item_id_migration_text()
    index = re.search(
        r"create index if not exists events_item_event_received_idx\s+on public\.events \((.*?)\)",
        text,
        re.S,
    )
    assert index is not None, "no per-item index: activation and tool-time reads would scan"
    columns = " ".join(index.group(1).split())
    assert columns == "item_id, event, received_at desc"


def test_the_two_migrations_agree_about_the_item_grade() -> None:
    """The SQLite mirror and the shipped migration must say the same thing, not merely exist."""
    postgres = item_id_migration_text()
    sqlite = (ROOT / "apps" / "api" / "repository.py").read_text(encoding="utf-8")
    # both directions exist in the mirror as table CHECKs (SQLite cannot add a CHECK later)
    assert "item_id is null or event <> 'subscription_started'" in postgres
    assert "item_id is null or event <> 'subscription_started'" in sqlite
    assert "events_item_id_forbidden_on_subscription_started" in postgres
    assert "or item_id is not null" in sqlite
    for event_name in ITEM_EVENTS:
        assert f"'{event_name}'" in sqlite.split("or item_id is not null")[0][-600:], (
            f"{event_name} is missing from the SQLite forward CHECK"
        )
    # and the index exists on both sides
    assert "events_item_event_received" in sqlite
    assert "events_item_event_received_idx" in postgres

    # Both DDL statements must be FK-free. Scoped to the statements: the corrected files
    # discuss the withdrawn FK in prose, so a whole-file `"references" not in text` would be
    # red for the wrong reason. This is the text-level half; the live-table half is
    # `test_foreign_keys_are_enabled_and_events_item_id_has_no_delete_action`.
    postgres_add = re.search(
        r"add column if not exists item_id\s+uuid[^;]*;", postgres, re.S
    )
    assert postgres_add is not None
    assert "references" not in postgres_add.group(0).lower()
    sqlite_column = ITEM_ID_COLUMN_DDL.search(sqlite)
    assert sqlite_column is not None, "the SQLite item_id column DDL is not where expected"
    assert "references" not in sqlite_column.group(0).lower()
    assert "alter table events add column item_id text" in sqlite, (
        "the pre-0003 SQLite migration must add a plain column, with no FK"
    )


# ---------------------------------------------------------------------------------------
# The frozen vocabulary, read out of packages/shared/src/analytics.ts
# ---------------------------------------------------------------------------------------
def _ts_vocabulary() -> tuple[list[str], dict[str, set[str]], set[str]]:
    """Parse the zod file: event names, per-event payload fields, envelope fields.

    Regex, not a TS parser, because the file's shape is fixed and a parser would be a new
    dependency for one assertion. The three patterns below are anchored (`^` with MULTILINE)
    so a field name mentioned inside a doc comment — `/** Opaque subject: anon_<uuid> ... */`
    — cannot be mistaken for a field.
    """
    text = ANALYTICS_TS.read_text(encoding="utf-8")

    names_block = re.search(r"export const ANALYTICS_EVENTS = \[(.*?)\] as const;", text, re.S)
    assert names_block is not None, "ANALYTICS_EVENTS is not where this test expects it"
    names = re.findall(r'"([a-z_]+)"', names_block.group(1))

    envelope_block = re.search(r"const Envelope = z\s*\.object\(\{(.*?)\n  \}\)", text, re.S)
    assert envelope_block is not None, "the Envelope object is not where this test expects it"
    envelope_fields = set(re.findall(r"^    (\w+):", envelope_block.group(1), re.MULTILINE))

    payloads: dict[str, set[str]] = {}
    for block in re.finditer(
        r"export const (\w+) = Envelope\.extend\(\{(.*?)\n\}\);", text, re.S
    ):
        body = block.group(2)
        tag = re.search(r'event: z\.literal\("([a-z_]+)"\)', body)
        assert tag is not None, f"{block.group(1)} has no literal event tag"
        payloads[tag.group(1)] = set(re.findall(r"^  (\w+):", body, re.MULTILINE)) - {"event"}
    return names, payloads, envelope_fields


def test_the_typescript_vocabulary_parses_as_seven_events() -> None:
    """Guard the parser itself: a refactor of analytics.ts must not make this file vacuous."""
    names, payloads, envelope_fields = _ts_vocabulary()
    assert len(names) == 7
    assert set(payloads) == set(names)
    # Eight fields as of `EVENTS_VERSION = 1.4.0`. `event_id` joined in 1.3.0 (ADR-007) and
    # `item_id` in 1.4.0 (ADR-008). This assertion is a parser self-check, not a vocabulary
    # freeze: if analytics.ts gains or loses a field, the parser must be re-read rather than
    # silently keep returning seven — which is exactly what happened when `item_id` landed and
    # this literal still said six-plus-one.
    assert len(envelope_fields) == 8
    assert envelope_fields == {
        "event_id",
        "event",
        "item_id",
        "client_ts",
        "session_id",
        "subject",
        "app_version",
        "platform",
    }


def test_python_models_mirror_the_frozen_typescript_vocabulary() -> None:
    """Every event name, and every field of every event, matches `analytics.ts` exactly.

    This is the drift alarm. `analytics.ts` is the typed source of truth for the *shape*
    (docs/CONTRACTS.md §7); the pydantic models in `apps/api/routers/events.py` are what the
    server actually accepts. If one gains or loses a field, this goes red — and the diff of the
    failure names the field, which is the whole reason for comparing the two artifacts instead
    of two hand-written lists.
    """
    names, ts_payloads, ts_envelope = _ts_vocabulary()
    assert set(names) == set(PY_EVENTS), {
        "ts_only": sorted(set(names) - set(PY_EVENTS)),
        "python_only": sorted(set(PY_EVENTS) - set(names)),
    }

    expected_envelope = ts_envelope | API_ADDITIVE_FIELDS
    mismatches: dict[str, dict[str, list[str]]] = {}
    for event_name, payload in ts_payloads.items():
        expected = expected_envelope | payload
        actual = set(PY_EVENTS[event_name].model_fields)
        if actual != expected:
            mismatches[event_name] = {
                "python_only": sorted(actual - expected),
                "ts_only": sorted(expected - actual),
            }
    assert not mismatches, mismatches


def test_the_seven_bodies_here_validate_against_the_models() -> None:
    """`BUILDERS` is hand-written; this proves it is not hand-written *wrong*.

    Without it, a typo in a builder would show up as an unrelated 422 in the happy-path test
    and send the next reader looking in the router.
    """
    for event_name, builder in BUILDERS.items():
        validated = ev.EVENT_ADAPTER.validate_python(builder())
        assert validated.event == event_name


#: The three grades `item_id` can have, as this file names them (ADR-008).
REQUIRED = "required"
OPTIONAL = "optional"
FORBIDDEN = "forbidden"


def _typescript_item_grade(declaration: str | None) -> str:
    """Read the grade off the zod declaration, or `optional` when the event inherits it."""
    if declaration is None:
        return OPTIONAL  # inherited from the envelope: `.uuid().nullable().optional()`
    if declaration.startswith("z.string().uuid()"):
        return REQUIRED
    if declaration.startswith("z.null()"):
        return FORBIDDEN
    raise AssertionError(f"unrecognised item_id declaration in analytics.ts: {declaration!r}")


def _python_item_grade(event_name: str) -> str:
    field = PY_EVENTS[event_name].model_fields["item_id"]
    if field.annotation is type(None):
        return FORBIDDEN
    return REQUIRED if field.is_required() else OPTIONAL


def test_item_id_grade_is_the_same_in_python_and_typescript() -> None:
    """The per-event grade is the whole point of ADR-008, so it gets its own parity check.

    Field-name parity (`test_python_models_mirror_the_frozen_typescript_vocabulary`) cannot see
    this: `item_id` present on all seven models is exactly what a lazy implementation looks
    like. This compares the *requirement*, event by event, in both languages — and asserts the
    expected grade literally, so making everything optional fails here even if both sides agree.
    """
    text = ANALYTICS_TS.read_text(encoding="utf-8")
    ts_grade: dict[str, str] = {}
    for block in re.finditer(
        r"export const (\w+) = Envelope\.extend\(\{(.*?)\n\}\);", text, re.S
    ):
        tag = re.search(r'event: z\.literal\("([a-z_]+)"\)', block.group(2))
        assert tag is not None
        declaration = re.search(r"^  item_id: ([^\n,]+),?$", block.group(2), re.MULTILINE)
        ts_grade[tag.group(1)] = _typescript_item_grade(
            declaration.group(1) if declaration else None
        )

    expected = {name: REQUIRED for name in ITEM_EVENTS}
    expected["paywall_impression"] = OPTIONAL
    expected["subscription_started"] = FORBIDDEN

    assert set(ts_grade) == set(expected)
    assert ts_grade == expected, {"typescript": ts_grade, "expected": expected}
    assert {name: _python_item_grade(name) for name in PY_EVENTS} == expected
