"""Tests for E3-T1 (registry) and E3-T2 (schema enforcement / one repair / evidence).

No network, no credentials, no VLM. BRIEF.md allows fixtures on disk and forbids stubbed
network responses *in the acceptance path* — these are unit tests of our own parsing logic,
and they deliberately do not claim an accuracy number. The real provider round-trip is
UNMEASURED until Bert picks a provider (docs/RUNBOOK.md §6); nothing here fakes it.

What each DoD line is proven by
--------------------------------
* E3-T1 "at least two extractors resolve by name" -> test_registry_resolves_two_extractors
* E3-T1 "provider chosen by configuration"      -> test_env_selects_provider
* E3-T1 "importing pulls in no provider SDK"    -> test_importing_attrs_does_not_import_httpx
  (subprocess, so an earlier test's import cannot mask a regression)
* E3-T2 "validates against ItemAttributes, extra=forbid" -> test_extra_key_triggers_repair
* E3-T2 "a schema violation is retried exactly once"     -> test_repair_happens_exactly_once
  and test_two_failures_raise_low_confidence (attempts == 2, never 3)
* E3-T2 "brand/material only with evidence"     -> test_unevidenced_brand_and_material_dropped
* Honest UNMEASURED                              -> test_no_config_raises_attributes_unavailable
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from packages.vision.attrs import (
    ATTRS_PROVIDER_ENV,
    AttributeExtractionFailed,
    AttributesUnavailable,
    available_extractors,
    get_extractor,
)
from packages.vision.attrs_adapters.base import (
    _TRACKED_FIELDS,
    CONFIDENCE_FLOOR,
    extract_from_response,
)
from packages.vision.attrs_adapters.http import HttpVlmExtractor

FIXTURES = Path(__file__).parent / "fixtures"

MODEL_VERSION = "test-fixture:v1"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _once(text: str):
    """A `complete` callable that replays one on-disk fixture, counting calls."""
    calls: list[str] = []

    def complete(prompt: str) -> str:
        calls.append(prompt)
        return text

    return complete, calls


# --------------------------------------------------------------------------- E3-T1 registry


def test_e3_t1_acceptance_command_resolves_stub() -> None:
    assert get_extractor("stub").name == "stub"


def test_registry_resolves_two_extractors() -> None:
    names = available_extractors()
    assert len(names) >= 2
    for name in names:
        assert get_extractor(name).name == name


def test_unknown_provider_names_the_available_ones() -> None:
    with pytest.raises(ValueError, match="available:"):
        get_extractor("made-up-vendor")


def test_env_selects_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ATTRS_PROVIDER_ENV, "stub")
    assert get_extractor().name == "stub"
    monkeypatch.setenv(ATTRS_PROVIDER_ENV, "http")
    assert get_extractor().name == "http"


def test_importing_attrs_does_not_import_httpx() -> None:
    """Subprocess on purpose: any earlier test importing httpx would mask this regression."""
    code = (
        "import sys; import packages.vision.attrs; "
        "from packages.vision.attrs import get_extractor; "
        "get_extractor('http'); "
        "assert 'httpx' not in sys.modules, 'provider SDK leaked into module import'"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_stub_is_honestly_marked_as_not_a_model() -> None:
    attrs = get_extractor("stub").extract(b"\x89PNG\r\n\x1a\n")
    assert attrs.model_version == "stub:not-a-model"
    assert attrs.brand is None
    assert attrs.material == []
    assert set(attrs.confidence.values()) == {0.0}
    assert set(_TRACKED_FIELDS) == set(attrs.needs_review)


# ------------------------------------------------------- E3-T2 schema / repair / evidence


def test_valid_fixture_validates_and_preserves_evidenced_fields() -> None:
    complete, calls = _once(_fixture("attrs_valid.json"))
    outcome = extract_from_response(complete, model_version=MODEL_VERSION)
    assert outcome.attempts == 1
    assert outcome.repaired is False
    assert len(calls) == 1
    assert outcome.attributes.category == "top"
    assert outcome.attributes.brand == "North Face"  # evidence.brand present
    assert outcome.attributes.material == ["cotton"]  # evidence.material present
    assert outcome.attributes.model_version == MODEL_VERSION
    assert "era" in outcome.attributes.needs_review  # confidence 0.0 < floor
    assert "measurements" in outcome.attributes.needs_review


def test_unevidenced_brand_and_material_dropped() -> None:
    complete, _ = _once(_fixture("attrs_hallucinated_brand.json"))
    outcome = extract_from_response(complete, model_version=MODEL_VERSION)
    assert outcome.attributes.brand is None
    assert outcome.attributes.material == []
    assert outcome.dropped_fields == ("brand", "material")
    for field in outcome.dropped_fields:
        assert field in outcome.attributes.needs_review
        assert outcome.attributes.confidence[field] == 0.0


def test_confidence_defaults_to_zero_not_a_guess() -> None:
    doc = json.loads(_fixture("attrs_hallucinated_brand.json"))
    doc["attributes"]["confidence"] = {}
    complete, _ = _once(json.dumps(doc))
    attrs = extract_from_response(complete, model_version=MODEL_VERSION).attributes
    assert all(score == 0.0 for score in attrs.confidence.values())
    assert set(_TRACKED_FIELDS) <= set(attrs.needs_review)
    assert all(score < CONFIDENCE_FLOOR for score in attrs.confidence.values())


def test_extra_key_triggers_repair() -> None:
    """ItemAttributes has extra='forbid'; a stray key must be a schema failure, not ignored."""
    bad = json.loads(_fixture("attrs_valid.json"))
    bad["attributes"]["invented_field"] = True
    responses = iter([json.dumps(bad), _fixture("attrs_valid.json")])
    calls: list[str] = []

    def complete(prompt: str) -> str:
        calls.append(prompt)
        return next(responses)

    outcome = extract_from_response(complete, model_version=MODEL_VERSION)
    assert "invented_field" not in outcome.attributes.model_dump()
    assert outcome.repaired is True


def test_repair_happens_exactly_once() -> None:
    bad = "{not json"
    responses = iter([bad, _fixture("attrs_valid.json"), "SHOULD NEVER BE REQUESTED"])
    calls = 0

    def complete(prompt: str) -> str:
        nonlocal calls
        calls += 1
        return next(responses)

    outcome = extract_from_response(complete, model_version=MODEL_VERSION)
    assert calls == 2
    assert outcome.attempts == 2
    assert outcome.repaired is True


def test_two_failures_raise_low_confidence() -> None:
    calls = 0

    def always_bad(prompt: str) -> str:
        nonlocal calls
        calls += 1
        return "{not json"

    with pytest.raises(AttributeExtractionFailed) as exc:
        extract_from_response(always_bad, model_version=MODEL_VERSION)
    assert calls == 2, "exactly one repair retry, never more"
    assert exc.value.attempts == 2
    assert exc.value.reason_code == "low_confidence"


def test_markdown_fence_is_tolerated() -> None:
    fenced = "```json\n" + _fixture("attrs_valid.json") + "\n```"
    complete, _ = _once(fenced)
    assert extract_from_response(complete, model_version=MODEL_VERSION).attempts == 1


def test_bare_attributes_object_is_accepted() -> None:
    doc = json.loads(_fixture("attrs_valid.json"))["attributes"]
    complete, _ = _once(json.dumps(doc))
    attrs = extract_from_response(complete, model_version=MODEL_VERSION).attributes
    # no evidence envelope => brand/material must be dropped
    assert attrs.brand is None
    assert attrs.material == []


# ------------------------------------------------------------- real adapter: encoding utils


def test_image_mime_is_sniffed_not_hardcoded() -> None:
    from packages.vision.attrs_adapters.http import _image_mime

    assert _image_mime(b"\xff\xd8\xff\xe0" + b"\x00" * 8) == "image/jpeg"
    assert _image_mime(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "image/png"
    webp = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8
    assert _image_mime(webp) == "image/webp"
    assert _image_mime(b"GIF89a" + b"\x00" * 8) == "image/gif"
    with pytest.raises(ValueError, match="unsupported image type"):
        _image_mime(b"not-an-image")


# ------------------------------------- real adapter: provider response + config robustness


def test_message_content_rejects_blank_and_malformed_bodies() -> None:
    from packages.vision.attrs_adapters.http import _message_content

    good = {"choices": [{"message": {"content": '{"attributes": {}}'}}]}
    assert _message_content(good) == '{"attributes": {}}'
    # DeepSeek JSON mode can replace unparseable output with spaces -> must trigger repair.
    for blank in ("", "   ", "\n", None):
        with pytest.raises(ValueError, match="blank content"):
            _message_content({"choices": [{"message": {"content": blank}}]})
    with pytest.raises(AttributeExtractionFailed) as exc:
        _message_content({"choices": [{"message": {}}]})
    assert exc.value.reason_code == "low_confidence"
    with pytest.raises(AttributeExtractionFailed):
        _message_content("not an object")


def test_blank_content_consumes_the_one_repair_retry() -> None:
    """A whitespace-only provider reply must be retried once, then fail low_confidence."""
    calls = 0

    def blank_then_valid(prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return "   "  # what DeepSeek JSON mode emits for unparseable output
        return '{"attributes": {"category": "top", "colors": ["black"]}, "evidence": {}}'

    outcome = extract_from_response(blank_then_valid, model_version=MODEL_VERSION)
    assert calls == 2
    assert outcome.repaired is True
    assert outcome.attributes.category == "top"

    def always_blank(prompt: str) -> str:
        nonlocal calls
        calls += 1
        return "  "

    calls = 0
    with pytest.raises(AttributeExtractionFailed) as exc:
        extract_from_response(always_blank, model_version=MODEL_VERSION)
    assert calls == 2
    assert exc.value.reason_code == "low_confidence"


def test_complete_raising_value_error_still_gets_one_repair() -> None:
    """The real adapter raises ValueError on blank content from inside complete(); that MUST be
    caught inside the try or the repair retry is lost. Distinct from _parse failing on blanks."""
    calls = 0

    def raises_then_valid(prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("provider returned blank content")
        return '{"attributes": {"category": "dress", "colors": ["red"]}, "evidence": {}}'

    outcome = extract_from_response(raises_then_valid, model_version=MODEL_VERSION)
    assert calls == 2
    assert outcome.repaired is True


def test_hard_provider_failure_is_not_retried() -> None:
    """AttributeExtractionFailed (RuntimeError) must propagate, not consume a repair."""
    calls = 0

    def hard_fail(prompt: str) -> str:
        nonlocal calls
        calls += 1
        raise AttributeExtractionFailed("provider HTTP 503", attempts=1)

    with pytest.raises(AttributeExtractionFailed) as exc:
        extract_from_response(hard_fail, model_version=MODEL_VERSION)
    assert calls == 1
    assert exc.value.reason_code == "low_confidence"


def test_extra_body_is_merged_into_the_actual_payload() -> None:
    """Guards the merge itself, not just config parsing: thinking/response_format must reach
    the request body, or DeepSeek's default CoT mode silently breaks structured output."""
    from packages.vision.attrs_adapters.http import _Config, build_payload

    cfg = _Config(
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        key="fixture-key",
        extra_body={
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": 1024,
        },
    )
    payload = build_payload(cfg, "system prompt", "data:image/jpeg;base64,AAAA")
    assert payload["model"] == "deepseek-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 1024
    assert payload["messages"][0] == {"role": "system", "content": "system prompt"}
    image_block = payload["messages"][1]["content"][1]
    assert image_block["type"] == "image_url"
    assert image_block["image_url"]["url"].startswith("data:image/jpeg;base64,")

    # config wins over defaults (a provider that rejects temperature can override it)
    override = _Config(
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        key="k",
        extra_body={"temperature": 1},
    )
    assert build_payload(override, "p", "data:image/png;base64,AAAA")["temperature"] == 1


