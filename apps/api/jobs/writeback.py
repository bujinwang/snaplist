"""E5-T4 — asset write-back: the one place a produced image becomes an `assets` row.

What a job produces, and what this module does with it
-----------------------------------------------------
The pipeline stages hand back bytes (a cutout PNG, a prettified JPEG, a render). Three things
have to stay true when those bytes are stored, and each of them is a test in
`apps/api/tests/test_storage.py`:

1. **Byte fidelity.** What goes in is what comes out, byte for byte. No transcoding, no EXIF
   scrubbing, no "helpful" re-encode: E3-T4 hashes the bytes to decide whether two photos are
   the same garment, and a re-encode changes the hash. `storage.write` is the only writer.
2. **Measured, never claimed.** `sha256` and `byte_size` on the row are computed from the bytes
   that were written. A caller may pass `expected_sha256` (the digest it *believes* it holds);
   if the bytes disagree the whole write-back aborts **before anything is stored**, because a
   row that describes bytes other than the ones in the bucket is worse than no row.
3. **All or nothing on the mismatch.** Every asset's digest is verified in a preflight pass.
   Only when all of them agree does the module write bytes and rows. A partially written
   asset pack would make `job -> done` a lie about content, and the worker's `done` transition
   is the only thing a seller ever sees.

Idempotency
-----------
`assets` is keyed by `(item_id, kind, position)` and `storage_path` is derived from
`(user_id, item_id, kind, position)` — not from the job id and not from a timestamp. Replaying
a job therefore overwrites the same object and upserts the same row: one row per slot, always,
which is what `docs/PLAN.md` E5-T4 means by "idempotent on job replay".
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from apps.api.repository import Repository
from apps.api.storage import (
    ALLOWED_CONTENT_TYPES,
    InvalidKey,
    Storage,
    StoredObject,
    asset_object_key,
    sha256_of,
    validate_key,
)

#: `assets.meta` keys written for every asset. Same names as `routers/uploads.py`: E3-T4 reads
#: `sha256` and must not have to know which writer produced the row.
META_SHA256 = "sha256"
META_CONTENT_TYPE = "content_type"
META_WRITER = "writer"
WRITER_NAME = "writeback"


class WritebackError(RuntimeError):
    """The asset pack was refused before any row or object was written."""


@dataclass(frozen=True, slots=True)
class AssetWrite:
    """One image a stage produced, ready to store.

    `expected_sha256` is optional and always the *caller's* claim. It exists so a stage that
    already hashed its own output can prove the bytes did not change in transit through the
    pipeline — the store still computes the digest it records independently.
    """

    kind: str
    data: bytes
    content_type: str = "image/png"
    position: int = 0
    expected_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class WritebackResult:
    """What was written, so a caller can log it without re-reading the bucket."""

    item_id: UUID
    user_id: UUID
    objects: tuple[StoredObject, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WritebackError(message)


def plan_keys(
    user_id: UUID, item_id: UUID, assets: Sequence[AssetWrite]
) -> list[str]:
    """The canonical object key for each asset. Pure, so a test can predict the bucket.

    Note what this does **not** do: validate. Keys are planned before `preflight` runs, so
    the whole list exists before a single byte is written — and `preflight` is where every
    key is validated. Planning and validating are separate steps on purpose; when planning
    happened *after* the digest checks, a key that `validate_key` rejects (say a `kind` of
    `../../escape`) was discovered mid-loop, after earlier assets were already on disk.
    """
    return [
        asset_object_key(user_id, item_id, asset.kind, asset.position, asset.content_type)
        for asset in assets
    ]


def preflight(
    assets: Sequence[AssetWrite], keys: Sequence[str]
) -> list[tuple[AssetWrite, str]]:
    """Validate every asset **and its planned key**, then return `(asset, measured_sha256)`.

    Nothing is written here, and nothing may be: every refusal in this function happens before
    the bucket or the database is touched, which is the only way "a mismatch leaves no trace"
    can be literally true. That is why the key is checked here rather than inside the write
    loop — the loop has already persisted earlier assets by the time it reaches the bad one.

    `kind` is checked separately from the key because a slash in it produces a *valid* key
    that is one directory deeper (`items/<u>/<i>/a/b.png`): `validate_key` cannot see the
    mistake, and the asset would land somewhere nothing ever reads.
    """
    _require(bool(assets), "write-back needs at least one asset.")
    _require(
        len(assets) == len(keys),
        "every asset needs exactly one planned key.",
    )
    seen: set[tuple[str, int]] = set()
    measured: list[tuple[AssetWrite, str]] = []
    for asset, key in zip(assets, keys, strict=True):
        slot = (asset.kind, asset.position)
        _require(
            slot not in seen,
            f"duplicate asset slot kind={asset.kind} position={asset.position}",
        )
        seen.add(slot)
        _require(bool(asset.kind), "asset kind must not be empty.")
        _require(
            "/" not in asset.kind and "\\" not in asset.kind,
            f"asset kind {asset.kind!r} must not contain a path separator.",
        )
        _require(
            asset.kind not in {".", ".."},
            f"asset kind {asset.kind!r} is a path segment, not an asset kind.",
        )
        _require(asset.position >= 0, "asset position must be >= 0.")
        _require(bool(asset.data), f"asset kind={asset.kind} has no bytes.")
        content_type = (asset.content_type or "").split(";", 1)[0].strip().lower()
        _require(
            content_type in ALLOWED_CONTENT_TYPES,
            f"asset kind={asset.kind} has unsupported content-type {content_type!r}.",
        )
        try:
            validate_key(key)
        except InvalidKey as exc:
            raise WritebackError(
                f"asset kind={asset.kind} would be stored at an invalid key ({exc}) "
                "nothing was written."
            ) from exc
        digest = sha256_of(asset.data)
        if asset.expected_sha256 is not None:
            _require(
                hmac.compare_digest(asset.expected_sha256, digest),
                (
                    f"asset kind={asset.kind} hashes to {digest}, but the caller expected "
                    f"{asset.expected_sha256}; nothing was written."
                ),
            )
        measured.append((asset, digest))
    return measured


def write_back(
    repository: Repository,
    storage: Storage,
    *,
    item_id: UUID,
    user_id: UUID,
    assets: Sequence[AssetWrite],
) -> WritebackResult:
    """Store every asset and upsert its row. Idempotent for a replayed job.

    The order is: plan every key, preflight **everything** (digests *and* keys), then write
    bytes, then write rows. Rows last, because a row is the promise that the bytes are there —
    the reverse order would publish an asset URL pointing at an object that does not exist
    yet, and the client would see a 404 on a pack it was just told was ready.

    That ordering is pinned, not merely intended: `test_rows_are_written_only_after_the_bytes_exist`
    hooks `set_asset` and asserts the object is already readable at the moment the row is
    written, so swapping the two loops turns a test red.
    """
    keys = plan_keys(user_id, item_id, assets)
    measured = preflight(assets, keys)

    stored: list[StoredObject] = []
    for (asset, digest), key in zip(measured, keys, strict=True):
        # `expected_sha256` is passed through so the backend re-checks the exact bytes it is
        # about to persist: belt and braces, and free (one hash of a buffer already in RAM).
        object_ = storage.write(
            key, asset.data, content_type=asset.content_type, expected_sha256=digest
        )
        stored.append(object_)

    for (asset, digest), key in zip(measured, keys, strict=True):
        repository.set_asset(
            item_id,
            user_id=user_id,
            kind=asset.kind,
            storage_path=key,
            position=asset.position,
            byte_size=len(asset.data),
            meta={
                META_SHA256: digest,
                META_CONTENT_TYPE: asset.content_type,
                META_WRITER: WRITER_NAME,
            },
        )

    return WritebackResult(
        item_id=item_id,
        user_id=user_id,
        objects=tuple(stored),
        metadata={"assets": str(len(stored))},
    )


__all__ = [
    "AssetWrite",
    "WritebackError",
    "WritebackResult",
    "plan_keys",
    "preflight",
    "write_back",
]
