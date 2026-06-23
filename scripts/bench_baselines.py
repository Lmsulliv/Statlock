"""Read-only benchmark for the baseline cache (api/cache.py).

Why this exists: the baseline lookups were turned into a cached layer to stop
re-summing the decade-bracket baseline rows on every analytics request. This
script measures that the cache actually pays off, and -- just as importantly --
makes the share of latency that the cache CANNOT remove visible: the personal
self-joins. That split is the evidence for whether the next step (materializing
per-account aggregates) is justified, which is deliberately NOT built yet.

It reads the SAME database the app reads (api.config.db_path) through the SAME
connection helper (tracker.db.connect) and issues SELECTs only -- no writes, no
schema changes, no API calls.

Run:  python -m scripts.bench_baselines   (or  python scripts/bench_baselines.py)
"""
import sys
import time
from pathlib import Path

# scripts/ lives one level below the project root; put the root on the path so
# `import api...` / `import tracker...` resolve exactly like the app's do.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import cache, queries, service
from api.config import db_path
from api.scope import FULL_BADGE_MAX, FULL_BADGE_MIN, Scope, snap_badge_range
from tracker.db import connect

ITERS = 50   # repeats per measurement; perf_counter resolution wants a few


def _bench(label, fn, iters=ITERS):
    """Average wall-clock seconds per call of `fn`, plus the label, as a row."""
    fn()  # warm code paths (imports, sqlite statement prep) before timing
    start = time.perf_counter()
    for _ in range(iters):
        fn()
    per_call = (time.perf_counter() - start) / iters
    return label, per_call


def _scopes(account_id):
    """A representative spread of the scope axes the baseline SQL keys on:
    full vs. narrow rank range, all-time vs. era-filtered, overall vs. laning."""
    full = snap_badge_range(FULL_BADGE_MIN, FULL_BADGE_MAX)
    narrow = snap_badge_range(20, 49)
    variants = [
        dict(badge_min=full[0], badge_max=full[1]),
        dict(badge_min=narrow[0], badge_max=narrow[1]),
        dict(badge_min=full[0], badge_max=full[1], in_lane=True),
    ]
    return [Scope(account_id=account_id, **v) for v in variants]


def _print_row(label, per_call):
    print(f"  {label:<46} {per_call * 1e3:8.3f} ms/call")


def main() -> None:
    conn = connect(db_path())
    account_id = queries.resolve_self_account_id(conn)
    snapshot = queries.latest_snapshot_id(conn)

    print("=" * 72)
    print("Baseline cache benchmark")
    print("=" * 72)
    if account_id is None:
        print("No self account in the DB -- nothing to benchmark. Ingest first.")
        return
    if snapshot is None:
        print("No baseline snapshot in the DB -- baselines are empty, so the cache")
        print("has nothing to do. Run the baseline refresh first.")
        return

    scopes = _scopes(account_id)
    heroes = [r["hero_id"] for r in conn.execute(
        "SELECT DISTINCT hero_id FROM match_players WHERE account_id = ?"
        " ORDER BY hero_id LIMIT 5", (account_id,)).fetchall()]
    print(f"self account = {account_id}, snapshot = {snapshot}, "
          f"{len(scopes)} scopes, {len(heroes)} heroes\n")

    # 1) Isolate where the time goes for the matchups path: the cacheable baseline
    #    re-sum vs. the personal self-join that the cache cannot touch.
    print("Per-query cost (matchups, uncached, averaged over scopes):")
    base = _scopes(account_id)[0]
    _print_row(*_bench("baseline_matchups (re-sum, now cached)",
                       lambda: queries.baseline_matchups(conn, base, snapshot)))
    _print_row(*_bench("personal_matchups (self-join, NOT cached)",
                       lambda: queries.personal_matchups(conn, base, my_hero_id=None)))
    print()

    # 2) Full service call, cold cache (baseline recomputed every call) vs. warm
    #    cache (baseline served from memory). The delta is what the cache buys.
    def call_matchups():
        for sc in scopes:
            service.matchups(conn, sc)

    cache.BASELINE_CACHE.clear()
    cache.BASELINE_CACHE.reset_stats()
    cold = _cold_bench(call_matchups)
    warm_label, warm = _bench("service.matchups x scopes (warm cache)", call_matchups)

    print("End-to-end matchups (all scopes per call):")
    _print_row("service.matchups x scopes (cold cache)", cold)
    _print_row(warm_label, warm)
    if warm > 0:
        print(f"  -> warm cache is {cold / warm:5.2f}x faster end-to-end\n")

    # 3) Same for the items path, if we have heroes to ask about.
    if heroes:
        def call_items():
            for sc in scopes:
                for hid in heroes:
                    service.items(conn, sc, hid)

        cache.BASELINE_CACHE.clear()
        cache.BASELINE_CACHE.reset_stats()
        cold_i = _cold_bench(call_items)
        warm_i_label, warm_i = _bench("service.items x scopes x heroes (warm)", call_items)
        print("End-to-end items (all scopes x heroes per call):")
        _print_row("service.items x scopes x heroes (cold cache)", cold_i)
        _print_row(warm_i_label, warm_i)
        if warm_i > 0:
            print(f"  -> warm cache is {cold_i / warm_i:5.2f}x faster end-to-end\n")

    stats = cache.BASELINE_CACHE.stats()
    total = stats["hits"] + stats["misses"]
    rate = (stats["hits"] / total * 100) if total else 0.0
    print(f"Cache: {stats['hits']} hits / {stats['misses']} misses "
          f"({rate:.1f}% hit rate), {stats['size']} entries held")
    print("\nReading this: if the self-join row above dominates the warm end-to-end")
    print("time, the baseline cache has done its job and the next lever is the")
    print("per-account summary table -- gate that on these numbers.")
    conn.close()


def _cold_bench(fn, iters=ITERS):
    """Average per-call time with the baseline cache cleared before EACH call, so
    every call pays the full re-sum -- the pre-cache cost we are improving on."""
    fn()
    start = time.perf_counter()
    for _ in range(iters):
        cache.BASELINE_CACHE.clear()
        fn()
    return (time.perf_counter() - start) / iters


if __name__ == "__main__":
    main()
