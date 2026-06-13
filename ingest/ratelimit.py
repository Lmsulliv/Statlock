"""Token-bucket rate limiter for deadlock-api requests.

A token bucket holds up to `capacity` tokens and refills at `rate` tokens
per second; each request costs one token, and a caller with no token sleeps
until one accrues. With capacity=1 and rate=0.2 this degenerates into
"at least 5 seconds between requests" — which is exactly what hard rule 3
demands. The spec's example suggests allowing bursts (capacity 5), but a
burst of back-to-back requests would momentarily exceed 1 req / 5 s, so we
keep capacity at 1 and leave it a parameter for a future, justified tune-up.

The timestamp of the last request is also persisted to a stamp file shared
with tracker/refresh_assets.py, so politeness holds across separate
processes and restarts, not just within one.
"""
import logging
import random
import time
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_STAMP = Path(__file__).parent.parent / "data" / ".last_deadlock_request"


def _default_jitter() -> float:
    # 0-2 s of randomness after each call: perfectly periodic traffic is the
    # signature of a misbehaving bot.
    return random.uniform(0, 2)


class TokenBucket:
    def __init__(
        self,
        rate: float = 0.2,
        capacity: float = 1.0,
        *,
        clock=time.time,
        sleep=time.sleep,
        jitter=_default_jitter,
        stamp_path: Path | None = None,
    ):
        self.rate = rate
        self.capacity = capacity
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._stamp_path = stamp_path
        # Start with only the tokens earned since the last recorded request
        # (possibly by another process); a recent stamp means starting empty
        # and waiting out the remaining interval.
        self._last_refill = self._clock()
        self._tokens = min(capacity, (self._last_refill - self._read_stamp()) * rate)

    def _read_stamp(self) -> float:
        if self._stamp_path is not None:
            try:
                return float(self._stamp_path.read_text())
            except (FileNotFoundError, ValueError):
                pass
        return 0.0

    def _write_stamp(self) -> None:
        if self._stamp_path is not None:
            self._stamp_path.parent.mkdir(parents=True, exist_ok=True)
            self._stamp_path.write_text(str(self._clock()))

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._last_refill) * self.rate)
        self._last_refill = now

    def acquire(self) -> None:
        """Block until a token is available, spend it, then add jitter."""
        self._refill()
        if self._tokens < 1.0:
            wait = (1.0 - self._tokens) / self.rate
            log.debug("rate limit: waiting %.1fs for a token", wait)
            self._sleep(wait)
            self._refill()
        self._tokens -= 1.0
        extra = self._jitter()
        if extra > 0:
            self._sleep(extra)
        self._write_stamp()
