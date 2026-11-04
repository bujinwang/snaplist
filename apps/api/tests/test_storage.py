"""E5-T4 — signed URLs, object keys, byte fidelity, and the two storage backends.

What this module proves, in the order it proves it
--------------------------------------------------
1. **Signature** — valid, unsigned, wrong key, tampered `exp`, wrong purpose, expired: six
   distinct facts, one assertion each. The signature is the only authorisation on
   `/v1/uploads` and `/v1/assets`, so "it mostly works" is not a standard it may meet.
2. **Key safety** — the key becomes a filesystem path in `LocalStorage`, so `..`, absolute
   paths, `//`, `\\`, control characters and over-long keys are 400s, and one test tries
   `items/../../etc/passwd` and then looks at the *disk* to confirm nothing appeared outside
   the root. A test that only asserts the status code would pass on a traversal that also
   wrote the file.
3. **Body limits** — 413 over the ceiling, 415 for a content-type outside the four accepted
   image formats, 400 for an empty body.
4. **Byte fidelity** — write then read hashes equal; the `assets.meta.sha256` written by
   `write_back` equals the hash of the bytes actually stored; an `expected_sha256` that
   disagrees raises **and leaves no row and no file**. That last one is the precondition for
   E3-T4's exact dedupe: dedupe on a hash that was not computed from the bytes is a coin flip.
5. **Idempotency** — the same key twice: 201 then 200, one row, the latest bytes.
6. **Read** — 200 with the same bytes, 403 without a signature or past the TTL, and 404 only
   for a *valid* signature (otherwise the route is an existence oracle).
7. **AssetPack** — the three URL fields are usable signed URLs (fetching one returns the
   original bytes) while `storage_path` holds the key.
8. **Claim** — a job is not claimable until its item has a `source` asset.
9. **Supabase** — the request shape for signing and uploading, through `httpx.MockTransport`.
10. **Zero network** — every test in the module runs with sockets trapped. The Supabase tests
    use `MockTransport`, which never opens one.

Time is injected (`FakeClock`), never slept: an "expiry" proven with `time.sleep` is proven
about the machine's clock, not about the code.
"""

from __future__ import annotations

import json
import socket
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.jobs.state_machine import apply_transition, fail
from apps.api.jobs.writeback import AssetWrite, WritebackError, write_back
from apps.api.main import create_app
from apps.api.repository import SqliteRepository
from apps.api.schemas import JobKind, JobState, PlanTier, ReasonCode
from apps.api.settings import Settings
from apps.api.storage import (
    ALLOWED_CONTENT_TYPES,
    BytesMismatch,
    InvalidKey,
    InvalidObject,
    LocalStorage,
    MissingObject,
    ObjectTooLarge,
    Storage,
    StorageError,
    StorageUnavailable,
    SupabaseStorage,
    UnavailableStorage,
    UnsupportedContentType,
    build_storage,
    parse_item_key,
    sha256_of,
    sign,
    source_object_key,
    validate_key,
)

USER = UUID("11111111-1111-4111-8111-111111111111")
HEADERS = {"x-snaplist-user-id": str(USER)}

BASE_TIME = 1_700_000_000.0  # 2023-11-14T22:13:20Z, a round unix second


# ---------------------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------------------
class FakeClock:
    """A clock a test can move. Expiry is proven by arithmetic, not by waiting."""

    def __init__(self, now: float = BASE_TIME) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No test in this module may reach the network — the rule `packages/vision/tests` uses.

    The trap is installed on `connect` / `create_connection` / `getaddrinfo` rather than on
    `socket.socket` itself, and the difference is not laziness: `TestClient` starts an anyio
    portal whose event loop needs a self-pipe built from `socket.socketpair`, which on this
    platform bottoms out in `socket.socket`. Replacing the class takes the test client down
    with it. These three functions are the ones every outbound path (`httpcore`, `urllib`)
    actually goes through, and the Supabase tests inject `httpx.MockTransport`, which calls
    none of them.
    """

    def _trap(*args: object, **kwargs: object) -> None:
        raise AssertionError("storage tests must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", _trap)
    monkeypatch.setattr(socket, "getaddrinfo", _trap)
    monkeypatch.setattr(socket.socket, "connect", _trap)
    monkeypatch.setattr(socket.socket, "connect_ex", _trap)
    yield


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def storage(tmp_path: Path, clock: FakeClock) -> LocalStorage:
    """A filesystem backend under a tmp dir, with a fixed secret and an injectable clock."""
    return LocalStorage(
        tmp_path / "objects",
        secret=b"unit-test-signing-secret-32bytes",
        upload_ttl_seconds=900,
        asset_ttl_seconds=3600,
        max_upload_bytes=4096,
        clock=clock,
    )


@pytest.fixture
def repository() -> Iterator[SqliteRepository]:
    store = SqliteRepository(":memory:")
    # Several tests create more items than the free plan allows (each one is a fresh upload
    # URL). The quota is not what those tests are about, and `plan_limits` is a runtime lever
    # precisely so a fixture can raise it without touching Settings.
    store.set_plan_limit(PlanTier.FREE, 10_000)
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def app(repository: SqliteRepository, storage: LocalStorage) -> FastAPI:
    return create_app(
        repository=repository,
        settings=Settings(env="local", supabase_jwt_secret=""),
        storage=storage,
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


#: A payload that is *not* a decodable image on purpose: storage must never inspect or
#: transcode bytes, and a real JPEG fixture would hide a re-encode that quietly "fixed" it.
PAYLOAD = b"\xff\xd8\xff\xe0" + b"snaplist-byte-fidelity" * 3 + b"\xff\xd9"


def signed_path(
    storage: LocalStorage, key: str, *, purpose: str = "upload", ttl: int | None = None
) -> str:
    """The URL path the router serves for `key`, signed with the backend's own secret."""
    exp = int(storage.clock()) + (storage.upload_ttl_seconds if ttl is None else ttl)
    sig = sign(storage.secret, purpose=purpose, key=key, exp=exp)
    route = "uploads" if purpose == "upload" else "assets"
    return f"/v1/{route}/{key}?exp={exp}&sig={sig}"


