"""Spike 09 (GATE): do PER-INTEGER badge atoms partition matches cleanly?

Spike 08 proved the 12 decade brackets re-sum to ~96% of a single 0..116 call
(the missing ~4% is matches with an unknown/NULL team-average badge, which no
badge filter can capture). Before we switch baseline ingestion from decade
brackets to ONE row per integer badge (badge_min==badge_max), we must confirm
the finer partition re-sums to the SAME rated total the decades did -- not to
the full range. So the pass criterion here is:

    per-integer sum  ~=  decade-bracket sum     (within TOLERANCE)

NOT per-integer ~= full-range (that would always fail by the known ~4% tail).

Probes BOTH endpoints we bracket (hero-counter-stats, item-stats?bucket=hero).
The item endpoint also re-checks that bucket=hero honors the badge filter: if it
were ignored, every atom would be identical and the per-integer sum would
overshoot ~117x.

~130 throttled calls per endpoint (117 atoms + 12 decades + 1 full), ~260 total
=> ~22 min at 1 req / 5 s. One-time manual gate, not the nightly job.
Run: python spikes/09_per_badge_partition.py
"""
import json
import time

from _api import OUT, get, save_raw

BASE = "https://api.deadlock-api.com/v1/analytics"
DAY = 86400

# One recent era span. Explicit timestamps: the analytics window otherwise
# defaults to a trailing 30 days (api-findings contradiction 7).
NOW = int(time.time())
MIN_UNIX = NOW - 30 * DAY
MAX_UNIX = NOW

# The 12 decade brackets we ship today (spike 08). Their sum is the rated total
# the per-integer atoms must reproduce.
DECADE_BRACKETS = [(0, 9)] + [(t * 10, t * 10 + 9) for t in range(1, 11)] + [(110, 116)]
ATOMS = [(v, v) for v in range(0, 117)]   # one per integer badge 0..116
FULL = (0, 116)

# More than this fractional gap between per-integer and decade sums fails.
TOLERANCE = 0.02


def _span() -> str:
    # Analytics wants the STRING game_mode variant (normal/street_brawl/...),
    # not the numeric 1 the match-metadata field uses (api-findings).
    return f"min_unix_timestamp={MIN_UNIX}&max_unix_timestamp={MAX_UNIX}&game_mode=normal"


def _sum(name: str, url: str, count_field: str, *, save: bool = False) -> int:
    for attempt in range(3):     # retry transient read timeouts (big payloads)
        try:
            status, body = get(url)
            break
        except (TimeoutError, OSError) as e:
            if attempt == 2:
                raise
            print(f"  {type(e).__name__}, retrying {name} (attempt {attempt + 2})")
    if save:
        save_raw(name, body)
    if status != 200:
        print(f"  HTTP {status} for {name}: {body[:200]}")
        return 0
    return sum(row[count_field] for row in json.loads(body))


def _sum_over(label: str, endpoint: str, path: str, count_field: str,
              brackets: list[tuple[int, int]], extra: str) -> int:
    total = 0
    for bmin, bmax in brackets:
        total += _sum(
            f"09_{endpoint}_{label}_{bmin}_{bmax}.json",
            f"{BASE}/{path}?{_span()}{extra}"
            f"&min_average_badge={bmin}&max_average_badge={bmax}",
            count_field,
        )
    print(f"  {label:11s} sum ({len(brackets):3d} calls): {total:,}")
    return total


def probe(endpoint: str, path: str, count_field: str, extra: str = "") -> None:
    print(f"\n=== {endpoint} ===")
    bmin, bmax = FULL
    full = _sum(
        f"09_{endpoint}_full.json",
        f"{BASE}/{path}?{_span()}{extra}&min_average_badge={bmin}&max_average_badge={bmax}",
        count_field,
        save=True,
    )
    print(f"  full 0..116 sum (  1 calls): {full:,}")
    decades = _sum_over("decade", endpoint, path, count_field, DECADE_BRACKETS, extra)
    atoms = _sum_over("per-integer", endpoint, path, count_field, ATOMS, extra)

    vs_decade = atoms / decades if decades else 0.0
    vs_full = atoms / full if full else 0.0
    print(f"\n  per-integer / decade : {vs_decade:.4f}   (gate compares THIS)")
    print(f"  per-integer / full   : {vs_full:.4f}   (context; ~0.96 expected)")
    if decades and abs(1.0 - vs_decade) <= TOLERANCE:
        print(f"  GATE PASS ({endpoint}): per-integer atoms re-sum to the decade total.")
    elif vs_decade > 1.0 + TOLERANCE:
        print(f"  GATE FAIL ({endpoint}): per-integer OVERSHOOTS decades -- badge filter"
              " likely ignored; atoms would double-count. STOP.")
    else:
        print(f"  GATE FAIL ({endpoint}): per-integer is materially BELOW the decade total"
              " -- single-badge resolution drops matches. STOP.")


def main() -> None:
    # min_matches=0: hero-counter-stats defaults min_matches > 0, which drops
    # (hero, enemy) pairs below the threshold. Narrow badge windows have fewer
    # matches per pair than a whole decade, so more pairs fall under the default
    # and the per-integer sum undercounts. Forcing min_matches=0 includes every
    # pair so the partition can be compared apples-to-apples.
    probe("counter", "hero-counter-stats", "matches_played", extra="&min_matches=0")
    probe("item", "item-stats", "matches", extra="&bucket=hero&min_matches=0")
    print(f"\n(raw responses archived under {OUT})")


if __name__ == "__main__":
    main()
