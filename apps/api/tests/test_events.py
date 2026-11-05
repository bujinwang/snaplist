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
   authenticated uuid (`test_subject_must_name_the_caller`).

Everything runs against the SQLite mirror of the table (`apps/api/repository.py`). The Postgres
migration is checked at the text level only; there is no Postgres in this environment and the
honesty note in `infra/migrations/0002_events.sql` says so.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import get_args
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.repository import SqliteRepository
from apps.api.routers import events as ev
from apps.api.settings import Settings
from apps.api.tests.conftest import USER_A, USER_B

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "infra" / "migrations" / "0002_events.sql"
ANALYTICS_TS = ROOT / "packages" / "shared" / "src" / "analytics.ts"

ENDPOINT = "/v1/events"
ENVELOPE_KEYS = {"code", "message", "retry_after_seconds", "details"}

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
    """Builders, not dicts: every call gets a fresh `event_id`, so two tests cannot collide."""
    return {
        "upload_started": lambda: envelope(
            event="upload_started", source="camera", capture_mode="single"
        ),
        "prettify_approved": lambda: envelope(
            event="prettify_approved",
            decision="approve",
            job_latency_ms=1200,
            prettify_latency_ms=8000,
            attrs_latency_ms=900,
        ),
        "attributes_edited": lambda: envelope(
            event="attributes_edited",
            edited_field_count=2,
            fields_touched=5,
            field_names=["colors", "size_label"],
        ),
        "export_clicked": lambda: envelope(
            event="export_clicked", marketplace="ebay", row_count=3, export_latency_ms=1200
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
            marketplace="vinted",
            row_count=3,
            bytes=2048,
            delivery_latency_ms=400,
        ),
    }


BUILDERS = bodies_by_event()


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
    response = post(client, headers_a, BUILDERS[event_name]())
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
    body = BUILDERS["export_clicked"]()
    assert post(client, headers_a, body).status_code == 202
    record = repository.get_analytics_event(UUID(str(body["event_id"])))
    assert record is not None
    assert record.marketplace == "ebay"
    assert record.row_count == 3
    assert record.export_latency_ms == 1200
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

    at_cap = BUILDERS["attributes_edited"]()
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
    body = BUILDERS["attributes_edited"]()
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
    body = BUILDERS["upload_started"]()
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
    body = BUILDERS["export_delivered"]()
    with file_backed_client() as (client, database):
        headers = {"x-snaplist-user-id": str(USER_A)}
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
    body = BUILDERS["export_delivered"]()
    with file_backed_client() as (client, database):
        headers = {"x-snaplist-user-id": str(USER_A)}
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
    body = BUILDERS["upload_started"]()
    with file_backed_client() as (client, database):
        headers = {"x-snaplist-user-id": str(USER_A)}
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
    body = BUILDERS["prettify_approved"]()
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
    }
)


def migration_text() -> str:
    assert MIGRATION.exists(), f"{MIGRATION} is missing — the table has no Postgres definition"
    return MIGRATION.read_text(encoding="utf-8")


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
    assert envelope_fields == {
        # `event_id` joined the envelope in EVENTS_VERSION 1.3.0 (ADR-007). This assertion is a
        # parser self-check, not a vocabulary freeze: if analytics.ts gains or loses a field, the
        # parser must be re-read rather than silently keep returning six.
        "event_id",
        "event",
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
