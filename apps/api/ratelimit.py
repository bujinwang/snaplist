"""In-process token-bucket rate limiter for `POST /v1/events`.

This module is deliberately **Free of any FastAPI import** so it can be unit-tested and
swapped in a test fixture without a request/response context. It is a pure token-bucket
implementation keyed on a caller identity (a `uuid.UUID`).

Lifecycle / scoping
-------------------
The limiter is instantiated **inside `create_app()`** (see `apps/api/main.py`) and stored on
`app.state.event_rate_limiter`. It is therefore **per-`create_app()` instance**, never a
module-global singleton. A module-global would share one bucket map across an entire test
session and break currently-green tests that post events; per-instance state keeps each
app (and, in the test suite, each test-built app) independent. See `docs/QA.md` §10 R8 for
the operational caveats (per-process only; `local_dev` resets on re-install; the 120 / 12 s
threshold is a product decision, not an observed load number).

The bucket is refilled from wall-clock time. The default clock is `time.monotonic`, which is
monotonic with respect to NTP steps, so a clock correction cannot make the bucket jump.

The limiter answers two questions:

* `allow(user_id)` — does this caller have a token *right now*? If so, consume one and return
  `True`; otherwise `False`. Refusal does **not** mutate the bucket, so a caller that is out
  of tokens keeps accruing refill from the original `last_ts` and is not penalised twice.
* `retry_after_seconds(user_id)` — how many seconds until the next token, for the `Retry-After`
  header.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from uuid import UUID

#: Burst capacity: a caller may fire this many requests back-to-back before the bucket empties.
EVENT_BURST_CAPACITY = 120

#: Seconds to regenerate one token. One token / 12 s means the full burst refills in
#: 120 * 12 = 1440 s (24 min); a steady 5 req/min is well under the replenishment rate.
EVENT_REFILL_SECONDS = 12.0

#: HTTP status returned when the bucket is empty.
STATUS_TOO_MANY_REQUESTS = 429


class EventRateLimiter:
    """A per-caller token bucket, held on `app.state` by one `create_app()` instance.

    `buckets` maps `user_id -> [tokens, last_ts]`. Both entries are floats; the list is used so
    the bucket can be mutated in place (refill + consume) without re-inserting into the dict.
    """

    def __init__(
        self,
        *,
        capacity: int = EVENT_BURST_CAPACITY,
        refill_seconds: float = EVENT_REFILL_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        """Build a fresh, empty limiter.

        Args:
            capacity: tokens a bucket starts with and refills up to.
            refill_seconds: seconds to regenerate one token.
            now: clock source; defaults to `time.monotonic` (NTP-skew safe).
        """
        self._capacity = capacity
        self._refill_seconds = refill_seconds
        self._now = now
        # user_id -> [tokens, last_ts]
        self._buckets: dict[UUID, list[float]] = {}

    def allow(self, user_id: UUID) -> bool:
        """Consume a token if available, else refuse.

        Lazy refill: `tokens = min(capacity, tokens + (now - last) / refill_seconds)`. If the
        refilled amount is at least 1, consume one token (and advance `last_ts` to `now`) and
        return `True`. Otherwise return `False` **without mutating the bucket**, so refill keeps
        accumulating from the original `last_ts` until a token is actually available.
        """
        current = self._now()
        bucket = self._buckets.get(user_id)
        if bucket is None:
            # First time we see this caller: full bucket, no refill needed.
            self._buckets[user_id] = [float(self._capacity), current]
            return True

        tokens, last_ts = bucket
        refilled = min(
            float(self._capacity),
            tokens + (current - last_ts) / self._refill_seconds,
        )
        if refilled >= 1.0:
            bucket[0] = refilled - 1.0
            bucket[1] = current
            return True
        # Refusal: leave the bucket exactly as it was so the next attempt refills from `last_ts`.
        return False

    def retry_after_seconds(self, user_id: UUID) -> float:
        """Seconds until the next token, for the `Retry-After` header.

        Returns `0.0` when the bucket is absent (never seen) or already holds at least one
        token; otherwise `(1 - tokens) * refill_seconds`.
        """
        bucket = self._buckets.get(user_id)
        if bucket is None or bucket[0] >= 1.0:
            return 0.0
        return (1.0 - bucket[0]) * self._refill_seconds
