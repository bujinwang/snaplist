"""`session_export_burst_60m` is computed from real `export_delivered` events now.

It stays report-only: these tests pin the measurement, not a new gate.
"""

from __future__ import annotations

from datetime import timedelta

from evals.telemetry.compute import export_burst, parse_now, window_bounds
from evals.telemetry.model import EventRow, SourceData

NOW = parse_now("2026-11-05T00:00:00.000000Z")
START, END = window_bounds(NOW)


def _row(index: int, *, when_minutes_ago: int, item: str, session: str = "sess-a",
         event: str = "export_delivered") -> EventRow:
    stamp = (NOW - timedelta(minutes=when_minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return EventRow(
        event_id=f"burst-{index:04d}-1111-4111-8111-111111111111",
        event=event,
        received_at=stamp,
        client_ts=stamp,
        session_id=session,
        subject="usr_00000001-3333-4333-8333-333333333333",
        app_version="1.4.0",
        platform="ios",
        item_id=item,
    )


def _data(*rows: EventRow) -> SourceData:
    return SourceData(present=True, events_table=True, events=tuple(rows))


def test_no_delivered_exports_is_unmeasured_not_zero() -> None:
    n, value, text = export_burst(_data(), START, END)
    assert (n, value) == (0, None)
    assert text == "UNMEASURED (n=0 sessions with a delivered export)"


def test_three_distinct_exports_within_an_hour_burst() -> None:
    rows = [
        _row(1, when_minutes_ago=45, item="item-1"),
        _row(2, when_minutes_ago=30, item="item-2"),
        _row(3, when_minutes_ago=5, item="item-3"),
    ]
    n, value, text = export_burst(_data(*rows), START, END)
    assert (n, value) == (1, 1.0)
    assert "1 with >=3 distinct items in 60m" in text


def test_exactly_sixty_minutes_is_inside_the_window() -> None:
    rows = [
        _row(1, when_minutes_ago=61, item="item-1"),
        _row(2, when_minutes_ago=31, item="item-2"),
        _row(3, when_minutes_ago=1, item="item-3"),
    ]
    assert export_burst(_data(*rows), START, END)[1] == 1.0


def test_sixty_one_minutes_is_outside_the_window() -> None:
    rows = [
        _row(1, when_minutes_ago=62, item="item-1"),
        _row(2, when_minutes_ago=31, item="item-2"),
        _row(3, when_minutes_ago=1, item="item-3"),
    ]
    n, value, _ = export_burst(_data(*rows), START, END)
    assert (n, value) == (1, 0.0)


def test_duplicate_delivery_of_one_item_is_not_a_burst() -> None:
    rows = [
        _row(1, when_minutes_ago=40, item="same-item"),
        _row(2, when_minutes_ago=20, item="same-item"),
        _row(3, when_minutes_ago=2, item="same-item"),
    ]
    assert export_burst(_data(*rows), START, END)[1] == 0.0


def test_clicks_do_not_count_as_deliveries() -> None:
    rows = [
        _row(1, when_minutes_ago=40, item="item-1"),
        _row(2, when_minutes_ago=20, item="item-2"),
        _row(3, when_minutes_ago=2, item="item-3", event="export_clicked"),
    ]
    n, value, _ = export_burst(_data(*rows), START, END)
    assert (n, value) == (1, 0.0)


def test_sessions_are_the_denominator() -> None:
    bursting = [
        _row(1, when_minutes_ago=40, item="a-1", session="sess-a"),
        _row(2, when_minutes_ago=20, item="a-2", session="sess-a"),
        _row(3, when_minutes_ago=2, item="a-3", session="sess-a"),
    ]
    quiet = [
        _row(4, when_minutes_ago=10, item="b-1", session="sess-b"),
        _row(5, when_minutes_ago=2, item="b-2", session="sess-b"),
    ]
    n, value, text = export_burst(_data(*bursting, *quiet), START, END)
    assert (n, value) == (2, 0.5)
    assert "n=2 sessions with >=1 delivery; 1 with >=3" in text