def query_of(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    return {name: values[0] for name, values in parse_qs(parsed.query).items()}


def path_of(url: str) -> str:
    """Path + query of an absolute signed URL, ready to hand back to `TestClient`."""
    parsed = urlparse(url)
    return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path


def new_item(
    client: TestClient, *, content_type: str | None = "image/jpeg"
) -> tuple[dict[str, Any], str]:
    """Create an item and return `(body, upload_url)`."""
    body_in: dict[str, Any] = {"filename": "tee.jpg"}
    if content_type is not None:
        body_in["content_type"] = content_type
    response = client.post("/v1/items", headers=HEADERS, json=body_in)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["upload_url"], "a configured LocalStorage must hand out an upload URL"
    return body, body["upload_url"]


def upload(
    client: TestClient,
    url: str,
    *,
    data: bytes = PAYLOAD,
    content_type: str = "image/jpeg",
) -> httpx.Response:
    return client.put(
        path_of(url), content=data, headers={"content-type": content_type}
    )


# ---------------------------------------------------------------------------------------
# 1. Signature — every failure is a 403, and the reason is never disclosed.
# ---------------------------------------------------------------------------------------
def test_a_valid_upload_signature_is_accepted(
    client: TestClient, storage: LocalStorage
) -> None:
    _, url = new_item(client)
    key = parse_key(url)
    params = query_of(url)
    assert storage.verify_signature(
        key, purpose="upload", exp=int(params["exp"]), sig=params["sig"]
    )
    assert upload(client, url).status_code == 201


def test_an_unsigned_upload_is_403(client: TestClient) -> None:
    _, url = new_item(client)
    unsigned = path_of(url).split("?", 1)[0]
    response = client.put(unsigned, content=PAYLOAD, headers={"content-type": "image/jpeg"})
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


def test_a_signature_for_another_key_is_403(client: TestClient, storage: LocalStorage) -> None:
    body, url = new_item(client)
    other_key = f"items/{USER}/{body['item_id']}/prettified.png"
    params = query_of(url)
    forged = f"/v1/uploads/{other_key}?exp={params['exp']}&sig={params['sig']}"
    response = client.put(forged, content=PAYLOAD, headers={"content-type": "image/jpeg"})
    assert response.status_code == 403, "a signature must not authorise a different object"


def test_a_tampered_exp_is_403(client: TestClient) -> None:
    _, url = new_item(client)
    params = query_of(url)
    path = path_of(url).split("?", 1)[0]
    later = int(params["exp"]) + 86_400
    response = client.put(
        f"{path}?exp={later}&sig={params['sig']}",
        content=PAYLOAD,
        headers={"content-type": "image/jpeg"},
    )
    assert response.status_code == 403, "extending the window must invalidate the signature"


def test_an_upload_signature_cannot_be_used_to_download(
    client: TestClient, repository: SqliteRepository
) -> None:
    """Purpose is part of the signed message: upload != download, by construction."""
    body, url = new_item(client)
    key = parse_key(url)
    repository.set_asset(
        UUID(body["item_id"]),
        user_id=USER,
        kind="prettified",
        storage_path=key,
        byte_size=len(PAYLOAD),
        meta={"sha256": sha256_of(PAYLOAD)},
    )
    params = query_of(url)
    response = client.get(f"/v1/assets/{key}?exp={params['exp']}&sig={params['sig']}")
    assert response.status_code == 403


def test_a_download_signature_cannot_be_used_to_upload(
    client: TestClient, storage: LocalStorage
) -> None:
    body, _ = new_item(client)
    key = f"items/{USER}/{body['item_id']}/source.jpg"
    asset_sig = signed_path(storage, key, purpose="asset")
    params = query_of("http://x" + asset_sig)
    response = client.put(
        f"/v1/uploads/{key}?exp={params['exp']}&sig={params['sig']}",
        content=PAYLOAD,
        headers={"content-type": "image/jpeg"},
    )
    assert response.status_code == 403


def test_an_expired_upload_url_is_403(
    client: TestClient, storage: LocalStorage, clock: FakeClock
) -> None:
    """The TTL is 900 s, proven by moving the clock past it — not by waiting."""
    _, url = new_item(client)
    assert storage.upload_ttl_seconds == 900
    clock.advance(899)
    assert upload(client, url).status_code == 201, "still inside the window"
    fresh = signed_path(storage, "items/x/y/source.jpg", ttl=0)
    clock.advance(2)
    response = client.put(
        f"{fresh}", content=PAYLOAD, headers={"content-type": "image/jpeg"}
    )
    assert response.status_code == 403, "exp + 2s must be dead"


def test_an_expired_download_url_is_403(
    client: TestClient, repository: SqliteRepository, storage: LocalStorage, clock: FakeClock
) -> None:
    body, _ = new_item(client)
    key = f"items/{USER}/{body['item_id']}/cutout.png"
    storage.write(key, PAYLOAD, content_type="image/png")
    url = storage.signed_asset_url(key, base_url="http://testserver")
    assert url is not None
    assert client.get(path_of(url)).status_code == 200
    clock.advance(storage.asset_ttl_seconds + 1)
    assert client.get(path_of(url)).status_code == 403


def parse_key(signed_url: str) -> str:
    """The object key out of `/v1/uploads/<key>?exp=..&sig=..`."""
    path = urlparse(signed_url).path
    return path.split("/v1/uploads/", 1)[1]


# ---------------------------------------------------------------------------------------
# 2. Object keys — the security boundary.
# ---------------------------------------------------------------------------------------
DANGEROUS_KEYS = [
    "items/../../etc/passwd",
    "../escape.jpg",
    "/etc/passwd",
    "items//double.jpg",
    "items/./same.jpg",
    "items\\windows.jpg",
    "items/bad\x00null.jpg",
    "items/newline\n.jpg",
    "",
    "items/" + "a" * 200 + ".jpg",
]


@pytest.mark.parametrize("key", DANGEROUS_KEYS)
def test_dangerous_keys_are_refused_by_the_key_validator(key: str) -> None:
    with pytest.raises(InvalidKey):
        validate_key(key)


@pytest.mark.parametrize("key", DANGEROUS_KEYS)
def test_dangerous_keys_cannot_write_outside_the_root(
    storage: LocalStorage, key: str
) -> None:
    with pytest.raises(InvalidKey):
        storage.write(key, PAYLOAD, content_type="image/jpeg")
    root = Path(storage.root)
    assert not (root.parent / "etc").exists()
    assert list(root.parent.glob("*.jpg")) == []


def test_a_traversal_upload_is_400_and_creates_no_file(
    client: TestClient, storage: LocalStorage, tmp_path: Path
) -> None:
    """The attack the ticket names, end to end, with an assertion about the *disk*.

    The key is signed by us — so the 400 has to come from key validation, not from a bad
    signature. `%2e%2e` is used because a URL parser would otherwise collapse `..` before the
    request left the client, and the server would never see the attack.
    """
    traversal = "items/%2e%2e/%2e%2e/etc/passwd"
    exp = int(storage.clock()) + 900
    sig = sign(storage.secret, purpose="upload", key="items/../../etc/passwd", exp=exp)
    response = client.put(
        f"/v1/uploads/{traversal}?exp={exp}&sig={sig}",
        content=PAYLOAD,
        headers={"content-type": "image/jpeg"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_request"
    root = Path(storage.root)
    outside = [p for p in tmp_path.rglob("passwd") if not p.is_relative_to(root)]
    assert outside == [], f"traversal wrote outside the root: {outside}"


def test_a_valid_key_is_returned_unchanged() -> None:
    key = f"items/{USER}/{uuid4()}/source.jpg"
    assert validate_key(key) == key


def test_parse_item_key_reads_only_item_shaped_keys() -> None:
    item_id = uuid4()
    assert parse_item_key(f"items/{USER}/{item_id}/source.jpg") == (USER, item_id)
    assert parse_item_key(f"items/{USER}/not-a-uuid/source.jpg") is None
    assert parse_item_key("fixtures/a/b/c/d") is None
    assert parse_item_key("items/short") is None


# ---------------------------------------------------------------------------------------
# 3. Body limits — 413 / 415 / 400.
# ---------------------------------------------------------------------------------------
def test_an_oversized_body_is_413(client: TestClient, storage: LocalStorage) -> None:
    _, url = new_item(client)
    too_big = b"\x00" * (storage.max_upload_bytes + 1)
    response = upload(client, url, data=too_big)
    assert response.status_code == 413, response.text
    assert response.json()["error"]["details"]["limit"] == str(storage.max_upload_bytes)


def test_a_bad_content_type_is_415(client: TestClient) -> None:
    _, url = new_item(client)
    response = upload(client, url, content_type="text/html")
    assert response.status_code == 415, response.text
    assert "image/jpeg" in response.json()["error"]["message"]


def test_every_accepted_content_type_is_actually_accepted(client: TestClient) -> None:
    for content_type in sorted(ALLOWED_CONTENT_TYPES):
        _, url = new_item(client, content_type=content_type)
        response = upload(client, url, content_type=content_type)
        assert response.status_code == 201, (content_type, response.text)


def test_an_empty_body_is_400(client: TestClient) -> None:
    _, url = new_item(client)
    response = upload(client, url, data=b"")
    assert response.status_code == 400, response.text
    assert "empty" in response.json()["error"]["message"].lower()


def test_an_upload_for_a_deleted_item_is_404_before_any_byte_is_written(
    client: TestClient, storage: LocalStorage
) -> None:
    body, url = new_item(client)
    assert client.delete(f"/v1/items/{body['item_id']}", headers=HEADERS).status_code == 204
    response = upload(client, url)
    assert response.status_code == 404, response.text
    assert not Path(storage.root, parse_key(url)).exists()


# ---------------------------------------------------------------------------------------
# 4. Byte fidelity.
# ---------------------------------------------------------------------------------------
def test_write_then_read_is_byte_identical(storage: LocalStorage) -> None:
    key = f"items/{USER}/{uuid4()}/source.jpg"
    stored = storage.write(key, PAYLOAD, content_type="image/jpeg")
    assert stored.sha256 == sha256_of(PAYLOAD)
    assert stored.byte_size == len(PAYLOAD)
    assert storage.read(key) == PAYLOAD


def test_upload_stores_bytes_verbatim_and_records_the_measured_digest(
    client: TestClient, repository: SqliteRepository, storage: LocalStorage
) -> None:
    """No transcode, no EXIF scrub: the object equals the body, byte for byte."""
    body, url = new_item(client)
    key = parse_key(url)
    assert upload(client, url).status_code == 201
    assert storage.read(key) == PAYLOAD

    record = repository.get_item(UUID(body["item_id"]), user_id=USER)
    assert record is not None
    source = next(a for a in record.assets if a.kind == "source")
    assert source.storage_path == key
    assert source.byte_size == len(PAYLOAD)
    assert source.meta is not None
    assert source.meta["sha256"] == sha256_of(PAYLOAD)
    assert source.meta["content_type"] == "image/jpeg"


def test_write_back_records_the_digest_of_the_bytes_it_stored(
    repository: SqliteRepository, storage: LocalStorage
) -> None:
    item = repository.create_item(user_id=USER)
    cutout = PAYLOAD + b"cutout"
    result = write_back(
        repository,
        storage,
        item_id=item.id,
        user_id=USER,
        assets=[AssetWrite(kind="cutout", data=cutout, content_type="image/png")],
    )
    assert len(result.objects) == 1
    key = result.objects[0].key
    assert key == f"items/{USER}/{item.id}/cutout.png"
    assert storage.read(key) == cutout

    record = repository.get_item(item.id, user_id=USER)
    assert record is not None
    asset = next(a for a in record.assets if a.kind == "cutout")
    assert asset.meta is not None
    assert asset.meta["sha256"] == sha256_of(cutout)
    assert asset.byte_size == len(cutout)


def test_expected_sha256_mismatch_writes_nothing(
    repository: SqliteRepository, storage: LocalStorage
) -> None:
    """E3-T4's precondition: a hash that is not the bytes' hash must stop the write dead."""
    item = repository.create_item(user_id=USER)
    with pytest.raises(WritebackError):
        write_back(
            repository,
            storage,
            item_id=item.id,
            user_id=USER,
            assets=[
                AssetWrite(
                    kind="prettified",
                    data=PAYLOAD,
                    content_type="image/png",
                    expected_sha256="0" * 64,
                )
            ],
        )
    record = repository.get_item(item.id, user_id=USER)
    assert record is not None
    assert record.assets == (), "a mismatch must not leave an assets row behind"
    assert not Path(storage.root, f"items/{USER}/{item.id}/prettified.png").exists(), (
        "a mismatch must not leave an object behind either"
    )


def test_expected_sha256_mismatch_on_the_second_asset_writes_neither(
    repository: SqliteRepository, storage: LocalStorage
) -> None:
    """The preflight covers the whole pack: asset #1 must not land when #2 is wrong."""
    item = repository.create_item(user_id=USER)
    good = b"good-bytes"
    with pytest.raises(WritebackError):
        write_back(
            repository,
            storage,
            item_id=item.id,
            user_id=USER,
            assets=[
                AssetWrite(
                    kind="cutout",
                    data=good,
                    content_type="image/png",
                    expected_sha256=sha256_of(good),
                ),
                AssetWrite(
                    kind="prettified",
                    data=PAYLOAD,
                    content_type="image/png",
                    expected_sha256="f" * 64,
                ),
            ],
        )
    record = repository.get_item(item.id, user_id=USER)
    assert record is not None and record.assets == ()
    assert not Path(storage.root, f"items/{USER}/{item.id}/cutout.png").exists()


def test_storage_write_itself_refuses_a_mismatch(storage: LocalStorage) -> None:
    key = f"items/{USER}/{uuid4()}/cutout.png"
    with pytest.raises(BytesMismatch):
        storage.write(key, PAYLOAD, content_type="image/png", expected_sha256="0" * 64)
    assert not Path(storage.root, key).exists()


def test_write_back_is_idempotent_on_replay(
    repository: SqliteRepository, storage: LocalStorage
) -> None:
    item = repository.create_item(user_id=USER)
    assets = [AssetWrite(kind="cutout", data=PAYLOAD, content_type="image/png")]
    write_back(repository, storage, item_id=item.id, user_id=USER, assets=assets)
    write_back(repository, storage, item_id=item.id, user_id=USER, assets=assets)
    record = repository.get_item(item.id, user_id=USER)
    assert record is not None
    assert [a.kind for a in record.assets] == ["cutout"], "one row per slot, always"


def test_write_back_refuses_an_empty_asset(
    repository: SqliteRepository, storage: LocalStorage
) -> None:
    item = repository.create_item(user_id=USER)
    with pytest.raises(WritebackError):
        write_back(
            repository,
            storage,
            item_id=item.id,
            user_id=USER,
            assets=[AssetWrite(kind="cutout", data=b"", content_type="image/png")],
        )


# ---------------------------------------------------------------------------------------
# 5. Idempotency of PUT.
# ---------------------------------------------------------------------------------------
def test_reuploading_the_same_key_keeps_one_row_and_the_latest_bytes(
    client: TestClient, repository: SqliteRepository, storage: LocalStorage
) -> None:
    body, url = new_item(client)
    key = parse_key(url)
    first = upload(client, url, data=b"first-bytes")
    assert first.status_code == 201
    second = upload(client, url, data=b"second-bytes")
    assert second.status_code == 200, "an overwrite is not a creation"

    record = repository.get_item(UUID(body["item_id"]), user_id=USER)
    assert record is not None
    sources = [a for a in record.assets if a.kind == "source"]
    assert len(sources) == 1, "a retry must not create a second source row"
    assert sources[0].meta is not None
    assert sources[0].meta["sha256"] == sha256_of(b"second-bytes")
    assert sources[0].byte_size == len(b"second-bytes")
    assert storage.read(key) == b"second-bytes"


# ---------------------------------------------------------------------------------------
# 6. Signed read.
# ---------------------------------------------------------------------------------------
def test_a_valid_asset_signature_returns_the_original_bytes(
    client: TestClient, repository: SqliteRepository, storage: LocalStorage
) -> None:
    body, upload_url = new_item(client)
    assert upload(client, upload_url).status_code == 201
    key = parse_key(upload_url)
    url = storage.signed_asset_url(key, base_url="http://testserver")
    assert url is not None
    response = client.get(path_of(url))
    assert response.status_code == 200, response.text
    assert response.content == PAYLOAD
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-snaplist-sha256"] == sha256_of(PAYLOAD)


def test_an_unsigned_read_is_403_even_when_the_object_exists(
    client: TestClient, storage: LocalStorage
) -> None:
    key = f"items/{USER}/{uuid4()}/cutout.png"
    storage.write(key, PAYLOAD, content_type="image/png")
    response = client.get(f"/v1/assets/{key}")
    assert response.status_code == 403, response.text


def test_a_valid_signature_for_a_missing_object_is_404(
    client: TestClient, storage: LocalStorage
) -> None:
    key = f"items/{USER}/{uuid4()}/never-written.png"
    url = storage.signed_asset_url(key, base_url="http://testserver")
    assert url is not None
    response = client.get(path_of(url))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------------------
# 7. AssetPack — signed at read time, keys stored.
# ---------------------------------------------------------------------------------------
def test_the_asset_pack_urls_are_usable_and_the_row_keeps_keys(
    client: TestClient, repository: SqliteRepository, storage: LocalStorage
) -> None:
    body, _ = new_item(client)
    item_id = UUID(body["item_id"])
    prettified = b"prettified-bytes"
    cutout = b"cutout-bytes"
    on_model = b"render-bytes"

    write_back(
        repository,
        storage,
        item_id=item_id,
        user_id=USER,
        assets=[
            AssetWrite(kind="prettified", data=prettified, content_type="image/png"),
            AssetWrite(kind="cutout", data=cutout, content_type="image/png"),
            AssetWrite(kind="on_model", data=on_model, content_type="image/png", position=1),
        ],
    )

    record = repository.get_item(item_id, user_id=USER)
    assert record is not None
    stored_key = record.path_for("prettified")
    assert stored_key == f"items/{USER}/{item_id}/prettified.png"
    assert "sig=" not in (stored_key or ""), "storage_path must be a key, never a URL"

    pack = client.get(f"/v1/items/{item_id}", headers=HEADERS).json()
    for field, expected in (
        ("prettified_url", prettified),
        ("cutout_url", cutout),
    ):
        url = pack[field]
        assert isinstance(url, str) and "/v1/assets/" in url and "sig=" in url
        fetched = client.get(path_of(url))
        assert fetched.status_code == 200, (field, fetched.text)
        assert fetched.content == expected
    assert len(pack["on_model_urls"]) == 1
    assert client.get(path_of(pack["on_model_urls"][0])).content == on_model


def test_an_item_with_no_assets_keeps_null_urls(client: TestClient) -> None:
    body, _ = new_item(client)
    pack = client.get(f"/v1/items/{body['item_id']}", headers=HEADERS).json()
    assert pack["prettified_url"] is None
    assert pack["cutout_url"] is None
    assert pack["on_model_urls"] == []


def test_asset_pack_without_a_resolver_returns_raw_keys(
    repository: SqliteRepository, storage: LocalStorage
) -> None:
    """The default of `to_asset_pack` is unchanged: no resolver means the raw key."""
    item = repository.create_item(user_id=USER)
    repository.set_asset(
        item.id, user_id=USER, kind="cutout", storage_path="items/k.png", byte_size=1
    )
    record = repository.get_item(item.id, user_id=USER)
    assert record is not None
    assert record.to_asset_pack().cutout_url == "items/k.png"


# ---------------------------------------------------------------------------------------
# 8. Claim predicate — no bytes, no claim.
# ---------------------------------------------------------------------------------------
def test_a_job_is_not_claimable_until_the_source_asset_exists(
    repository: SqliteRepository,
) -> None:
    item = repository.create_item(user_id=USER)
    job = repository.create_job(item_id=item.id, user_id=USER, kind=JobKind.FULL)
    assert repository.peek_next_queued_job() is None, (
        "POST /v1/items creates the job before the bytes land; claiming it would burn three "
        "attempts on a photo that is not there"
    )

    repository.set_asset(
        item.id,
        user_id=USER,
        kind="source",
        storage_path=source_object_key(USER, item.id, "image/jpeg"),
        byte_size=len(PAYLOAD),
        meta={"sha256": sha256_of(PAYLOAD)},
    )
    claimed = repository.peek_next_queued_job()
    assert claimed is not None and claimed.id == job.id


def test_a_non_source_asset_does_not_unblock_the_claim(
    repository: SqliteRepository,
) -> None:
    item = repository.create_item(user_id=USER)
    repository.create_job(item_id=item.id, user_id=USER, kind=JobKind.FULL)
    repository.set_asset(
        item.id, user_id=USER, kind="prettified", storage_path="items/x/prettified.png"
    )
    assert repository.peek_next_queued_job() is None


# ---------------------------------------------------------------------------------------
# 9. SupabaseStorage — request shape, through MockTransport (no socket is opened).
# ---------------------------------------------------------------------------------------
def supabase_with(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    service_key: str = "service-role-key-not-a-real-one",
) -> SupabaseStorage:
    return SupabaseStorage(
        url="https://project.supabase.co",
        service_key=service_key,
        bucket="snaplist",
        upload_ttl_seconds=900,
        asset_ttl_seconds=3600,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_supabase_signing_a_download_asks_for_the_right_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"signedURL": "/object/sign/snaplist/items/u/i/prettified.png?token=abc"}
        )

    storage = supabase_with(handler)
    url = storage.signed_asset_url("items/u/i/prettified.png")
    assert url == (
        "https://project.supabase.co/storage/v1/object/sign/snaplist/"
        "items/u/i/prettified.png?token=abc"
    )
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == (
        "https://project.supabase.co/storage/v1/object/sign/snaplist/items/u/i/prettified.png"
    )
    assert request.headers["authorization"] == "Bearer service-role-key-not-a-real-one"
    assert request.headers["apikey"] == "service-role-key-not-a-real-one"
    assert json.loads(request.content) == {"expiresIn": 3600}


def test_supabase_signing_an_upload_uses_the_upload_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"url": "/object/upload/sign/snaplist/items/u/i/source.jpg?token=t"}
        )

    storage = supabase_with(handler)
    url = storage.signed_upload_url("items/u/i/source.jpg")
    assert url == (
        "https://project.supabase.co/storage/v1/object/upload/sign/snaplist/"
        "items/u/i/source.jpg?token=t"
    )
    assert "/object/upload/sign/" in str(seen[0].url)
    assert seen[0].method == "POST"


