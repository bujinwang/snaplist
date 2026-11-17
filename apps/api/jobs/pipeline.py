"""The vision pipeline as a job handler: photo in, asset pack out, job row telling the truth.

What this module is
-------------------
`apps/api/jobs/worker.py` runs handlers of shape `Callable[[JobRecord, Repository], None]`. The
storage backend is not part of that signature, so it arrives by closure: `build_full_handler()`
returns a handler with one storage bound to it, and `run_full_pipeline()` is the same work without
the closure, so tests can drive it directly and see the exception it raises.

Until this landed, every one of the five job kinds pointed at `default_handler`, which raises
`PipelineUnavailable` — so the API accepted photos, charged a quota unit, and then failed the job
terminally (correctly and honestly) without a single pixel of vision code running. This is the
wiring, not new capability: not one stage algorithm changed.

The order below is not arbitrary
--------------------------------
Attributes are extracted FIRST, before segmentation, because `rectify()` needs a category and
nothing else in the request carries one (`CreateItemRequest` has no category field, and inventing
`"other"` would drag a dress's 3:4 or a shoe's 4:3 onto a 4:5 canvas and warp it). The seller-facing
order is unchanged — attributes are still written last; only the internal order moved. The cost is
one extra stage before a possible `bad_photo`, which is cheaper than a wrong aspect ratio on every
photo that succeeds.

What is deliberately NOT here
-----------------------------
* `prettify_sdxl` is never called. E2-T2 adjudicated the generative path UNMEETABLE on this
  hardware; calling it would turn every job into a retryable failure.
* No mask asset is written. `cutout.png` is RGBA and its alpha channel *is* the matte, and
  `AssetPack` exposes no mask, so writing one would be two more rows nobody reads.
* `cost_micros` is always `None`. The VLM reports `cost_usd` (often `None` itself); converting a
  guess into micros would put an invented number in the cost report. `None` is UNMEASURED.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apps.api.jobs.errors import JobFailure
from apps.api.jobs.writeback import AssetWrite, write_back
from apps.api.repository import JobRecord, Repository
from apps.api.schemas import ItemAttributes, ReasonCode
from apps.api.storage import MissingObject, Storage, StorageError
from packages.vision.asset_pack import alpha_for_canvas, cutout_png
from packages.vision.attrs import (
    AttributeExtractionFailed,
    AttributeExtractor,
    AttributesUnavailable,
    get_extractor,
)
from packages.vision.matte import matte
from packages.vision.prettify import PrettifyBackendUnavailable, prettify
from packages.vision.rectify import rectify
from packages.vision.runtime import RuntimeInfo, probe
from packages.vision.segment import SegmentBackendUnavailable, segment
from packages.vision.types import PrettifyMode

#: `Handler` in worker.py, restated here so this module does not import the worker (which would
#: make the worker's `DEFAULT_HANDLERS` and this file import each other).
Handler = Callable[[JobRecord, Repository], None]

#: The marker the stages use for "the mask has no garment pixels". It is a message fragment
#: rather than a type because the stages raise plain `ValueError` — see the failure table below.
_NO_GARMENT_MARKER = "(no_garment)"

#: How an undecodable upload announces itself. `segment()` and `matte()` both raise plain
#: `ValueError` for *two* different things — "these bytes are not an image" (the seller's problem)
#: and "this shape is wrong" (our bug) — so the message is the only signal that separates them.
#: Matching on it is ugly and deliberate: giving those two failures distinct types is a change to
#: stage behaviour, which is exactly what this ticket is not allowed to make. Both markers are
#: taken from the stages' own wording (`segment(): could not decode …`, `matte(): … bytes are
#: empty`), and the `internal` branch below is what fires when neither matches.
_BAD_PHOTO_MARKERS = ("could not decode", "bytes are empty")


def _failure(
    exc: BaseException,
    *,
    stage: str,
    decode_stage: bool = False,
) -> JobFailure:
    """Map a stage exception onto the frozen reason-code vocabulary.

    `decode_stage` says whether a `ValueError` from this call means "the bytes are not a photo".
    That is the only way to separate an undecodable upload (`bad_photo`, deterministic, the seller
    must send another photo) from a shape assertion inside a later stage (our bug, `internal`) —
    both are `ValueError`, and the message is the only other signal.
    """
    text = str(exc)

    # (1) No garment. Checked first at every stage: rectify and prettify both raise it as a
    # ValueError, and it is the one deterministic cause a seller can act on.
    if isinstance(exc, ValueError) and _NO_GARMENT_MARKER in text:
        return JobFailure(
            reason_code=ReasonCode.NO_GARMENT,
            retryable=False,
            stage=stage,
            detail="the mask contains no garment pixels; nothing to prettify",
        )

    # (2) Undecodable upload. Only a decode-capable call can mean this, and only when the message
    # is one of the decode markers: `matte()` also raises `ValueError` for a wrong-shaped alpha,
    # which is our bug and must stay `internal` (asserted by
    # test_a_shape_assertion_maps_to_internal_without_retrying).
    if (
        decode_stage
        and isinstance(exc, ValueError)
        and any(marker in text for marker in _BAD_PHOTO_MARKERS)
    ):
        return JobFailure(
            reason_code=ReasonCode.BAD_PHOTO,
            retryable=False,
            stage=stage,
            detail=f"the uploaded photo could not be decoded: {text}",
        )

    # (3) A backend this machine cannot run. Retrying cannot find missing weights, so the detail
    # carries the real reason (and weights path) instead of a generic "internal".
    if isinstance(exc, SegmentBackendUnavailable | PrettifyBackendUnavailable):
        return JobFailure(
            reason_code=ReasonCode.INTERNAL,
            retryable=False,
            stage=stage,
            detail=f"{type(exc).__name__}: {text}",
        )

    # (4) The attribute stage: unconfigured, or the model could not produce a valid record after
    # its one repair. Both are `low_confidence` — that is the vocabulary's word for "we could not
    # read this photo confidently", and it is what the seller sees.
    if isinstance(exc, AttributesUnavailable | AttributeExtractionFailed):
        return JobFailure(
            reason_code=ReasonCode.LOW_CONFIDENCE,
            retryable=False,
            stage=stage,
            detail=f"{type(exc).__name__}: {text}",
        )

    # (5) The row says there is a source object and the bucket disagrees: a data-integrity
    # problem, not a transient one.
    if isinstance(exc, MissingObject):
        return JobFailure(
            reason_code=ReasonCode.INTERNAL,
            retryable=False,
            stage=stage,
            detail=f"{stage}: the source object is missing from storage ({exc})",
        )

    # (6) Any other storage failure is the one class worth retrying: a network or throttle blip
    # usually clears, and the attempts budget bounds the damage.
    if isinstance(exc, StorageError):
        return JobFailure(
            reason_code=ReasonCode.INTERNAL,
            retryable=True,
            stage=stage,
            detail=f"{type(exc).__name__}: {text}",
        )

    # (7) Any other ValueError is a shape/assertion failure in our own code.
    if isinstance(exc, ValueError):
        return JobFailure(
            reason_code=ReasonCode.INTERNAL,
            retryable=False,
            stage=stage,
            detail=f"{stage}: {type(exc).__name__}: {text}",
        )

    # Anything else is genuinely unknown. Re-raise it so the worker's generic branch treats it as
    # a transient internal error, which is what it does for unclassified exceptions by design.
    raise exc


def _provider_label(seg_backend: object, extractor_name: str) -> str:
    """`<segment backend>|classical|<extractor>` — which code produced this asset pack.

    `stub` must never appear here: the repository rejects it outright, because a job row that says
    `stub` is claiming no pipeline ran, and one did. Length is checked by the repository (2..64);
    the segment backend is the long part and it is a fixed vocabulary, so this stays well inside.
    """
    return f"{seg_backend}|{PrettifyMode.CLASSICAL.value}|{extractor_name}"


def _stage_meta(meta: object, stage: str, seconds: float | None) -> dict[str, Any]:
    """What the asset row records about the work: which backend, how long, which stage."""
    return {
        "stage": stage,
        "backend": str(getattr(meta, "backend", "unmeasured")),
        "seconds": seconds,
    }


def run_full_pipeline(
    job: JobRecord,
    repository: Repository,
    storage: Storage,
    *,
    extractor_factory: Callable[[], AttributeExtractor] = get_extractor,
    probe_fn: Callable[[], RuntimeInfo] = probe,
) -> None:
    """Run the whole pipeline for one FULL job and write the results.

    Raises `JobFailure` for every cause the vocabulary can name (see `_failure`), and lets
    genuinely unknown exceptions escape so the worker's generic branch can treat them as transient.
    """
    # 1-3. Read the source object the job was claimed against.
    item = repository.get_item(job.item_id, user_id=job.user_id)
    if item is None:
        raise JobFailure(
            reason_code=ReasonCode.INTERNAL,
            retryable=False,
            stage="load_item",
            detail=f"item {job.item_id} not found for user {job.user_id}",
        )
    source_key = item.path_for("source", 0)
    if source_key is None:
        # The worker only claims jobs whose item has a source row (see peek_next_queued_job), so a
        # missing one here is a data-integrity problem rather than an unclaimed upload.
        raise JobFailure(
            reason_code=ReasonCode.INTERNAL,
            retryable=False,
            stage="load_item",
            detail=f"item {item.id} has no source asset at position 0",
        )
    try:
        photo = storage.read(source_key)
    except StorageError as exc:
        raise _failure(exc, stage="load_photo") from exc

    # 4. Attributes FIRST: `rectify` needs the category, and this request carries no other source
    # of one. Written to the item LAST, so the seller-facing order is unchanged.
    try:
        extractor = extractor_factory()
        attributes: ItemAttributes = extractor.extract(photo)
    except Exception as exc:  # noqa: BLE001 - mapped or re-raised by `_failure`
        raise _failure(exc, stage="attributes") from exc

    # 5-6. Segment, then turn the mask into an alpha.
    try:
        runtime = probe_fn()
        seg = segment(photo, runtime.backend)
    except Exception as exc:  # noqa: BLE001
        raise _failure(exc, stage="segment", decode_stage=True) from exc
    try:
        mt = matte(photo, seg.mask_png)
    except Exception as exc:  # noqa: BLE001
        raise _failure(exc, stage="matte", decode_stage=True) from exc

    # 7-8. Rectify to the category's canvas, then bring the alpha onto that canvas.
    try:
        rect = rectify(photo, mt.alpha_png, attributes.category.value)
    except Exception as exc:  # noqa: BLE001
        raise _failure(exc, stage="rectify") from exc
    try:
        alpha_flat = alpha_for_canvas(mt.alpha_png, rect.flat_png)
    except Exception as exc:  # noqa: BLE001
        raise _failure(exc, stage="alpha_for_canvas") from exc

    # 9. CLASSICAL, explicitly. `prettify`'s default is PASS_THROUGH, so omitting `mode` would
    # silently ship the un-cleaned flat-lay while the report claimed cleanup ran. SDXL is never
    # called: E2-T2 adjudicated it UNMEETABLE on this hardware.
    try:
        pt = prettify(rect.flat_png, alpha_flat, mode=PrettifyMode.CLASSICAL)
    except Exception as exc:  # noqa: BLE001
        raise _failure(exc, stage="prettify") from exc

    # 10. The cutout is the original photo through the matte — the asset that shows what was
    # removed, and the only place the mask survives.
    try:
        cutout = cutout_png(photo, mt.alpha_png)
    except Exception as exc:  # noqa: BLE001
        raise _failure(exc, stage="cutout") from exc

    # 11. Two assets, both at position 0. No mask: its information is the cutout's alpha channel.
    try:
        write_back(
            repository,
            storage,
            item_id=item.id,
            user_id=job.user_id,
            assets=[
                AssetWrite(
                    kind="cutout",
                    data=cutout,
                    content_type="image/png",
                    meta=_stage_meta(mt.meta, "cutout", getattr(mt.meta, "seconds", None)),
                ),
                AssetWrite(
                    kind="prettified",
                    data=pt.prettified_png,
                    content_type="image/png",
                    meta=_stage_meta(pt.meta, "prettify", getattr(pt.meta, "seconds", None)),
                ),
            ],
        )
    except (StorageError, ValueError) as exc:
        raise _failure(exc, stage="write_back") from exc

    # 12. Attributes last: the seller-visible order is unchanged by step 4 being early.
    repository.update_item_attributes(item.id, user_id=job.user_id, attributes=attributes)

    # 13. Say who did the work. `cost_micros=None` is UNMEASURED, and stays that way: the VLM
    # reports a USD estimate (often None), and turning an estimate into micros would invent a
    # number for the cost report.
    repository.set_job_provenance(
        job.id,
        user_id=job.user_id,
        provider=_provider_label(seg.meta.backend, extractor.name),
        model_version=attributes.model_version,
        cost_micros=None,
    )


def build_full_handler(
    *,
    storage: Storage,
    extractor_factory: Callable[[], AttributeExtractor] = get_extractor,
    probe_fn: Callable[[], RuntimeInfo] = probe,
) -> Handler:
    """Bind a storage backend to the pipeline, producing the worker's `Handler` shape.

    The worker's handler signature has no room for storage, and adding one would change every
    handler for one caller. A closure keeps the seam as it is.
    """

    def handler(job: JobRecord, repository: Repository) -> None:
        run_full_pipeline(
            job,
            repository,
            storage,
            extractor_factory=extractor_factory,
            probe_fn=probe_fn,
        )

    return handler
