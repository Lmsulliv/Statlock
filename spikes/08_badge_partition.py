"""Spike 08 (GATE): do the 11 tier badge brackets partition matches cleanly?

Before we switch baseline ingestion from one 0..116 row per era span to 11
per-tier rows, we must confirm that re-summing the tiers reproduces the full
range. The baseline query (api/queries.py) re-sums brackets with a CONTAINMENT
predicate, so a full-range scope sums all 11 tier rows; that's only correct if
the 11 tiers add up to the single 0..116 call. If matches whose team-average
badge lands in a gap (e.g. 47-50) or is unknown get dropped, the bracketed sum
is materially lower and re-summing would silently undercount -> STOP.

Probes BOTH endpoints we intend to bracket (hero-counter-stats, item-stats?
bucket=hero); the item endpoint also checks that bucket=hero actually honors
the badge filter (an ignored filter would make all 11 tiers identical and
OVERCOUNT 11x). Same sum method on both sides, so the ratio is what matters,
not the absolute (rows double-count matches across hero pairs/items alike).

24 throttled calls (~2 min at 1 req / 5 s). Run: python spikes/08_badge_partition.py
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

# Gap-free contiguous decade brackets tiling all of 0..116 (no holes, no overlap):
# [0,9],[10,19],...,[100,109],[110,116]. These align with the scope snap rule
# ((badge//10)*10 edges), so the gate validates exactly the brackets we ship.
# Unlike subtier-1..6 brackets, they claim the inter-tier team-average badges
# (17-19, 27-29, ...) that the first run dropped. 12 brackets.
TIER_BRACKETS = [(0, 9)] + [(t * 10, t * 10 + 9) for t in range(1, 11)] + [(110, 116)]
FULL = (0, 116)

# More than this fractional shortfall fails the gate.
TOLERANCE = 0.02


def _span() -> str:
    # Analytics wants the STRING game_mode variant (normal/street_brawl/...),
    # NOT the numeric 1 the match-metadata field uses. Sending game_mode=1 400s
    # ("unknown variant `1`"); the existing _counter_url/_item_stats_url have
    # this bug. Recorded in api-findings as a new contradiction.
    return f"min_unix_timestamp={MIN_UNIX}&max_unix_timestamp={MAX_UNIX}&game_mode=normal"


def _sum(name: str, url: str, count_field: str) -> int:
    status, body = get(url)
    save_raw(name, body)
    if status != 200:
        print(f"  HTTP {status} for {name}: {body[:200]}")
        return 0
    total = sum(row[count_field] for row in json.loads(body))
    print(f"  {name}: rows={len(json.loads(body))}, sum({count_field})={total:,}")
    return total


def probe(endpoint: str, path: str, count_field: str, extra: str = "") -> None:
    print(f"\n=== {endpoint} ===")
    bmin, bmax = FULL
    single = _sum(
        f"08_{endpoint}_full.json",
        f"{BASE}/{path}?{_span()}{extra}&min_average_badge={bmin}&max_average_badge={bmax}",
        count_field,
    )

    bracketed = 0
    for bmin, bmax in TIER_BRACKETS:
        bracketed += _sum(
            f"08_{endpoint}_{bmin}_{bmax}.json",
            f"{BASE}/{path}?{_span()}{extra}&min_average_badge={bmin}&max_average_badge={bmax}",
            count_field,
        )

    ratio = bracketed / single if single else 0.0
    print(f"\n  single 0..116 sum : {single:,}")
    print(f"  11-bracket sum    : {bracketed:,}")
    print(f"  bracketed / single: {ratio:.4f}")
    if single and abs(1.0 - ratio) <= TOLERANCE:
        print(f"  GATE PASS ({endpoint}): brackets partition matches cleanly.")
    elif ratio > 1.0 + TOLERANCE:
        print(f"  GATE FAIL ({endpoint}): bracketed sum OVERSHOOTS -- badge filter"
              " likely ignored (e.g. bucket=hero), tiers would double-count.")
    else:
        print(f"  GATE FAIL ({endpoint}): bracketed sum is materially LOWER --"
              " gaps/unknown badges dropped; re-summing would undercount. STOP.")


def main() -> None:
    probe("counter", "hero-counter-stats", "matches_played")
    probe("item", "item-stats", "matches", extra="&bucket=hero")
    print(f"\n(raw responses archived under {OUT})")


if __name__ == "__main__":
    main()