def test_supabase_write_upserts_with_the_measured_bytes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"Key": "snaplist/items/u/i/cutout.png"})

    storage = supabase_with(handler)
    stored = storage.write("items/u/i/cutout.png", PAYLOAD, content_type="image/png")
    assert stored.sha256 == sha256_of(PAYLOAD)
    request = seen[0]
    assert request.method == "PUT"
    assert str(request.url) == (
        "https://project.supabase.co/storage/v1/object/snaplist/items/u/i/cutout.png"
    )
    assert request.headers["x-upsert"] == "true"
    assert request.headers["content-type"] == "image/png"
    assert request.content == PAYLOAD


def test_supabase_read_maps_a_404_onto_MissingObject() -> None:
    storage = supabase_with(lambda request: httpx.Response(404, json={"error": "not found"}))
    with pytest.raises(MissingObject):
        storage.read("items/u/i/absent.png")


def test_supabase_never_verifies_our_own_signatures() -> None:
    """Supabase signs with its own key. Pretending to verify would be the worse bug."""
    storage = supabase_with(lambda request: httpx.Response(200, json={}))
    verified = storage.verify_signature(
        "items/u/i/a.png", purpose="asset", exp=99, sig="deadbeef"
    )
    assert verified is False


# ---------------------------------------------------------------------------------------
# 10. Backend selection and the unconfigured process.
# ---------------------------------------------------------------------------------------
def test_build_storage_picks_the_filesystem_when_nothing_is_configured(tmp_path: Path) -> None:
    settings = Settings(
        env="local", supabase_url="", supabase_service_key="", storage_dir=str(tmp_path)
    )
    built = build_storage(settings)
    assert isinstance(built, LocalStorage)
    assert built.configured is True


