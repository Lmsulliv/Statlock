"""Spike 11: find the FINEST badge-bracket width that re-sums to the decade total.

Gate spike 09 showed per-INTEGER (width-1) badge atoms re-sum to only ~85% of the
12-decade total on both analytics endpoints, even with min_matches=0. The leading
explanation is that a match's team-AVERAGE badge is fractional, so integer-edged
brackets leave uncovered gaps between them (e.g. [9,10) is in neither [0,9] nor
[10,19]); the narrower the brackets, the more boundaries, the more matches fall in
the gaps. If that's the mechanism, the loss grows monotonically as the bracket
width shrinks, and no sub-decade width reconciles.

This sweeps several widths W, tiling 0..116 into [a, a+W-1] brackets, and prints
each tiling's sum as a ratio to the width-10 decade sum (the rated population we
ship) and to the single full-range call. Counter endpoint only (representative,
and the one the slider's matchup baseline reads). min_matches=0 throughout.

Run: python spikes/11_bracket_width_sweep.py [widths]   e.g. ... 5,3,2
~135 calls for widths 2,3,5 (+ decade + full), ~11 min at 1 req / 5 s.
"""
import json
import sys
import time

from _api import get

BASE = "https://api.deadlock-api.com/v1/analytics"
DAY = 86400
NOW = int(time.time())
MIN_UNIX = NOW - 30 * DAY
MAX_UNIX = NOW
FULL_MAX = 116
TOLERANCE = 0.02


def _span() -> str:
    return (f"min_unix_timestamp={MIN_UNIX}&max_unix_timestamp={MAX_UNIX}"
            f"&game_mode=normal&min_matches=0")


def _tiling(width: int) -> list[tuple[int, int]]:
    """Gapless [a, a+width-1] brackets tiling 0..116 (last clamped to 116)."""
    return [(a, min(a + width - 1, FULL_MAX)) for a in range(0, FULL_MAX + 1, width)]


def _get_sum(bmin: int, bmax: int) -> int:
    url = (f"{BASE}/hero-counter-stats?{_span()}"
           f"&min_average_badge={bmin}&max_average_badge={bmax}")
    for attempt in range(3):
        try:
            status, body = get(url)
            break
        except (TimeoutError, OSError) as e:
            if attempt == 2:
                raise
            print(f"  {type(e).__name__}, retrying [{bmin},{bmax}] (attempt {attempt + 2})")
    if status != 200:
        print(f"  HTTP {status} for [{bmin},{bmax}]: {body[:160]}")
        return 0
    return sum(r["matches_played"] for r in json.loads(body))


def _sum_tiling(width: int) -> tuple[int, int]:
    brackets = _tiling(width)
    total = sum(_get_sum(bmin, bmax) for bmin, bmax in brackets)
    return total, len(brackets)


def main() -> None:
    widths = [int(w) for w in (sys.argv[1] if len(sys.argv) > 1 else "5,3,2").split(",")]
    full = _get_sum(0, FULL_MAX)
    decade, _ = _sum_tiling(10)
    print(f"\n  full 0..116      : {full:,}")
    print(f"  decade (width 10): {decade:,}   ({decade / full:.4f} of full)\n")
    print(f"  {'width':>5} {'calls':>5} {'sum':>14} {'/decade':>9} {'/full':>8}  verdict")
    for w in sorted(set(widths)):
        total, n = _sum_tiling(w)
        vs_dec = total / decade if decade else 0.0
        vs_full = total / full if full else 0.0
        ok = "RECONCILES" if abs(1.0 - vs_dec) <= TOLERANCE else "leaks"
        print(f"  {w:>5} {n:>5} {total:>14,} {vs_dec:>9.4f} {vs_full:>8.4f}  {ok}")


if __name__ == "__main__":
    main()
