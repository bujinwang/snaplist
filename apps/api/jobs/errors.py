"""Typed job failures — a stage that fails for a reason the vocabulary can name.

Why this file exists
--------------------
`worker.run_once` maps exceptions onto job outcomes, and until now it only had two branches:
a stub that does not exist (terminal, deterministic) and "anything else" (retryable, because
unknown is treated as transient until proven otherwise). That is the right policy for bugs, and
the wrong policy for a stage that *knows* why it failed.

A pipeline stage does know. "The photo has no garment in it" is not a bug and retrying it three
times will not find a garment; "the VTON provider returned 503" is not a bug either, but retrying
it will often work. Neither is expressible as `Exception`, and encoding both as `internal` throws
away the only information a seller would find useful. `JobFailure` carries that information
explicitly: a `reason_code` from the frozen job vocabulary, a `retryable` flag describing the
failure's own nature rather than the worker's guess, and the stage that raised it.

The vocabulary is frozen
------------------------
`ReasonCode` in `apps/api/schemas.py` is mirrored by a PG CHECK constraint and by
`packages/shared` — there is no `detail`-only code and no free-form reason. A stage that cannot
name its failure with one of those five should not raise this type; it should let the generic
`Exception` branch treat it as an unknown, retryable internal error, which is exactly what that
branch is for.
"""

from __future__ import annotations

from apps.api.schemas import ReasonCode


class JobFailure(RuntimeError):
    """A stage failed for a reason the job vocabulary can name.

    Attributes
    ----------
    reason_code:
        One of the five frozen `ReasonCode` values. It reaches the seller-facing job row, so it
        decides whether they are told "we could not find a garment" or "something went wrong".
    retryable:
        Whether *this kind of failure* is worth another attempt — a property of the failure, not
        of the worker. The attempts budget still bounds it: a retryable failure on its third claim
        terminates like any other.
    stage:
        The pipeline stage that raised, for `error_detail`. Free-form: it names a place in our
        code, not a value in a contract.
    detail:
        The human-readable cause, also for `error_detail`.
    """

    def __init__(
        self,
        *,
        reason_code: ReasonCode,
        retryable: bool,
        stage: str,
        detail: str,
    ) -> None:
        self.reason_code = reason_code
        self.retryable = retryable
        self.stage = stage
        self.detail = detail
        # One message, and it is the whole payload: `_detail` truncates it into `error_detail`,
        # where a traceback would help nobody. `str(exc)` therefore names stage, reason and detail
        # — that string is what a seller-facing job row ends up holding.
        super().__init__(
            f"{stage} failed ({reason_code}, retryable={retryable}): {detail}"
        )
