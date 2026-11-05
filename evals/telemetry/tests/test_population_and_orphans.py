# ruff: noqa: F811
# `tmp_path` is imported to register the shim-safe pytest fixture defined in fixtures.py;
# each test's parameter of the same name is the injected value, not a redefinition.
"""Population, window, activation and per-item mechanics (T03/T04).

These tests pin the rulings that the frozen PLAN wording does NOT state correctly:
  * filtering/sorting uses `received_at`, NEVER `client_ts`; the window is half-open;
  * `tool_time_ms` third term comes from `export_clicked.export_latency_ms`, NOT from
    `export_delivered` (PLAN:124 is a spec bug; `repository.py` reverse CHECK forbids the
    delivery row from carrying that column);
  * tool time SUMS across retries while the point attribute takes the latest;
  * activation counts `export_delivered` DISTINCT items per seller, n in SELLERS;
  * `export_burst_60m` has no producer and must print UNMEASURED with a reason;
  * seller identity is the uuid SUFFIX of `subject` (ADR-003 anon -> usr promotion).
"""

from __future__ import annotations

from pathlib import Path

from evals.telemetry.tests import fixtures as fx
from evals.telemetry.tests.fixtures import tmp_path  # noqa: F401 - pytest fixture


def _db(tmp_path: Path, label: str) -> tuple[fx.Db, Path]:
    path = tmp_path / f"{label}.db"
    return fx.make_db(path), path


def _gate_line(stdout: str, name: str) -> str:
    return next(line for line in stdout.splitlines() if line.startswith(f"{name} "))


def test_population_counts_distinct_items_and_sellers_in_the_window(
    tmp_path: Path,
) -> None:
    db, path = _db(tmp_path, "population")
    fx.populate_baseline(db)
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert result.returncode == 0
    assert "population=30 items (from 3 sellers)" in result.stdout
    assert "n=30 items (from 3 sellers)" in result.stdout


def test_half_open_window_uses_received_at_never_client_ts(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "window")
    fx.populate_baseline(db)
    # Exactly the window end: client_ts says "inside", received_at says "outside".
    db.event(
        name="prettify_approved",
        item_id=fx.item_uuid(999),
        received_at=fx.AT_WINDOW_END,
        client_ts=fx.IN_WINDOW,
        decision="approve",
        prettify_latency_ms=1_000,
        attrs_latency_ms=1_000,
    )
    # Inside the window by received_at, but client_ts is a week stale.
    db.event(
        name="prettify_approved",
        item_id=fx.item_uuid(998),
        received_at=fx.IN_WINDOW,
        client_ts=fx.BEFORE_WINDOW,
        decision="approve",
        prettify_latency_ms=1_000,
        attrs_latency_ms=1_000,
    )
    # Strictly before the window start on received_at.
    db.event(
        name="prettify_approved",
        item_id=fx.item_uuid(997),
        received_at=fx.BEFORE_WINDOW,
        client_ts=fx.IN_WINDOW,
        decision="approve",
        prettify_latency_ms=1_000,
        attrs_latency_ms=1_000,
    )
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert "population=31 items (from 3 sellers)" in result.stdout
    assert result.returncode == 0


def test_soft_deleted_items_keep_their_events(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "soft")
    fx.populate_baseline(db, count=30)
    db.connection.execute(
        "update items set deleted_at = ? where id in (select id from items limit 10)",
        (fx.IN_WINDOW,),
    )
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert result.returncode == 0
    assert "population=30 items (from 3 sellers)" in result.stdout
    assert "PASS=8" in result.stdout, "soft delete must not shrink the historical rate"


def test_hard_deleted_orphan_is_not_excluded_only_reported(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "orphan")
    fx.populate_baseline(db, count=30)
    db.hard_delete(fx.item_uuid(1))
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert "orphan_item_ids=1" in result.stdout
    assert "population=30 items (from 3 sellers)" in result.stdout
    assert "PASS=8" in result.stdout, "an orphan is still a datapoint, not an exclusion"


def test_failure_mix_unattributable_is_an_honest_residual(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "mix")
    fx.populate_baseline(db, count=30)
    # Remove the jobs row without a hard delete: reason_code is unrecoverable, but the item
    # is NOT excluded (positive evidence only). ADR-008 no-FK + jobs cascade residual.
    db.connection.execute("delete from jobs where item_id = ?", (fx.item_uuid(1),))
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert "failure_mix_unattributable=1" in result.stdout
    assert "orphan_item_ids=0" in result.stdout
    assert result.returncode == 0


