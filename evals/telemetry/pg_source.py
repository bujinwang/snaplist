"""Read-only Postgres adapter for the E0-T5 telemetry gate (R3).

Deliberately OPTIONAL: psycopg is imported lazily inside `_connect`, so merely importing this
module (or `source.select_source` with no `SNAPLIST_PG_DSN`) never requires psycopg to be
installed. The default `uv sync` omits the `pg` dependency group on purpose — see pyproject.toml.

Read discipline (identical to the SQLite adapter):
  * a single read-only connection (`default_transaction_read_only=on`),
  * never writes, never creates a table, never coerces an id,
  * the `usage_events` ledger self-join is the ONLY attribution source, because
    `job_refunded.item_id` is always NULL in production (`apps/api/usage.py::refund_one`),
  * NULL join keys from `0001_init.sql:231-232` SET NULL are classified as DEGRADED
    (evidence absence) exactly as on SQLite — the SAME `_classify_attribution` is reused.

The `pg` group (`psycopg[binary]`) is a PEP 735 OPTIONAL dependency group and is NOT installed by
a plain `uv sync`. Importing this module therefore performs NO top-level `import psycopg`; that
only happens inside `_connect`, which is reached solely when `PostgresEventSource.load()` runs and
the caller has explicitly opted in via `SNAPLIST_PG_DSN`.
"""

from __future__ import annotations

from dataclasses import replace

from evals.telemetry.model import SourceData
from evals.telemetry.source import _materialize


def _mask_dsn(dsn: str) -> str:
    """Drop credentials from a DSN for logging; keep host/db so a reader knows what was read."""
    without_scheme = dsn.split("://", 1)[-1]
    if "@" in without_scheme:
        hostpart = without_scheme.split("@", 1)[1]
        scheme = dsn.split("://", 1)[0]
        return f"{scheme}://{hostpart}"
    return dsn


def _connect(dsn: str):
    """Open a read-only Postgres connection. psycopg is imported HERE, not at module top."""
    import psycopg

    return psycopg.connect(
        dsn,
        options="-c default_transaction_read_only=on",
        row_factory=psycopg.rows.dict_row,
    )


class PostgresEventSource:
    """Read-only adapter over a Supabase/Postgres analytics database."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.source_label = f"postgres:{_mask_dsn(dsn)}"

    def load(self) -> SourceData:
        try:
            connection = _connect(self.dsn)
        except Exception:
            # Any connect/auth failure is 3a: source absent, never a fabricated empty result.
            return SourceData(source_label=self.source_label, present=False, events_table=False)
        try:
            with connection.cursor() as cur:
                cur.execute(
                    "select table_name from information_schema.tables "
                    "where table_schema = 'public'"
                )
                tables = {str(row["table_name"]) for row in cur.fetchall()}
            if "events" not in tables:
                return SourceData(source_label=self.source_label, present=True, events_table=False)
            data = _materialize(connection, tables, "postgres", backend="postgres")
            return replace(data, source_label=self.source_label)
        finally:
            connection.close()
