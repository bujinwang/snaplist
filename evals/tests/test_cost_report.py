"""Tests for the `cost.md` producer. No network, no provider keys.

The producer must never turn an unmeasured job into a free one, never allocate an aggregate to a
stage that did not measure it, and always emit a table the existing E0-T4 dashboard can parse.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from apps.api.repository import SqliteRepository
from apps.api.schemas import JobKind, JobState
from evals import cost_report
from infra.ci.gates import cost_dashboard


def _seed_db(tmp_path: Path) -> tuple[SqliteRepository, object]:
    db = tmp_path / "snaplist.db"
    repo = SqliteRepository(str(db))
    user_id = uuid4()
    item = repo.create_item(user_id=user_id, status="ready")
    return repo, (user_id, item.id)


def _add_job(
    repo: SqliteRepository,
    user_id: object,
    item_id: object,
    kind: JobKind,
    cost_micros: int | None,
) -> None:
    job = repo.create_job(item_id=item_id, user_id=user_id, kind=kind)
    repo.update_job(replace(job, state=JobState.DONE))
    # `update_job` persists state, but cost is written only by the audited provenance path.
    repo.set_job_provenance(
        job.id,
        user_id=user_id,
        provider="test-backend|classical|test-extractor",
        model_version="test-model-1",
        cost_micros=cost_micros,
    )


def test_absent_database_reports_unmeasured_not_zero() -> None:
    obs = cost_report.collect("/tmp/snaplist-definitely-absent.db")
    assert obs.source_state == "DB_ABSENT"
    assert obs.e4_per_render_usd is None
    text = cost_report.render(obs, generated_at="2026-11-20T00:00:00+00:00")
    assert "| E1 segmentation | UNMEASURED |" in text
    assert "| E4 on-model render (per render) | UNMEASURED |" in text
    assert "$0.0000" not in text


def test_measured_vton_rows_are_averaged_without_allocating_aggregate(
    tmp_path: Path,
) -> None:
    repo, (user_id, item_id) = _seed_db(tmp_path)
    _add_job(repo, user_id, item_id, JobKind.VTON, 75_000)
    _add_job(repo, user_id, item_id, JobKind.VTON, 125_000)
    _add_job(repo, user_id, item_id, JobKind.FULL, 999_999)

    obs = cost_report.collect(str(tmp_path / "snaplist.db"))
    assert obs.source_state == "DB_READ"
    assert obs.vton_n == 2
    assert obs.aggregate_cost_micros == 1_199_999
    assert obs.e4_per_render_usd == pytest.approx(0.1)

    text = cost_report.render(obs, generated_at="2026-11-20T00:00:00+00:00")
    assert "| E4 on-model render (per render) | $0.1000 | 2 |" in text
    # The FULL job's aggregate is reported as an observation, never split into E1+E2+E3.
    assert "| E1 segmentation | UNMEASURED |" in text
    assert "| E3 attributes + dedupe | UNMEASURED |" in text
    assert "| aggregate measured cost_micros | 1199999 |" in text


def test_null_cost_is_unmeasured_not_free(tmp_path: Path) -> None:
    repo, (user_id, item_id) = _seed_db(tmp_path)
    _add_job(repo, user_id, item_id, JobKind.VTON, None)

    obs = cost_report.collect(str(tmp_path / "snaplist.db"))
    assert obs.jobs_with_cost == 0
    assert obs.e4_per_render_usd is None
    text = cost_report.render(obs, generated_at="2026-11-20T00:00:00+00:00")
    assert "NULL means UNMEASURED, not free" in text
    assert "$0.0000" not in text


def test_bytes_and_tokens_are_observed_but_never_priced(tmp_path: Path) -> None:
    repo, (user_id, item_id) = _seed_db(tmp_path)
    repo.set_asset(
        item_id,
        user_id=user_id,
        kind="prettified",
        storage_path="sellers/x/prettified.png",
        byte_size=2048,
        meta={"stage": "prettify", "usage": {"total_tokens": 3360}},
    )

    obs = cost_report.collect(str(tmp_path / "snaplist.db"))
    assert obs.total_bytes == 2048
    assert obs.total_tokens == 3360
    text = cost_report.render(obs, generated_at="2026-11-20T00:00:00+00:00")
    assert "| total measured asset bytes | 2048 |" in text
    assert "| total observed tokens | 3360 |" in text
    assert "| E3 attributes + dedupe | UNMEASURED |" in text


def test_existing_dashboard_parses_the_generated_table(tmp_path: Path) -> None:
    repo, (user_id, item_id) = _seed_db(tmp_path)
    _add_job(repo, user_id, item_id, JobKind.VTON, 75_000)
    text = cost_report.render(
        cost_report.collect(str(tmp_path / "snaplist.db")),
        generated_at="2026-11-20T00:00:00+00:00",
    )
    parsed, problems = cost_dashboard.parse_report(text)
    assert problems == []
    assert parsed["E1"].cost is None
    assert parsed["E2"].cost is None
    assert parsed["E3"].cost is None
    assert parsed["E4"].cost == pytest.approx(0.075)
    assert parsed["E4"].n == 1


def test_cli_writes_report_and_dry_run_does_not(tmp_path: Path) -> None:
    repo, (user_id, item_id) = _seed_db(tmp_path)
    _add_job(repo, user_id, item_id, JobKind.VTON, 75_000)
    db = str(tmp_path / "snaplist.db")
    out = tmp_path / "reports" / "cost.md"

    assert cost_report.main(["--db", db, "--report", str(out), "--dry-run"]) == 0
    assert not out.exists()

    assert cost_report.main(
        ["--db", db, "--report", str(out), "--now", "2026-11-20T00:00:00+00:00"]
    ) == 0
    assert out.exists()
    assert "UNMEASURED" in out.read_text()
    assert not (tmp_path / "reports" / "cost.md.tmp").exists()


def test_read_only_source_is_not_mutated(tmp_path: Path) -> None:
    repo, (user_id, item_id) = _seed_db(tmp_path)
    _add_job(repo, user_id, item_id, JobKind.VTON, 75_000)
    db = tmp_path / "snaplist.db"
    before = db.read_bytes()
    cost_report.collect(str(db))
    assert db.read_bytes() == before
