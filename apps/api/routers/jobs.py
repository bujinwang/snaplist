"""`GET /v1/jobs/{job_id}` — job polling for the mobile progress bar.

Read-only on purpose. CONTRACTS.md §3 moves jobs from a worker holding a row lock, never from
an HTTP call: a seller who could PATCH a job could set `state=failed` and farm quota refunds.
The state machine is importable for tests and for the worker (`apps/api/jobs/state_machine.py`),
but there is no route that exposes it.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from apps.api import errors, web
from apps.api.auth import Caller, current_caller
from apps.api.repository import Repository
from apps.api.schemas import ErrorResponse, Job

router = APIRouter(prefix="/v1", tags=["jobs"])


def _as_uuid(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise errors.not_found(f"No job {raw} for this account.") from exc


@router.get(
    "/jobs/{job_id}",
    response_model=Job,
    responses={404: {"model": ErrorResponse}},
    summary="Poll one job's state",
)
def get_job(
    job_id: str,
    request: Request,
    caller: Caller = Depends(current_caller),
) -> Job:
    """`queued` is a real answer, not a placeholder.

    A worker exists (`python -m apps.api.jobs.worker`) and runs FULL jobs through the vision
    pipeline, but nothing claims a job inside the API process, so a fresh job is `queued` until
    that process runs. A fake `done` would hand the client asset URLs that do not exist.
    """
    repository: Repository = web.repository_of(request)
    record = repository.get_job(_as_uuid(job_id), user_id=caller.user_id)
    if record is None:
        raise errors.not_found(f"No job {job_id} for this account.")
    return record.to_job()
