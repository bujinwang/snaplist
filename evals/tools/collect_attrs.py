"""E3-T3 ONE-SHOT COLLECTOR — 40 real VLM calls, writes `evals/reports/attrs_predictions.json`.

    python -m evals.tools.collect_attrs

This is the ONLY module in this ticket that touches the network or reads a credential, and it
is deliberately NOT part of `evals.run`, `pytest` or `make test`. Collection and computation
are split on purpose: the harness reads the frozen artifact and stays zero-network.

What it does, in order:

1. Parse `.env` LINE BY LINE IN PYTHON (`k, v = line.split("=", 1)`, skipping comments and
   blank lines) into `os.environ`. **Never `source .env` / `set -a; . ./.env`** — the shell
   eats the double quotes inside `SNAPLIST_VLM_EXTRA_BODY` and the http adapter then rejects it.
   Nothing is written back to disk, and no value is ever printed.
2. Read all 40 entries of `evals/golden/manifest.json` (READ ONLY — the copyright-frozen zone).
3. For each entry, read the photo bytes and call `get_extractor().extract(photo_bytes)`.
4. Write predictions + metadata to `evals/reports/attrs_predictions.json`. No photo bytes, no
   base64, no API key, no authenticated URL fragment, and no secret substring in any error text.

Failure policy: one photo failing (transport error, schema violation, `AttributesUnavailable`)
does NOT abort the batch. It is recorded as `status=failed` with `error_type` +
`error_message`, and `evals/metrics/attrs.py` counts that entry in `n` as a MISS on all four
fields. Nothing is skipped to flatter an accuracy.

Retry policy, stated so token totals cannot be misleading: the http adapter already performs
EXACTLY ONE schema-repair retry and sums `prompt_tokens` / `completion_tokens` across both
calls in `StageMeta.notes`. This collector deliberately adds NO second retry layer: an outer
retry that failed would consume tokens that `last_meta` cannot observe, so the totals would
silently undercount. Per-entry `attempts` / `retry_count` / `repaired` record the adapter's
own retry count. A final failure is recorded as failed, never dressed up as a success.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals import git_sha

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV = ROOT / ".env"
DEFAULT_MANIFEST = ROOT / "evals" / "golden" / "manifest.json"
DEFAULT_OUTPUT = ROOT / "evals" / "reports" / "attrs_predictions.json"

FIELDS: tuple[str, ...] = ("category", "colors", "pattern", "material")
REQUIRED_ENV_NAMES: tuple[str, ...] = (
    "SNAPLIST_ATTRS_PROVIDER",
    "SNAPLIST_VLM_BASE_URL",
    "SNAPLIST_VLM_MODEL",
    "VLM_API_KEY",
)
_SECRET_WORDS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")


def load_env(path: Path) -> tuple[int, list[str]]:
    """Parse `.env` in Python and put it into `os.environ`. Returns (count, secret values).

    The secret values are returned ONLY so `_redact()` can scrub them out of error strings;
    they are never printed, logged, or written to any file. Skipping comment/blank lines and
    splitting on the first `=` keeps JSON values such as `SNAPLIST_VLM_EXTRA_BODY` intact.
    """
    if not path.is_file():
        raise FileNotFoundError(f"env file not found: {path}")
    count = 0
    secrets: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        os.environ[key] = value
        count += 1
        if value and any(word in key.upper() for word in _SECRET_WORDS):
            secrets.append(value)
    return count, secrets


def _redact(text: object, secrets: list[str]) -> str:
    """Scrub credentials out of anything that could land in the artifact, then bound its size."""
    cleaned = str(text)
    for secret in secrets:
        if len(secret) >= 4:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    cleaned = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", cleaned)
    cleaned = re.sub(r"(?i)(authorization[\"'\s:=]+)[^\s,}\"']+", r"\1[REDACTED]", cleaned)
    return cleaned[:500]


def _meta_int(meta: Any, key: str, default: int = 0) -> int:
    """Read one integer out of StageMeta.notes without ever guessing a positive value."""
    notes = getattr(meta, "notes", None) or {}
    value = notes.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return int(value)


def _blank_record(entry_id: str) -> dict[str, Any]:
    """The shape every row has, so a failed row cannot accidentally look like a hit."""
    return {
        "id": entry_id,
        "status": "failed",
        "category": None,
        "colors": None,
        "pattern": None,
        "material": None,
        "round_trip_seconds": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "attempts": 1,
        "retry_count": 0,
        "repaired": False,
        "error_type": None,
        "error_message": None,
    }


def collect(manifest_path: Path, output_path: Path, env_path: Path) -> int:
    """Run the batch and write the artifact. Returns a process-style exit code."""
    env_count, secrets = load_env(env_path)
    missing = [name for name in REQUIRED_ENV_NAMES if not os.environ.get(name)]
    if missing:
        # Names only. Never echo a value, not even a masked one.
        print(
            "VLM configuration is incomplete; missing env NAMES: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 2
    print(f"loaded {env_count} variables from {env_path.name} (values not printed)")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or not manifest:
        print("golden manifest must be a non-empty JSON array", file=sys.stderr)
        return 2
    print(f"collecting {len(manifest)} predictions from {manifest_path.name}")

    from packages.vision.attrs import get_extractor  # local: no provider SDK at import time

    try:
        extractor = get_extractor()
    except Exception as exc:  # noqa: BLE001 - a bad registry entry is a config error, not a photo
        print(_redact(f"cannot resolve extractor: {type(exc).__name__}: {exc}", secrets),
              file=sys.stderr)
        return 2

    entries: list[dict[str, Any]] = []
    golden_dir = manifest_path.parent
    for entry in manifest:
        entry_id = str(entry["id"])
        record = _blank_record(entry_id)
        photo_path = golden_dir / str(entry["path"])
        try:
            photo_bytes = photo_path.read_bytes()
            attributes = extractor.extract(photo_bytes)
            dumped = attributes.model_dump(mode="json")
            meta = getattr(extractor, "last_meta", None)
            record.update(
                {
                    "status": "measured",
                    **{field: dumped[field] for field in FIELDS},
                    "round_trip_seconds": float(getattr(meta, "seconds", 0.0) or 0.0),
                    "prompt_tokens": _meta_int(meta, "prompt_tokens"),
                    "completion_tokens": _meta_int(meta, "completion_tokens"),
                    "attempts": _meta_int(meta, "attempts", 1),
                    "retry_count": max(0, _meta_int(meta, "attempts", 1) - 1),
                    "repaired": bool((getattr(meta, "notes", None) or {}).get("repaired")),
                    "error_type": None,
                    "error_message": None,
                }
            )
        except Exception as exc:  # noqa: BLE001 - one bad photo must not abort the batch
            attempts = int(getattr(exc, "attempts", 1) or 1)
            record.update(
                {
                    "error_type": type(exc).__name__,
                    "error_message": _redact(f"{type(exc).__name__}: {exc}", secrets),
                    "attempts": attempts,
                    "retry_count": max(0, attempts - 1),
                }
            )
        entries.append(record)
        print(
            f"  {entry_id}: {record['status']} "
            f"({record['round_trip_seconds']:.3f}s, "
            f"{record['prompt_tokens']}+{record['completion_tokens']} tokens)"
        )

    n_measured = sum(1 for record in entries if record["status"] == "measured")
    n_failed = len(entries) - n_measured
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "git_sha": git_sha(),
        "provider": os.environ.get("SNAPLIST_ATTRS_PROVIDER", ""),
        "model": os.environ.get("SNAPLIST_VLM_MODEL", ""),
        "n_entries": len(entries),
        "n_measured": n_measured,
        "n_failed": n_failed,
        "prompt_tokens_total": sum(record["prompt_tokens"] for record in entries),
        "completion_tokens_total": sum(record["completion_tokens"] for record in entries),
        "round_trip_seconds_total": round(
            sum(record["round_trip_seconds"] for record in entries), 3
        ),
        "retry_count_total": sum(record["retry_count"] for record in entries),
        "token_accounting": (
            "token counts come from each successful extract() call's StageMeta.notes; the "
            "http adapter already sums usage across its own single schema-repair retry. "
            "Failed calls have no StageMeta, so any tokens they consumed are NOT observable "
            "and are NOT included in these totals. This is disclosed rather than estimated."
        ),
        "source_note": (
            "predictions only; no photo bytes, no base64, no credentials and no "
            "authenticated URL fragment. Photos are the locally frozen, uncommitted "
            "evals/golden set (all rights reserved)."
        ),
        "entries": entries,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {output_path} — n_entries={len(entries)} n_measured={n_measured} "
        f"n_failed={n_failed} "
        f"tokens={payload['prompt_tokens_total']}+{payload['completion_tokens_total']}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.tools.collect_attrs")
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    try:
        return collect(args.manifest, args.out, args.env)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"collection aborted: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