def test_build_storage_with_supabase_backend_and_no_key_is_unconfigured() -> None:
    settings = Settings(
        env="local",
        storage_backend="supabase",
        supabase_url="https://project.supabase.co",
        supabase_service_key="",
    )
    built = build_storage(settings)
    assert isinstance(built, UnavailableStorage)
    assert built.configured is False
    assert built.signed_upload_url("items/u/i/source.jpg") is None


def test_supabase_from_settings_names_the_missing_key_without_leaking_it() -> None:
    settings = Settings(
        env="local",
        storage_backend="supabase",
        supabase_url="https://project.supabase.co",
        supabase_service_key="",
    )
    with pytest.raises(StorageUnavailable) as excinfo:
        SupabaseStorage.from_settings(settings)
    assert excinfo.value.missing_credential == "supabase_service_key"
    assert "supabase_service_key" in str(excinfo.value)


def test_an_unconfigured_process_still_creates_items_with_a_null_upload_url(
    repository: SqliteRepository,
) -> None:
    settings = Settings(
        env="local",
        supabase_jwt_secret="",
        storage_backend="supabase",
        supabase_url="https://project.supabase.co",
        supabase_service_key="",
    )
    app = create_app(repository=repository, settings=settings)
    with TestClient(app) as client:
        response = client.post("/v1/items", headers=HEADERS, json={"filename": "tee.jpg"})
        assert response.status_code == 202, response.text
        assert response.json()["upload_url"] is None, "the contract allows null; never fake a URL"

        ready = client.get("/readyz").json()
        assert ready["storage_configured"] is False
        assert "supabase_service_key" in ready["storage"]

        key = f"items/{USER}/{uuid4()}/source.jpg"
        response = client.put(
            f"/v1/uploads/{key}?exp=1&sig=0",
            content=PAYLOAD,
            headers={"content-type": "image/jpeg"},
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["details"]["missing_credential"] == "supabase_service_key"


def test_storage_protocol_is_satisfied_by_both_backends(
    storage: LocalStorage,
) -> None:
    assert isinstance(storage, Storage)
    assert isinstance(
        SupabaseStorage(url="https://x.supabase.co", service_key="k"), Storage
    )
    assert isinstance(UnavailableStorage(), Storage)


# ---------------------------------------------------------------------------------------
# Local-storage unit behaviour that has no HTTP surface.
# ---------------------------------------------------------------------------------------
def test_local_storage_rejects_a_bad_content_type_and_an_empty_object(
    storage: LocalStorage,
) -> None:
    key = f"items/{USER}/{uuid4()}/x.png"
    with pytest.raises(UnsupportedContentType) as bad_type:
        storage.write(key, PAYLOAD, content_type="application/zip")
    assert "image/" in str(bad_type.value)
    with pytest.raises(InvalidObject):
        storage.write(key, b"", content_type="image/png")


def test_local_storage_rejects_an_oversized_object(storage: LocalStorage) -> None:
    key = f"items/{USER}/{uuid4()}/x.png"
    with pytest.raises(ObjectTooLarge):
        storage.write(key, b"x" * (storage.max_upload_bytes + 1), content_type="image/png")


def test_missing_object_read_raises(storage: LocalStorage) -> None:
    with pytest.raises(MissingObject):
        storage.read(f"items/{USER}/{uuid4()}/absent.png")


def test_upload_ttl_and_asset_ttl_are_the_configured_values(storage: LocalStorage) -> None:
    """The two TTLs the docs quote: 900 s upload, 3600 s asset."""
    key = f"items/{USER}/{uuid4()}/source.jpg"
    upload_exp = int(query_of(storage.signed_upload_url(key) or "")["exp"])
    asset_exp = int(query_of(storage.signed_asset_url(key) or "")["exp"])
    assert upload_exp - int(storage.clock()) == 900
    assert asset_exp - int(storage.clock()) == 3600


def test_a_signed_url_survives_a_process_restart_only_with_a_configured_secret(
    tmp_path: Path,
) -> None:
    """Ephemeral secrets are the honest local default; a configured one is stable."""
    first = LocalStorage(tmp_path / "a")
    second = LocalStorage(tmp_path / "a")
    assert first.secret != second.secret, "no hard-coded fallback key, ever"

    configured = Settings(env="local", storage_dir=str(tmp_path / "b"), storage_signing_secret="s")
    one = build_storage(configured)
    two = build_storage(configured)
    assert isinstance(one, LocalStorage) and isinstance(two, LocalStorage)
    assert one.secret == two.secret
    assert one.secret == b"s"


def test_storage_errors_never_leak_the_secret(
    repository: SqliteRepository, storage: LocalStorage
) -> None:
    try:
        storage.write(f"items/{USER}/{uuid4()}/x.png", PAYLOAD, expected_sha256="0" * 64,
                      content_type="image/png")
    except StorageError as exc:
        assert storage.secret.decode(errors="ignore") not in str(exc)
    else:  # pragma: no cover - the write above must fail
        pytest.fail("expected BytesMismatch")


def test_datetime_now_is_used_only_for_metadata(tmp_path: Path) -> None:
    """`created_at` on a StoredObject comes from the injected clock, not from `utcnow()`."""
    fixed = FakeClock()
    storage = LocalStorage(tmp_path / "meta", secret=b"x" * 32, clock=fixed)
    key = f"items/{USER}/{uuid4()}/source.jpg"
    stored = storage.write(key, PAYLOAD, content_type="image/jpeg")
    assert stored.created_at == datetime.fromtimestamp(fixed.now, UTC)


def test_an_old_sqlite_file_gets_the_new_asset_columns(tmp_path: Path) -> None:
    """`make dev` keeps one database across checkouts, and `create table if not exists` does
    not add a column. Without the in-place ALTER, the first upload on a pre-E5-T4 file dies."""
    database = tmp_path / "old.sqlite"
    connection = sqlite3.connect(str(database))
    connection.executescript(
        "create table items (id text primary key, user_id text not null, status text not null,"
        " attributes text, deleted_at text, created_at text not null, updated_at text not null);"
        "create table assets (item_id text not null references items (id) on delete cascade,"
        " user_id text not null, kind text not null, position integer not null default 0,"
        " storage_path text not null, created_at text not null,"
        " primary key (item_id, kind, position));"
    )
    connection.commit()
    connection.close()

    store = SqliteRepository(str(database))
    try:
        item = store.create_item(user_id=USER)
        store.set_asset(
            item.id,
            user_id=USER,
            kind="source",
            storage_path="items/k.jpg",
            byte_size=len(PAYLOAD),
            meta={"sha256": sha256_of(PAYLOAD)},
        )
        record = store.get_item(item.id, user_id=USER)
        assert record is not None
        source = next(a for a in record.assets if a.kind == "source")
        assert source.byte_size == len(PAYLOAD)
        assert source.meta is not None and source.meta["sha256"] == sha256_of(PAYLOAD)
    finally:
        store.close()


def test_a_signed_read_advertises_only_the_signatures_remaining_life(
    client: TestClient, storage: LocalStorage, clock: FakeClock
) -> None:
    key = f"items/{USER}/{uuid4()}/cutout.png"
    storage.write(key, PAYLOAD, content_type="image/png")
    url = storage.signed_asset_url(key, base_url="http://testserver")
    assert url is not None
    clock.advance(3600 - 60)  # one minute left
    response = client.get(path_of(url))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=60"


# ---------------------------------------------------------------------------------------
# 11. QA round 2 — three behaviours that had no regression guard, and one inconsistency.
#
# Every test name below corresponds to a defect a reviewer demonstrated and could then fix
# without a single test turning red. That is the definition of a missing guard: the bug was
# reachable, the tests were green, and nothing connected the two.
# ---------------------------------------------------------------------------------------
def _files_under(root: Path) -> list[Path]:
    """Every regular file below `root`, sorted. The disk, not the database."""
    base = Path(root)
    if not base.exists():
        return []
    return sorted(path for path in base.rglob("*") if path.is_file())


# -- A. a client-supplied signature must never produce a 5xx -----------------------------
def test_a_non_ascii_signature_is_403_not_503_on_upload(client: TestClient) -> None:
    """`hmac.compare_digest` raises TypeError on a non-ASCII str.

    Before this guard, `?sig=%C3%A9` on an unauthenticated route escaped the handler as a 503
    with a full ASGI traceback in the log — a client-triggerable 5xx exactly where every bad
    signature is supposed to be an indistinguishable 403.
    """
    body, url = new_item(client)
    key = parse_key(url)
    response = client.put(
        f"/v1/uploads/{key}?exp=9999999999&sig=%C3%A9",
        content=PAYLOAD,
        headers={"content-type": "image/jpeg"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


def test_a_non_ascii_signature_is_403_not_503_on_read(client: TestClient) -> None:
    body, _ = new_item(client)
    key = f"items/{USER}/{body['item_id']}/prettified.png"
    response = client.get(f"/v1/assets/{key}?exp=9999999999&sig=%C3%A9")
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


def test_a_signature_with_a_trailing_space_is_403(client: TestClient) -> None:
    """`?sig=<valid>%20` used to be `.strip()`ed back into a valid signature and accepted.

    A value the route never accepted as-is must not be repaired into one it does: the shape
    check rejects the 65-character string rather than trimming it back to 64.
    """
    _, url = new_item(client)
    response = client.put(
        f"{path_of(url)}%20", content=PAYLOAD, headers={"content-type": "image/jpeg"}
    )
    assert response.status_code == 403, response.text


def test_verify_signature_returns_false_for_any_non_hex_value(storage: LocalStorage) -> None:
    """The unit-level half of A: no input to `verify_signature` may raise."""
    key = f"items/{USER}/{uuid4()}/source.jpg"
    exp = int(storage.clock()) + 900
    for candidate in ("é", "A" * 64, "0" * 63, "0" * 65, " " * 64, "g" * 64, ""):
        assert storage.verify_signature(key, purpose="upload", exp=exp, sig=candidate) is False
    assert storage.verify_signature(key, purpose="upload", exp=exp, sig=None) is False


# -- B. write_back's "all or nothing" must cover the disk, not only the digests -----------
def test_a_kind_that_escapes_the_root_writes_nothing_at_all(
    repository: SqliteRepository, storage: LocalStorage
) -> None:
    """The reviewer's repro: asset #1 is valid, asset #2's `kind` is a traversal.

    Before the fix, #1 was already on disk when #2 was refused — an orphan object with no row
    pointing at it, which nothing ever cleans up. The keys are now planned and validated
    before the first write.
    """
    item = repository.create_item(user_id=USER)
    with pytest.raises(WritebackError):
        write_back(
            repository,
            storage,
            item_id=item.id,
            user_id=USER,
            assets=[
                AssetWrite(kind="cutout", data=b"first-asset", content_type="image/png"),
                AssetWrite(kind="../../escape", data=b"second-asset", content_type="image/png"),
            ],
        )
    assert _files_under(storage.root) == [], "a refused write-back must leave zero objects"
    record = repository.get_item(item.id, user_id=USER)
    assert record is not None and record.assets == ()


def test_a_kind_with_a_path_separator_is_refused(
    repository: SqliteRepository, storage: LocalStorage
) -> None:
    """`kind="a/b"` yields a *valid* key one directory deeper, which validate_key cannot see."""
    item = repository.create_item(user_id=USER)
    with pytest.raises(WritebackError):
        write_back(
            repository,
            storage,
            item_id=item.id,
            user_id=USER,
            assets=[AssetWrite(kind="a/b", data=b"x", content_type="image/png")],
        )
    assert _files_under(storage.root) == []


# -- C1. an unauthenticated PUT must not learn whether an item exists ---------------------
def test_an_unsigned_put_at_an_unknown_item_key_is_403_not_404(client: TestClient) -> None:
    """403 for every unauthenticated request, even one aimed at a key that does not exist.

    If the item lookup ran before the signature check, a 404/403 split would tell an
    unauthenticated caller whether an item id is real — an existence oracle on the one route
    that is deliberately open.
    """
    ghost = f"items/{USER}/{uuid4()}/source.jpg"
    response = client.put(
        f"/v1/uploads/{ghost}", content=PAYLOAD, headers={"content-type": "image/jpeg"}
    )
    assert response.status_code == 403, response.text
    # ...and nothing in the body may confirm or deny that the item exists.
    assert "no item" not in response.text.lower()
    assert uuid_str_not_in(response.text, ghost)


def uuid_str_not_in(text: str, key: str) -> bool:
    """The item id inside `key` must not be echoed back on a refused request."""
    return key.split("/")[2] not in text


def test_a_signed_put_at_an_unknown_item_key_is_404(
    client: TestClient, storage: LocalStorage
) -> None:
    """The flip side: *with* a valid signature a missing item is a 404, and that is fine —
    only this process can mint that signature."""
    ghost = f"items/{USER}/{uuid4()}/source.jpg"
    response = client.put(
        path_of(signed_path(storage, ghost)), content=PAYLOAD,
        headers={"content-type": "image/jpeg"},
    )
    assert response.status_code == 404, response.text


# -- C2. the expiry boundary, pinned ------------------------------------------------------
def test_the_expiry_boundary_is_inclusive(storage: LocalStorage, clock: FakeClock) -> None:
    """`exp == now` is still valid; `exp + 1` is not.

    Chosen deliberately (see `LocalStorage.verify_signature`'s docstring): `exp` is
    `int(clock()) + ttl`, and `int()` already truncates up to a second off the configured TTL,
    so making the boundary exclusive as well would subtract that second twice. Either choice
    is defensible; leaving it unpinned is not — flipping `>` to `>=` used to change behaviour
    with the suite still green.
    """
    key = f"items/{USER}/{uuid4()}/source.jpg"
    exp = int(clock()) + 900
    sig = sign(storage.secret, purpose="upload", key=key, exp=exp)

    clock.now = float(exp)
    assert storage.verify_signature(key, purpose="upload", exp=exp, sig=sig) is True

    clock.now = float(exp + 1)
    assert storage.verify_signature(key, purpose="upload", exp=exp, sig=sig) is False


# -- D. a processed item must not accept a new source photo --------------------------------
def _finish_job(repository: SqliteRepository, job_id: UUID, user_id: UUID) -> None:
    job = repository.get_job(job_id, user_id=user_id)
    assert job is not None
    running = apply_transition(repository, job, to_state=JobState.RUNNING, claimed_by="test")
    apply_transition(repository, running, to_state=JobState.DONE)


def test_a_finished_item_refuses_a_new_source_photo_with_409(
    client: TestClient, repository: SqliteRepository, storage: LocalStorage
) -> None:
    """The reviewer's repro: job `done`, then a PUT inside the 900 s TTL returned 200 and
    replaced the source while the renders still described the old photo."""
    body, url = new_item(client)
    key = parse_key(url)
    item_id = UUID(body["item_id"])
    first = b"photo-number-one"
    assert upload(client, url, data=first).status_code == 201
    _finish_job(repository, UUID(body["job_id"]), USER)

    response = upload(client, url, data=b"photo-number-two")
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert "new item" in error["message"], "the 409 must tell the client what to do instead"
    assert error["details"]["item_id"] == str(item_id)

    # Bytes and metadata unchanged — the refusal is not cosmetic.
    assert storage.read(key) == first
    record = repository.get_item(item_id, user_id=USER)
    assert record is not None
    source = next(a for a in record.assets if a.kind == "source")
    assert source.meta is not None
    assert source.meta["sha256"] == sha256_of(first)
    assert source.byte_size == len(first)


def test_an_unprocessed_item_still_accepts_a_replayed_upload(
    client: TestClient, repository: SqliteRepository
) -> None:
    """`queued` / `running` / `failed` are not `done`: a retry must stay idempotent."""
    body, url = new_item(client)
    job_id = UUID(body["job_id"])

    assert upload(client, url, data=b"queued-bytes").status_code == 201

    job = repository.get_job(job_id, user_id=USER)
    assert job is not None
    running = apply_transition(repository, job, to_state=JobState.RUNNING, claimed_by="test")
    assert upload(client, url, data=b"running-bytes").status_code == 200

    fail(repository, running, reason_code=ReasonCode.INTERNAL, error_detail="stage missing")
    assert upload(client, url, data=b"failed-bytes").status_code == 200


def test_devtools_refuses_a_storage_path_that_is_not_an_object_key(tmp_path: Path) -> None:
    """`devtools attach-asset --path https://…` used to write a URL into `storage_path`,
    after which `GET /v1/items/{id}` failed while signing (503 from a dev tool that looked
    like it had worked). The CLI now refuses the path instead."""
    from argparse import Namespace

    from apps.api.devtools import attach_asset

    args = Namespace(
        db=str(tmp_path / "devtools.sqlite"),
        item=str(USER),
        user=str(USER),
        kind="prettified",
        path="https://example.test/p.png",
        position=0,
    )
    assert attach_asset(args) == 2
    assert not (tmp_path / "devtools.sqlite").exists(), (
        "the path is validated before the database is opened"
    )


def test_devtools_accepts_a_real_object_key(tmp_path: Path) -> None:
    from argparse import Namespace

    from apps.api.devtools import attach_asset
    from apps.api.repository import build_repository

    database = tmp_path / "devtools-ok.sqlite"
    seed = build_repository(str(database))
    item = seed.create_item(user_id=USER)
    seed.close()

    key = f"items/{USER}/{item.id}/prettified.png"
    args = Namespace(
        db=str(database),
        item=str(item.id),
        user=str(USER),
        kind="prettified",
        path=key,
        position=0,
    )
    assert attach_asset(args) == 0
    check = build_repository(str(database))
    try:
        record = check.get_item(item.id, user_id=USER)
        assert record is not None
        assert record.path_for("prettified") == key
    finally:
        check.close()


def test_a_kind_that_overflows_the_key_limit_writes_nothing_at_all(
    repository: SqliteRepository, storage: LocalStorage
) -> None:
    """The one input only `preflight`'s `validate_key(key)` guards.

    The `kind` separator check catches `../..`; a 190-character `kind` has no separator and is
    not `.`/`..`, so it sails past every other check and only overflows the **key** (274 > 200
    characters). Without that validation, asset #1 is on disk before asset #2's key is refused
    — the same orphan bug through a different door. `M13` (delete the `validate_key` call) used
    to turn zero tests red; this is the test that closes it.
    """
    item = repository.create_item(user_id=USER)
    with pytest.raises(WritebackError):
        write_back(
            repository,
            storage,
            item_id=item.id,
            user_id=USER,
            assets=[
                AssetWrite(kind="cutout", data=b"first-asset", content_type="image/png"),
                AssetWrite(kind="a" * 190, data=b"second-asset", content_type="image/png"),
            ],
        )
    assert _files_under(storage.root) == [], "an over-long key must not leave asset #1 behind"
    record = repository.get_item(item.id, user_id=USER)
    assert record is not None and record.assets == ()


def test_rows_are_written_only_after_the_bytes_exist(
    repository: SqliteRepository,
    storage: LocalStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`write_back` writes rows **last**, and this is what makes that claim testable.

    A row is the promise that the object is there. If the loops were swapped, an `AssetPack`
    fetched in between would carry a signed URL for an object that does not exist yet — a 404
    on a pack the client was just told was ready. Nothing used to distinguish the two orders
    (`M6b`: swap the loops → 0 red), so the hook below asks the only question that matters at
    the moment `set_asset` runs: are the bytes already readable?
    """
    item = repository.create_item(user_id=USER)
    observed: list[bool] = []
    original = repository.set_asset

    def checking_set_asset(item_id: UUID, **kwargs: Any) -> None:
        observed.append(storage.exists(kwargs["storage_path"]))
        original(item_id, **kwargs)

    monkeypatch.setattr(repository, "set_asset", checking_set_asset)

    write_back(
        repository,
        storage,
        item_id=item.id,
        user_id=USER,
        assets=[
            AssetWrite(kind="cutout", data=b"cutout", content_type="image/png"),
            AssetWrite(kind="prettified", data=b"prettified", content_type="image/png"),
        ],
    )
    assert observed == [True, True], (
        "every row must be written after its object exists; a False here means the row came "
        "first and published a dangling URL"
    )
