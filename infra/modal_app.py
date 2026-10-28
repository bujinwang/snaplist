"""Modal GPU app — SCAFFOLD ONLY. Owner: backend + vton roles (E5/E4).

Modal is the job runner named in BRIEF.md ("job execution: jobs table + Modal, no Redis").
A FastAPI worker will claim rows with `SELECT ... FOR UPDATE SKIP LOCKED` and call into
`packages.vision`. Nothing here is wired up yet, and `modal` is deliberately **not** a
dependency of this repo: it is installed in the Modal image, not in the api image.

Deploy (once E5/E4 exist):

    pip install modal
    modal token set --token-id $MODAL_TOKEN_ID --token-secret $MODAL_TOKEN_SECRET
    modal deploy infra/modal_app.py

This module is never imported by the test suite — importing it without `modal` installed
must fail, not silently pass.
"""

from __future__ import annotations

APP_NAME = "snaplist-jobs"


def run_item_job(item_id: str) -> dict[str, str]:
    """Claim one `jobs` row and run the pipeline. Not implemented."""
    raise NotImplementedError(
        "infra/modal_app.py is a scaffold. See docs/PLAN.md ticket E5-T3 (job runner) "
        "and E4-T2 (CatVTON on Modal)."
    )


if __name__ == "__main__":
    raise SystemExit(
        "Scaffold only. Nothing to deploy yet — see docs/PLAN.md E5 before running this."
    )
