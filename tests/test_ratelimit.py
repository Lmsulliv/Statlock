"""Unit tests for ingest.ratelimit.TokenBucket (fake clock, no real sleeping)."""
from ingest.ratelimit import TokenBucket


class SimClock:
    """Fake time source: sleep() advances the clock instead of waiting."""

    def __init__(self, start: float = 1000.0):
        self.t = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def make_bucket(clock, **kwargs):
    kwargs.setdefault("jitter", lambda: 0.0)
    return TokenBucket(rate=0.2, capacity=1, clock=clock, sleep=clock.sleep, **kwargs)


def test_requests_are_at_least_five_seconds_apart():
    clock = SimClock()
    bucket = make_bucket(clock)
    times = []
    for _ in range(4):
        bucket.acquire()
        times.append(clock.t)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(gap >= 5.0 for gap in gaps), gaps


def test_no_wait_when_idle_long_enough():
    clock = SimClock()
    bucket = make_bucket(clock)
    bucket.acquire()
    clock.t += 60  # plenty of idle time
    before = clock.t
    bucket.acquire()
    assert clock.t == before  # no sleep needed


def test_capacity_one_means_no_bursts():
    # Hard rule 3: never exceed 1 request / 5 s; even after long idle the
    # bucket holds at most one token, so back-to-back calls still space out.
    clock = SimClock()
    bucket = make_bucket(clock)
    clock.t += 3600
    bucket.acquire()
    first = clock.t
    bucket.acquire()
    assert clock.t - first >= 5.0


def test_jitter_added_after_acquire():
    clock = SimClock()
    bucket = TokenBucket(rate=0.2, capacity=1, clock=clock, sleep=clock.sleep,
                         jitter=lambda: 1.5)
    bucket.acquire()
    assert 1.5 in clock.sleeps


def test_stamp_file_carries_politeness_across_processes(tmp_path):
    # A "previous process" wrote a stamp 1 second ago; a fresh bucket must
    # still wait out the remaining interval.
    stamp = tmp_path / "stamp"
    clock = SimClock(start=1000.0)
    stamp.write_text(str(clock.t - 1.0))
    bucket = make_bucket(clock, stamp_path=stamp)
    bucket.acquire()
    assert clock.t >= 1000.0 - 1.0 + 5.0
    # And it records its own request for the next process.
    assert float(stamp.read_text()) == clock.t