def test_failed_items_are_excluded_and_counted_in_failure_mix(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "failed")
    fx.populate_baseline(db, count=31)
    db.job(950, fx.item_uuid(31), state="failed", reason_code="bad_photo")
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert result.returncode == 0
    assert "failure_mix: bad_photo=1" in result.stdout
    assert "n=30 items (from 3 sellers)" in result.stdout


def test_activation_counts_sellers_and_is_reported_not_gated(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "activation")
    for seller in range(20):
        for offset in range(3):
            db.full_item(seller * 3 + offset + 1, seller=seller)
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert result.returncode == 0
    assert "population=60 items (from 20 sellers)" in result.stdout
    assert "activation_7d=1.0000, n=20 sellers (reported, not gated)" in result.stdout
    assert "VERDICTS: PASS=8" in result.stdout


def test_activation_uses_export_delivered_not_export_clicked(tmp_path: Path) -> None:
    """HANDOFF-product §12.2(g) draft (`export_clicked`) is void; count deliveries."""
    db, path = _db(tmp_path, "activation-click")
    fx.populate_baseline(db, with_delivery=False)
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert "activation_7d=UNMEASURED (n=0 sellers < 20)" in result.stdout
    assert "PASS=8" in result.stdout, "delivery is NOT a prerequisite for tool time"


def test_export_burst_60m_is_unmeasured_with_a_reason(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "burst")
    fx.populate_baseline(db)
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert "export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)" in (
        result.stdout
    )


def test_seller_key_is_the_uuid_suffix_for_anon_and_usr(tmp_path: Path) -> None:
    """ADR-003: an anonymous promotion changes only the prefix, not the seller identity."""
    db, path = _db(tmp_path, "seller")
    suffix = fx.subject_uuid(7)
    for index in (1, 2):
        item_id = fx.item_uuid(index)
        db.event(
            name="prettify_approved",
            item_id=item_id,
            subject_text=f"anon_{suffix}",
            decision="approve",
            prettify_latency_ms=1_000,
            attrs_latency_ms=1_000,
        )
        db.event(
            name="prettify_approved",
            item_id=item_id,
            subject_text=f"usr_{suffix}",
            decision="approve",
            prettify_latency_ms=1_000,
            attrs_latency_ms=1_000,
        )
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert "population=2 items (from 1 sellers)" in result.stdout


def test_tool_time_uses_click_latency_not_delivery_latency(tmp_path: Path) -> None:
    """PLAN:124 reconciliation, proven behaviourally with two DIFFERENT values."""
    db, path = _db(tmp_path, "tooltime")
    fx.populate_baseline(
        db,
        prettify_latency_ms=1_000,
        attrs_latency_ms=1_000,
        export_latency_ms=1_111,
        delivery_latency_ms=9_999,
    )
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    # 1000 (prettify) + 1000 (attrs) + 1111 (export_clicked) = 3111, NOT 11999.
    tooltime = _gate_line(result.stdout, "tooltime_p50")
    assert "3111 ms" in tooltime
    assert "11999 ms" not in tooltime
    assert "9999" not in tooltime
    assert result.returncode == 0


def test_tool_time_sums_retries_but_point_attribute_takes_latest(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "retries")
    fx.populate_baseline(
        db,
        prettify_latency_ms=5_000,
        attrs_latency_ms=1_000,
        export_latency_ms=1_000,
        retries=(1_000, 2_000),
    )
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    # Point attribute (prettify_p50) takes the LATEST attempt = 5000.
    assert "5000 ms" in _gate_line(result.stdout, "prettify_p50")
    # Tool time SUMS 1000 + 2000 + 5000 + attrs 1000 + export 1000 = 10000.
    assert "10000 ms" in _gate_line(result.stdout, "tooltime_p50")
    assert "n=30 items (from 3 sellers)" in result.stdout, "one row per item, not per event"


def test_nearest_rank_percentile_is_hand_written(tmp_path: Path) -> None:
    """p50 of 1..30 = 15, p90 = 27 (index ceil(p*n)-1). No library quantile."""
    db, path = _db(tmp_path, "percentile")
    for index in range(1, 31):
        db.full_item(index, prettify_latency_ms=index)
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert "15 ms" in _gate_line(result.stdout, "prettify_p50")
    assert "27 ms" in _gate_line(result.stdout, "prettify_p90")
    assert "percentile=nearest-rank" in result.stdout


def test_delivery_is_not_a_prerequisite_for_tool_time(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "no-delivery")
    fx.populate_baseline(db, with_delivery=False)
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert "3000 ms" in _gate_line(result.stdout, "tooltime_p50")
    assert result.returncode == 0
