"""E5-T4 — object storage: a `Storage` protocol, a filesystem backend, a Supabase backend.

Why a protocol, again
---------------------
`apps/api/repository.py` set the pattern this module follows: the slice must run with **zero
credentials**, so the concrete backend a route talks to is a `Protocol`, not a hard-wired
client. `LocalStorage` (filesystem + HMAC) makes `make test` and `make dev` work with an empty
`.env`; `SupabaseStorage` implements the same protocol against the hosted service and **no
router changes**. The one difference the client can observe is the URL a signed upload points
at, and that is the point of the seam.

Signed URLs are minted at read time, never stored
-------------------------------------------------
`assets.storage_path` holds the **canonical object key**
(`items/<user_id>/<item_id>/prettified.png`), and the three URL fields on `AssetPack` are
signed on the way out. The reason is not tidiness: a signed URL has a TTL (900 s for upload,
3600 s for download in this build), and a mobile client caches the whole `AssetPack` on disk.
A URL written into a row is a dead link the next morning, and the row cannot be re-signed
without a migration. Storing the key means the item row never rots and the TTL is a
configuration value instead of a data-cleaning job.

What the signature covers, and why all three parts
--------------------------------------------------
The signed message is ``v1:<purpose>:<key>:<exp>``, HMAC-SHA256, hex, compared with
`hmac.compare_digest`.

* `key` — otherwise a signature issued for one object authorises every object;
* `exp` — otherwise "expires" is a promise rather than a property;
* `purpose` (`upload` / `asset`) — an upload signature must not read bytes back, and a
  download signature must not overwrite an object. Two capabilities, two signatures.

Signature verification, not a bearer token, is the authorisation for these two routes: the
signature is only issuable by this process (it holds the secret) and only for a key the
process chose. `apps/api/routers/uploads.py` therefore takes **no** `Authorization` header; a
signed PUT is a capability URL, which is exactly what a mobile client on a bad connection can
retry without a token refresh dance.

Zero-credential operation, stated honestly
------------------------------------------
`LocalStorage` uses `SNAPLIST_STORAGE_SIGNING_SECRET` when it is set. When it is not, it
generates 32 random bytes **for the life of the process**: `make test` and `make dev` run with
no configuration, and the honest consequence is that local signed URLs stop working across a
`uvicorn --reload` restart. That is documented rather than papered over — the alternative
(deriving a "default" secret) would be a hard-coded key, which is strictly worse.

No backend here ever invents bytes: `write()` stores exactly what it was given (no transcode,
no EXIF stripping), and the `sha256`/`byte_size` it reports are computed from the bytes it
wrote — never copied from what a caller claimed.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import httpx

from apps.api import errors
from apps.api.settings import Settings

# ---------------------------------------------------------------------------------------
# Vocabulary — names that appear on the wire or in a test assertion, defined once.
# ---------------------------------------------------------------------------------------
PURPOSE_UPLOAD = "upload"
PURPOSE_ASSET = "asset"
PURPOSES = frozenset({PURPOSE_UPLOAD, PURPOSE_ASSET})

SIGNATURE_VERSION = "v1"
SIGNATURE_ALGORITHM = "sha256"

#: The four formats a phone camera produces that this pipeline accepts. A content-type outside
#: this set is a 415 at the route and a refusal here: "image/svg+xml" is a script carrier, and
#: "application/octet-stream" tells the later stages nothing about how to decode.
ALLOWED_CONTENT_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/heic"}
)

EXTENSION_BY_CONTENT_TYPE: Mapping[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
}

CONTENT_TYPE_BY_EXTENSION: Mapping[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "heic": "image/heic",
}

#: Object keys are a security boundary, not a formatting choice: they become filesystem paths
#: in `LocalStorage`. 200 characters is the cap; see `validate_key` for the rest.
MAX_KEY_LENGTH = 200

#: Default ceilings. Both are also `Settings` values so an operator can change them without a
#: release; these are the values a bare `LocalStorage()` uses.
DEFAULT_UPLOAD_TTL_SECONDS = 900
DEFAULT_ASSET_TTL_SECONDS = 3600
DEFAULT_MAX_UPLOAD_BYTES = 20_000_000

#: Supabase bucket. The key already carries `items/`, so the bucket is the outer namespace.
DEFAULT_SUPABASE_BUCKET = "snaplist"


# ---------------------------------------------------------------------------------------
# Refusals. Named, so a router can map each onto exactly one status code.
# ---------------------------------------------------------------------------------------
class StorageError(RuntimeError):
    """Base class for every storage-level refusal."""


class StorageUnavailable(StorageError):
    """The backend cannot serve this process (missing credential, provider down).

    `missing_credential` is the *config name*, never a value — the same convention as
    `apps/api/errors.DETAIL_MISSING_CREDENTIAL`.
    """

    def __init__(self, message: str, *, missing_credential: str | None = None) -> None:
        super().__init__(message)
        self.missing_credential = missing_credential


class InvalidKey(StorageError):
    """An object key that is not a safe relative path. Always a 400, never a 403."""


class InvalidObject(StorageError):
    """A byte payload the backend refuses on its own terms (empty, wrong type)."""


class UnsupportedContentType(InvalidObject):
    """A content-type outside `ALLOWED_CONTENT_TYPES`."""


class ObjectTooLarge(InvalidObject):
    """A payload larger than the configured ceiling."""


class MissingObject(StorageError):
    """A well-formed key with no object behind it. A 404, and only ever after a good signature."""


class BytesMismatch(StorageError):
    """The bytes do not hash to what the caller said they would. Nothing is written."""


# ---------------------------------------------------------------------------------------
# Key rules — one function, used by every backend, so "safe" cannot drift between them.
# ---------------------------------------------------------------------------------------
def validate_key(key: str) -> str:
    """Return `key` unchanged, or raise `InvalidKey`.

    Rejected, each for a stated reason:

    * empty / longer than 200 — an unbounded key is an unbounded filename;
    * a leading or trailing `/` — would address the bucket root instead of an object;
    * `//` — POSIX collapses it, so `a//b` and `a/b` would be the same object with two keys,
      and an allow-list that checks one spelling would miss the other;
    * `\\` — a path separator on Windows and a directory-traversal primitive on some servers;
    * a `.` or `..` segment — `..` is the traversal itself; `.` is the same object by a second
      name, which defeats idempotency;
    * any control character (< 0x20 or 0x7f) — `\\x00` truncates a C string and a newline
      forges a log line.
    """
    if not key:
        raise InvalidKey("Object key must not be empty.")
    if len(key) > MAX_KEY_LENGTH:
        raise InvalidKey(f"Object key exceeds {MAX_KEY_LENGTH} characters.")
    if key.startswith("/"):
        raise InvalidKey("Object key must be relative (no leading '/').")
    if key.endswith("/"):
        raise InvalidKey("Object key must name an object, not a prefix (no trailing '/').")
    if "\\" in key:
        raise InvalidKey("Object key must use '/' as its only separator.")
    if "//" in key:
        raise InvalidKey("Object key must not contain an empty segment ('//').")
    if any(character < " " or character == "\x7f" for character in key):
        raise InvalidKey("Object key must not contain control characters.")
    for segment in key.split("/"):
        if segment in {".", ".."}:
            raise InvalidKey("Object key must not contain '.' or '..' segments.")
    return key


def source_object_key(user_id: UUID, item_id: UUID, content_type: str | None = None) -> str:
    """Where the uploaded original lives: `items/<user>/<item>/source.<ext>`.

    The extension is cosmetic (the authoritative type is recorded in `assets.meta`), but it
    keeps a bucket listing readable — which is the whole job of a path.
    """
    extension = EXTENSION_BY_CONTENT_TYPE.get((content_type or "").lower(), "")
    suffix = f".{extension}" if extension else ""
    return f"items/{user_id}/{item_id}/source{suffix}"


def asset_object_key(
    user_id: UUID, item_id: UUID, kind: str, position: int = 0, content_type: str = "image/png"
) -> str:
    """Where a pipeline output lives: `items/<user>/<item>/<kind>[-<position>].<ext>`."""
    extension = EXTENSION_BY_CONTENT_TYPE.get(content_type.lower(), "bin")
    slot = kind if position == 0 else f"{kind}-{position}"
    return f"items/{user_id}/{item_id}/{slot}.{extension}"


def parse_item_key(key: str) -> tuple[UUID, UUID] | None:
    """Split `items/<user>/<item>/<name>` into `(user_id, item_id)`, or None.

    Returns None rather than raising: the upload route uses this to decide whether an object
    should also get an `assets` row, and a key that is not item-shaped (a future prefix, a
    test fixture) is stored without one instead of failing the upload.
    """
    parts = key.split("/")
    if len(parts) != 4 or parts[0] != "items":
        return None
    try:
        return UUID(parts[1]), UUID(parts[2])
    except ValueError:
        return None


def content_type_for_key(key: str) -> str:
    """Best-effort media type from the extension. Deterministic; no `mimetypes` file lookup."""
    suffix = key.rsplit(".", 1)[-1].lower() if "." in key.rsplit("/", 1)[-1] else ""
    return CONTENT_TYPE_BY_EXTENSION.get(suffix, "application/octet-stream")


# ---------------------------------------------------------------------------------------
# Signing primitives.
# ---------------------------------------------------------------------------------------
def signature_message(*, purpose: str, key: str, exp: int) -> bytes:
    """The exact bytes that get signed. One function, so sign and verify cannot disagree."""
    if purpose not in PURPOSES:
        raise ValueError(f"unknown signature purpose {purpose!r}")
    return f"{SIGNATURE_VERSION}:{purpose}:{key}:{exp}".encode()


def sign(secret: bytes, *, purpose: str, key: str, exp: int) -> str:
    """HMAC-SHA256 over `signature_message`, hex encoded."""
    return hmac.new(secret, signature_message(purpose=purpose, key=key, exp=exp),
                    hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class StoredObject:
    """What was actually written. Every number here is measured, never accepted from a caller."""

    key: str
    byte_size: int
    sha256: str
    content_type: str
    created_at: datetime


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: A signature this process issues is a lowercase hex SHA-256 digest: exactly 64 characters in
#: `[0-9a-f]`. Anything else cannot be one of ours, and — the reason this exists — comparing a
#: `str` that contains non-ASCII with `hmac.compare_digest` raises `TypeError`. On an
#: unauthenticated route (`?sig=` comes from the network) that `TypeError` used to escape the
#: handler as a 503 with a full ASGI traceback in the log: a client-triggerable 5xx on the one
#: surface where every bad signature is supposed to be an indistinguishable 403.
SIGNATURE_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def is_signature_shaped(sig: str | None) -> bool:
    """True when `sig` is even a candidate: 64 lowercase hex characters, nothing else."""
    if not isinstance(sig, str) or len(sig) != 64:
        return False
    return SIGNATURE_PATTERN.fullmatch(sig) is not None


# ---------------------------------------------------------------------------------------
# The protocol every backend implements.
# ---------------------------------------------------------------------------------------
@runtime_checkable
class Storage(Protocol):
    """The whole storage surface the API and the worker need."""

    configured: bool
    max_upload_bytes: int
    upload_ttl_seconds: int
    asset_ttl_seconds: int

    def describe(self) -> str:
        """Short human label for `/readyz`. Never a secret, never a full path."""
        ...

    def signed_upload_url(self, key: str, *, base_url: str = "") -> str | None:
        """A URL the client may `PUT` bytes to, or None when the backend cannot issue one."""
        ...

    def signed_asset_url(self, key: str, *, base_url: str = "") -> str | None:
        """A URL the client may `GET` bytes from, or None."""
        ...

    def verify_signature(
        self, key: str, *, purpose: str, exp: int | None, sig: str | None
    ) -> bool:
        """True only for a live signature over this exact key and purpose."""
        ...

    def write(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        expected_sha256: str | None = None,
    ) -> StoredObject:
        """Store `data` verbatim and report what was stored."""
        ...

    def read(self, key: str) -> bytes:
        """The stored bytes. Raises `MissingObject` when there are none."""
        ...

    def exists(self, key: str) -> bool: ...


# ---------------------------------------------------------------------------------------
# LocalStorage — filesystem + HMAC. The backend that makes a zero-credential run possible.
# ---------------------------------------------------------------------------------------
class LocalStorage:
    """Objects under one directory; signatures from one secret.

    The HTTP face of this backend is `apps/api/routers/uploads.py`: that module verifies the
    signature and then calls `read`/`write` here, so the route and the backend cannot drift
    on what a valid signature means.

    Writes are **atomic**: bytes go to a sibling temp file and are `os.replace`d into place,
    so a client that dies mid-upload cannot leave a truncated object that a later job would
    happily process. Re-uploading the same key replaces the object and keeps exactly one
    `assets` row (the row is keyed by `(item_id, kind, position)`), and the row's sha256/
    byte_size are then the *latest* bytes — see `apps/api/routers/uploads.py`.
    """

    #: Where objects live. Created lazily on first write, so `/readyz` touches nothing.
    root: Path
    #: HMAC key. Random per process when `SNAPLIST_STORAGE_SIGNING_SECRET` is unset.
    secret: bytes
    upload_ttl_seconds: int
    asset_ttl_seconds: int
    max_upload_bytes: int
    clock: Callable[[], float]
    configured: bool

    def __init__(
        self,
        root: str | Path,
        *,
        secret: bytes | None = None,
        upload_ttl_seconds: int = DEFAULT_UPLOAD_TTL_SECONDS,
        asset_ttl_seconds: int = DEFAULT_ASSET_TTL_SECONDS,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root)
        # Per-process secret when none is configured — see the module docstring. Never a
        # hard-coded fallback: a published default key signs everything for everybody.
        self.secret = secret if secret is not None else secrets.token_bytes(32)
        self.upload_ttl_seconds = upload_ttl_seconds
        self.asset_ttl_seconds = asset_ttl_seconds
        self.max_upload_bytes = max_upload_bytes
        self.clock = clock
        self.configured = True

    # -- plumbing ----------------------------------------------------------------------
    def describe(self) -> str:
        return "LocalStorage"

    def _root(self) -> Path:
        """The storage directory, created on first use.

        Created lazily on purpose: importing the app or calling `/readyz` must not touch the
        filesystem. `is_dir()` before `mkdir` rather than `exist_ok=True`, which raises
        `FileExistsError` on some Python builds when two processes race.
        """
        if not self.root.is_dir():
            self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def _path(self, key: str) -> Path:
        """Resolve `key` to a path that is provably inside the root."""
        validate_key(key)
        root = self._root().resolve()
        candidate = (root / key).resolve()
        if not candidate.is_relative_to(root):
            # Unreachable while `validate_key` holds, and kept as a second lock: a future
            # relaxation of the key rules must not silently become a write outside the root.
            raise InvalidKey("Object key resolves outside the storage root.")
        return candidate

    # -- urls --------------------------------------------------------------------------
    def signed_upload_url(self, key: str, *, base_url: str = "") -> str | None:
        return self._signed_url(key, PURPOSE_UPLOAD, self.upload_ttl_seconds, base_url)

    def signed_asset_url(self, key: str, *, base_url: str = "") -> str | None:
        return self._signed_url(key, PURPOSE_ASSET, self.asset_ttl_seconds, base_url)

    def _signed_url(self, key: str, purpose: str, ttl: int, base_url: str) -> str:
        validate_key(key)
        exp = int(self.clock()) + ttl
        sig = sign(self.secret, purpose=purpose, key=key, exp=exp)
        route = "uploads" if purpose == PURPOSE_UPLOAD else "assets"
        prefix = base_url.rstrip("/")
        return f"{prefix}/v1/{route}/{key}?exp={exp}&sig={sig}"

    def verify_signature(
        self, key: str, *, purpose: str, exp: int | None, sig: str | None
    ) -> bool:
        """Five independent checks; any failure is `False`, and `False` is a 403 everywhere.

        The order matters:

        1. a missing `exp`/`sig` or an unknown purpose is False (nothing to compare);
        2. **shape** — `sig` must be 64 lowercase hex characters *before* it is compared.
           `hmac.compare_digest` raises `TypeError` on a non-ASCII `str`, which on this
           unauthenticated route would have been a 503 with a traceback in the log;
        3. expiry is **inclusive**: a signature whose `exp` equals the current second is still
           valid, and it dies at `exp + 1`. `exp` is computed as `int(clock()) + ttl`, and
           `int()` already truncates up to a second off the configured TTL — making the
           boundary exclusive as well would subtract that second twice;
        4. the signature must match this exact `key` and `purpose` (both are in the message);
        5. the comparison is constant time.
        """
        if exp is None or purpose not in PURPOSES:
            return False
        candidate: str = sig if isinstance(sig, str) else ""
        if not is_signature_shaped(candidate):
            return False
        if int(self.clock()) > exp:
            return False
        try:
            expected = sign(self.secret, purpose=purpose, key=key, exp=exp)
        except ValueError:
            return False
        return hmac.compare_digest(expected, candidate)

    # -- bytes -------------------------------------------------------------------------
    def write(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        expected_sha256: str | None = None,
    ) -> StoredObject:
        """Store `data` byte for byte. Refuses anything questionable *before* touching disk."""
        normalized = (content_type or "").split(";", 1)[0].strip().lower()
        if normalized not in ALLOWED_CONTENT_TYPES:
            raise UnsupportedContentType(
                f"content-type {normalized or 'unset'!r} is not one of "
                f"{', '.join(sorted(ALLOWED_CONTENT_TYPES))}."
            )
        if not data:
            raise InvalidObject("Refusing to store an empty object.")
        if len(data) > self.max_upload_bytes:
            raise ObjectTooLarge(
                f"Payload is {len(data)} bytes; the ceiling is {self.max_upload_bytes}."
            )
        digest = sha256_of(data)
        if expected_sha256 is not None and not hmac.compare_digest(expected_sha256, digest):
            # Before the file exists: a mismatch must leave nothing behind, not a bad object
            # plus a row that points at it.
            raise BytesMismatch(
                f"Bytes hash to {digest}, but the caller expected {expected_sha256}."
            )
        path = self._path(key)
        if not path.parent.is_dir():
            path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.part")
        temporary.write_bytes(data)
        os.replace(temporary, path)
        return StoredObject(
            key=key,
            byte_size=len(data),
            sha256=digest,
            content_type=normalized,
            created_at=datetime.fromtimestamp(self.clock(), UTC),
        )

    def read(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise MissingObject(key)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> bool:
        """Remove one object. Used by tests and by an operator cleaning an orphan."""
        path = self._path(key)
        if not path.is_file():
            return False
        path.unlink()
        return True


# ---------------------------------------------------------------------------------------
# SupabaseStorage — the hosted backend. Same protocol, different transport.
# ---------------------------------------------------------------------------------------
class SupabaseStorage:
    """Supabase Storage through the documented REST API.

    Three calls, all authenticated with the **service** key (never the anon key — an anon key
    cannot sign, and using one here would produce 400s that look like a client bug):

    * `POST /storage/v1/object/upload/sign/<bucket>/<key>` — a signed upload URL. The client
      PUTs straight to Supabase, so the bytes never transit this API process.
    * `POST /storage/v1/object/sign/<bucket>/<key>` with `{"expiresIn": <seconds>}` — a signed
      download URL for the TTL in `Settings`.
    * `PUT /storage/v1/object/<bucket>/<key>` with `x-upsert: true` — the write-back path used
      by `apps/api/jobs/writeback.py`, which runs on the server.

    `verify_signature` returns False for every input, deliberately. Supabase signs its own
    URLs with its own key; this process cannot (and must not pretend to) validate them. The
    consequence is that `/v1/uploads` and `/v1/assets` — the `LocalStorage` transport — refuse
    (403) when the process runs on Supabase, and no well-behaved client ever calls them in that
    mode, because the URLs it was handed point at Supabase.
    """

    def __init__(
        self,
        *,
        url: str,
        service_key: str,
        bucket: str = DEFAULT_SUPABASE_BUCKET,
        upload_ttl_seconds: int = DEFAULT_UPLOAD_TTL_SECONDS,
        asset_ttl_seconds: int = DEFAULT_ASSET_TTL_SECONDS,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        clock: Callable[[], float] = time.time,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = url.rstrip("/")
        self._service_key = service_key
        self.bucket = bucket
        self.upload_ttl_seconds = upload_ttl_seconds
        self.asset_ttl_seconds = asset_ttl_seconds
        self.max_upload_bytes = max_upload_bytes
        self.clock = clock
        self._client = client
        self.configured = True

    @classmethod
    def from_settings(
        cls, settings: Settings, *, client: httpx.Client | None = None
    ) -> SupabaseStorage:
        """Build from `Settings`, or raise `StorageUnavailable` naming what is missing."""
        missing = [
            name
            for name in ("supabase_url", "supabase_service_key")
            if not getattr(settings, name)
        ]
        if missing:
            raise StorageUnavailable(
                f"Supabase Storage is not configured: {missing[0]} is empty "
                "(see docs/RUNBOOK.md §6).",
                missing_credential=missing[0],
            )
        return cls(
            url=settings.supabase_url,
            service_key=settings.supabase_service_key,
            bucket=settings.storage_bucket,
            upload_ttl_seconds=settings.storage_upload_ttl_seconds,
            asset_ttl_seconds=settings.storage_asset_ttl_seconds,
            max_upload_bytes=settings.storage_max_upload_bytes,
            client=client,
        )

    # -- plumbing ----------------------------------------------------------------------
    def describe(self) -> str:
        return "SupabaseStorage"

    def _auth_headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._service_key}",
            "apikey": self._service_key,
        }

    def _send(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        """One place that owns transport creation, so an injected `MockTransport` works."""
        headers = {**self._auth_headers(), **kwargs.pop("headers", {})}
        if self._client is not None:
            return self._client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        with httpx.Client(timeout=30.0) as client:
            return client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)

    def _absolute(self, signed_url: str) -> str:
        """Supabase returns a path; the client needs something it can `fetch` from a phone."""
        if signed_url.startswith(("http://", "https://")):
            return signed_url
        if signed_url.startswith("/storage/v1"):
            return f"{self.base_url}{signed_url}"
        if signed_url.startswith("/"):
            return f"{self.base_url}/storage/v1{signed_url}"
        return f"{self.base_url}/storage/v1/{signed_url}"

    # -- urls --------------------------------------------------------------------------
    def signed_upload_url(self, key: str, *, base_url: str = "") -> str | None:
        """`base_url` is accepted and ignored: the URL belongs to Supabase, not to this API."""
        del base_url
        validate_key(key)
        path = f"/storage/v1/object/upload/sign/{self.bucket}/{key}"
        response = self._send("POST", path)
        if response.status_code >= 400:
            raise StorageError(
                f"Supabase refused a signed upload URL ({response.status_code})."
            )
        payload = response.json()
        url = payload.get("url")
        token = payload.get("token")
        if isinstance(url, str) and url:
            return self._absolute(url)
        if isinstance(token, str) and token:
            return f"{self.base_url}{path}?token={token}"
        raise StorageError("Supabase returned no signed upload URL.")

    def signed_asset_url(self, key: str, *, base_url: str = "") -> str | None:
        del base_url
        validate_key(key)
        path = f"/storage/v1/object/sign/{self.bucket}/{key}"
        response = self._send("POST", path, json={"expiresIn": self.asset_ttl_seconds})
        if response.status_code >= 400:
            raise StorageError(
                f"Supabase refused a signed download URL ({response.status_code})."
            )
        payload = response.json()
        signed = payload.get("signedURL") or payload.get("signedUrl")
        if not isinstance(signed, str) or not signed:
            raise StorageError("Supabase returned no signed download URL.")
        return self._absolute(signed)

    def verify_signature(
        self, key: str, *, purpose: str, exp: int | None, sig: str | None
    ) -> bool:
        """Unconditionally `False`, and that is the honest answer.

        Supabase signs its URLs with its own key; this process cannot and must not verify
        them. A malformed `sig` gets the same 403 as a well-formed one — there is no
        comparison here, so the `TypeError` that `LocalStorage` has to guard against cannot
        arise. `is_signature_shaped` is therefore not called: dead code behind a
        `return False` would be worse than the comment explaining why it is absent.
        """
        del key, purpose, exp, sig
        return False

    # -- bytes -------------------------------------------------------------------------
    def write(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        expected_sha256: str | None = None,
    ) -> StoredObject:
        normalized = (content_type or "").split(";", 1)[0].strip().lower()
        if normalized not in ALLOWED_CONTENT_TYPES:
            raise UnsupportedContentType(
                f"content-type {normalized or 'unset'!r} is not one of "
                f"{', '.join(sorted(ALLOWED_CONTENT_TYPES))}."
            )
        if not data:
            raise InvalidObject("Refusing to store an empty object.")
        if len(data) > self.max_upload_bytes:
            raise ObjectTooLarge(
                f"Payload is {len(data)} bytes; the ceiling is {self.max_upload_bytes}."
            )
        digest = sha256_of(data)
        if expected_sha256 is not None and not hmac.compare_digest(expected_sha256, digest):
            raise BytesMismatch(
                f"Bytes hash to {digest}, but the caller expected {expected_sha256}."
            )
        response = self._send(
            "PUT",
            f"/storage/v1/object/{self.bucket}/{key}",
            content=data,
            headers={"content-type": normalized, "x-upsert": "true"},
        )
        if response.status_code >= 400:
            raise StorageError(f"Supabase refused the upload ({response.status_code}).")
        return StoredObject(
            key=key,
            byte_size=len(data),
            sha256=digest,
            content_type=normalized,
            created_at=datetime.fromtimestamp(self.clock(), UTC),
        )

    def read(self, key: str) -> bytes:
        response = self._send("GET", f"/storage/v1/object/{self.bucket}/{key}")
        if response.status_code == 404:
            raise MissingObject(key)
        if response.status_code >= 400:
            raise StorageError(f"Supabase refused the read ({response.status_code}).")
        return response.content

    def exists(self, key: str) -> bool:
        response = self._send("HEAD", f"/storage/v1/object/{self.bucket}/{key}")
        return response.status_code < 400


# ---------------------------------------------------------------------------------------
# UnavailableStorage — the null object for "no bucket is configured".
# ---------------------------------------------------------------------------------------
class UnavailableStorage:
    """What the process uses when the configured backend cannot be built.

    Every method either refuses or returns none. The point is that `POST /v1/items` still
    succeeds with `upload_url: null` (the contract allows null) and `/readyz` says the truth,
    instead of the API inventing a URL that cannot work.
    """

    configured = False

    def __init__(self, *, missing_credential: str = "supabase_service_key") -> None:
        self.missing_credential = missing_credential
        self.upload_ttl_seconds = DEFAULT_UPLOAD_TTL_SECONDS
        self.asset_ttl_seconds = DEFAULT_ASSET_TTL_SECONDS
        self.max_upload_bytes = DEFAULT_MAX_UPLOAD_BYTES

    def describe(self) -> str:
        return f"unconfigured({self.missing_credential})"

    def signed_upload_url(self, key: str, *, base_url: str = "") -> str | None:
        del key, base_url
        return None

    def signed_asset_url(self, key: str, *, base_url: str = "") -> str | None:
        del key, base_url
        return None

    def verify_signature(
        self, key: str, *, purpose: str, exp: int | None, sig: str | None
    ) -> bool:
        del key, purpose, exp, sig
        return False

    def write(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        expected_sha256: str | None = None,
    ) -> StoredObject:
        del key, data, content_type, expected_sha256
        raise StorageUnavailable(
            f"Object storage is not configured: {self.missing_credential} is empty.",
            missing_credential=self.missing_credential,
        )

    def read(self, key: str) -> bytes:
        del key
        raise StorageUnavailable(
            f"Object storage is not configured: {self.missing_credential} is empty.",
            missing_credential=self.missing_credential,
        )

    def exists(self, key: str) -> bool:
        del key
        return False


# ---------------------------------------------------------------------------------------
# Construction + the FastAPI accessor.
# ---------------------------------------------------------------------------------------
def resolve_backend(settings: Settings) -> str:
    """`local`, `supabase`, or `unconfigured`.

    An explicit `SNAPLIST_STORAGE_BACKEND` wins; otherwise the presence of a Supabase URL
    selects Supabase, so a deployment that sets credentials gets the hosted bucket without a
    second flag, and a laptop with an empty `.env` gets the filesystem.
    """
    declared = (settings.storage_backend or "").strip().lower()
    if declared == "local":
        return "local"
    if declared == "supabase":
        return "supabase"
    if declared not in {"", "auto"}:
        return "unconfigured"
    if settings.supabase_url and settings.supabase_service_key:
        return "supabase"
    if not settings.supabase_url and not settings.supabase_service_key:
        return "local"
    return "supabase"


def local_root(settings: Settings) -> Path:
    """Where `LocalStorage` keeps objects when the process did not say.

    `SNAPLIST_STORAGE_DIR` wins; the default is the same cache directory `make fetch-models`
    uses, so a developer has one place to look and one place to delete.
    """
    if settings.storage_dir:
        return Path(settings.storage_dir).expanduser()
    return Path.home() / ".cache" / "snaplist" / "storage"


def build_storage(
    settings: Settings, *, client: httpx.Client | None = None
) -> Storage:
    """Build the backend this process should use, never raising on missing credentials."""
    backend = resolve_backend(settings)
    if backend == "supabase":
        try:
            return SupabaseStorage.from_settings(settings, client=client)
        except StorageUnavailable as exc:
            return UnavailableStorage(
                missing_credential=exc.missing_credential or "supabase_service_key"
            )
    if backend == "local":
        return LocalStorage(
            local_root(settings),
            secret=(
                settings.storage_signing_secret.encode()
                if settings.storage_signing_secret
                else None
            ),
            upload_ttl_seconds=settings.storage_upload_ttl_seconds,
            asset_ttl_seconds=settings.storage_asset_ttl_seconds,
            max_upload_bytes=settings.storage_max_upload_bytes,
        )
    return UnavailableStorage(missing_credential="storage_backend")


def storage_of(request: Any) -> Storage:
    """Read the storage backend off `app.state` — the same seam as `web.repository_of`.

    Duck-typed on `request` on purpose: this module stays importable without FastAPI so the
    storage rules can be tested as plain functions.
    """
    app = getattr(request, "app", None)
    storage = getattr(getattr(app, "state", None), "storage", None)
    if storage is None:  # pragma: no cover - create_app always sets it
        raise errors.unavailable(
            "Object storage is not configured on this process.",
            details={errors.DETAIL_MISSING_CREDENTIAL: "supabase_service_key"},
        )
    return storage


__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "BytesMismatch",
    "CONTENT_TYPE_BY_EXTENSION",
    "EXTENSION_BY_CONTENT_TYPE",
    "InvalidKey",
    "InvalidObject",
    "LocalStorage",
    "MissingObject",
    "ObjectTooLarge",
    "PURPOSE_ASSET",
    "PURPOSE_UPLOAD",
    "Storage",
    "StorageError",
    "StorageUnavailable",
    "StoredObject",
    "SupabaseStorage",
    "UnavailableStorage",
    "UnsupportedContentType",
    "asset_object_key",
    "build_storage",
    "content_type_for_key",
    "local_root",
    "parse_item_key",
    "resolve_backend",
    "is_signature_shaped",
    "sha256_of",
    "sign",
    "signature_message",
    "source_object_key",
    "storage_of",
    "validate_key",
]
