"""Contract tests for apps/api/schemas.py.

These are the guard rails behind docs/CONTRACTS.md. They assert the *shape* promises other
roles code against: field names, required-ness, enum literals, and the extra="forbid"
policy. The zod mirror is checked separately by packages/shared/test/contract_parity.test.ts.
"""

from apps.api.schemas import (
    CONTRACT_VERSION,
    AssetPack,
    ErrorCode,
    ErrorResponse,
    GarmentCategory,
    ItemAttributes,
    Job,
    JobState,
    ReasonCode,
)

EXPECTED_ITEM_ATTRIBUTES_FIELDS = [
    "brand",
    "category",
    "colors",
    "condition",
    "confidence",
    "defects",
    "era",
    "fit",
    "keywords",
    "material",
    "measurements",
    "model_version",
    "needs_review",
    "pattern",
    "size_label",
    "subcategory",
]


def _minimal_attributes() -> dict[str, object]:
    return {"category": "top", "colors": ["black"]}


def test_contract_version_is_pinned() -> None:
    assert CONTRACT_VERSION == "1.1.0"


def test_item_attributes_field_set_is_frozen() -> None:
    assert sorted(ItemAttributes.model_fields) == EXPECTED_ITEM_ATTRIBUTES_FIELDS


def test_only_category_and_colors_are_required() -> None:
    required = {n for n, f in ItemAttributes.model_fields.items() if f.is_required()}
    assert required == {"category", "colors"}


def test_asset_pack_has_exactly_the_five_contract_keys() -> None:
    assert sorted(AssetPack.model_fields) == [
        "attributes",
        "cutout_url",
        "item_id",
        "on_model_urls",
        "prettified_url",
    ]


def test_asset_pack_rejects_unknown_keys() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AssetPack.model_validate(
            {
                "item_id": "00000000-0000-0000-0000-000000000000",
                "prettified_url": None,
                "cutout_url": None,
                "on_model_urls": [],
                "attributes": None,
                "surprise": 1,
            }
        )


def test_item_attributes_roundtrip_is_stable() -> None:
    a = ItemAttributes.model_validate(_minimal_attributes())
    assert a.pattern == "unknown"
    assert a.fit == "unknown"
    assert a.condition == "good"
    assert a.model_version == "unset"
    assert a.model_dump(mode="json")["colors"] == ["black"]


def test_colors_cannot_be_empty() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ItemAttributes.model_validate({"category": "top", "colors": []})


def test_categories_match_brief_vocabulary() -> None:
    assert [c.value for c in GarmentCategory] == [
        "top",
        "bottom",
        "dress",
        "outerwear",
        "shoe",
        "accessory",
        "other",
    ]


def test_job_state_machine_vocabulary() -> None:
    assert [s.value for s in JobState] == ["queued", "running", "done", "failed"]


def test_reason_code_enum_is_exactly_the_five_from_the_brief() -> None:
    assert sorted(r.value for r in ReasonCode) == sorted(
        ["bad_photo", "no_garment", "low_confidence", "vton_rejected", "internal"]
    )


def test_job_defaults_to_queued_with_no_reason_code() -> None:
    job = Job.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "item_id": "00000000-0000-0000-0000-000000000002",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    assert job.state == "queued"
    assert job.reason_code is None
    assert job.attempts == 0


def test_error_envelope_shape() -> None:
    err = ErrorResponse.model_validate(
        {"error": {"code": "quota_exceeded", "message": "plan limit reached"}}
    )
    assert err.error.code == ErrorCode.QUOTA_EXCEEDED