def test_json_mode_prompt_requirements() -> None:
    """DeepSeek JSON Output needs the word 'json' and an example in the prompt."""
    from packages.vision.attrs_adapters.base import build_prompt

    prompt = build_prompt()
    assert "json" in prompt.lower()
    assert "Shape example" in prompt
    assert "NOT values to copy" in prompt


def test_extra_body_is_parsed_and_merged_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider knobs live in config, so swapping vendors is not a code change."""
    from packages.vision.attrs_adapters.http import _EXTRA_BODY_ENV, _config_from_env

    monkeypatch.setenv("SNAPLIST_VLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("SNAPLIST_VLM_MODEL", "deepseek-flash")
    monkeypatch.setenv("VLM_API_KEY", "fixture-key")
    monkeypatch.setenv(
        _EXTRA_BODY_ENV,
        '{"thinking":{"type":"disabled"},"response_format":{"type":"json_object"}}',
    )
    cfg = _config_from_env()
    assert cfg.model == "deepseek-flash"
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.extra_body["thinking"] == {"type": "disabled"}
    assert cfg.extra_body["response_format"] == {"type": "json_object"}

    monkeypatch.setenv(_EXTRA_BODY_ENV, "{not json")
    with pytest.raises(AttributesUnavailable, match="must be a JSON object"):
        _config_from_env()
    monkeypatch.setenv(_EXTRA_BODY_ENV, "[1,2]")
    with pytest.raises(AttributesUnavailable, match="must be a JSON object"):
        _config_from_env()


# ------------------------------------------------------------- real adapter: no creds path


def test_no_config_raises_attributes_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    for var in ("SNAPLIST_VLM_BASE_URL", "SNAPLIST_VLM_MODEL", "VLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    # Isolate from cwd: once a real (gitignored) .env exists at the repo root, chdir-ing away
    # is what keeps this test about "no config" rather than about that file.
    clean = Path(tempfile.gettempdir()) / "snaplist_no_dotenv"
    if not clean.is_dir():
        clean.mkdir()
    for stray in clean.glob(".env*"):
        stray.unlink()
    monkeypatch.chdir(clean)
    with pytest.raises(AttributesUnavailable) as exc:
        HttpVlmExtractor().extract(b"\xff\xd8\xff")
    message = str(exc.value)
    assert "SNAPLIST_VLM_BASE_URL" in message
    assert "SNAPLIST_VLM_MODEL" in message
    assert "VLM_API_KEY" in message
    assert "stub" not in message.lower().replace("substitute the stub", "")


def test_dotenv_values_are_read_without_a_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key in gitignored .env must be picked up; an exported var must still win.

    Uses a fixed dir rather than pytest's `tmp_path`: the WorkBuddy shim raises EEXIST from
    `mkdir(exist_ok=True)`, which is what tmp_path's own fixture does internally.
    """
    import tempfile

    from packages.vision.attrs_adapters.http import _config_from_env

    for var in ("SNAPLIST_VLM_BASE_URL", "SNAPLIST_VLM_MODEL", "VLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    workdir = Path(tempfile.gettempdir()) / "snaplist_dotenv_test"
    if not workdir.is_dir():
        workdir.mkdir()
    (workdir / ".env").write_text(
        "SNAPLIST_VLM_BASE_URL=https://example.invalid/v1\n"
        "SNAPLIST_VLM_MODEL=fixture-model\n"
        "VLM_API_KEY=fixture-key\n"
    )
    monkeypatch.chdir(workdir)
    cfg = _config_from_env()
    assert cfg.base_url == "https://example.invalid/v1"
    assert cfg.model == "fixture-model"
    assert cfg.key == "fixture-key"

    monkeypatch.setenv("SNAPLIST_VLM_MODEL", "exported-model")
    assert _config_from_env().model == "exported-model"  # override=False honours env
    (workdir / ".env").unlink()
