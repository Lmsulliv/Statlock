"""Tests for the in-process baseline cache (api/cache.py).

The cache must be transparent (same numbers as the underlying query), share
entries across scope fields the baseline SQL ignores, and -- the part that
matters most -- never serve stale baselines across a refresh, including the
staggered refresh that keeps the SAME snapshot_id and only rewrites rows in
place (ingest.maintenance.refresh_baselines).
"""
import threading

import pytest

from api import cache, queries
from api.scope import Scope
from tests.conftest import HERO_ME, E_BRACKET, E_WEAK, SNAPSHOT


@pytest.fixture(autouse=True)
def _fresh_cache():
    """The module singleton is process-wide; reset it around every test so one
    test's entries and counters can't leak into the next."""
    cache.BASELINE_CACHE.clear()
    cache.BASELINE_CACHE.reset_stats()
    yield
    cache.BASELINE_CACHE.clear()
    cache.BASELINE_CACHE.reset_stats()


def _full_scope(**overrides) -> Scope:
    return Scope(**{"account_id": 1, **overrides})


# ── Transparency ─────────────────────────────────────────────────────────────

def test_hit_returns_same_numbers_and_skips_the_query(api_db, monkeypatch):
    scope = _full_scope()
    direct = queries.baseline_matchups(api_db, scope, SNAPSHOT)

    calls = {"n": 0}
    real = queries.baseline_matchups

    def counting(conn, sc, snap):
        calls["n"] += 1
        return real(conn, sc, snap)

    monkeypatch.setattr(queries, "baseline_matchups", counting)

    first = cache.cached_baseline_matchups(api_db, scope, SNAPSHOT)
    second = cache.cached_baseline_matchups(api_db, scope, SNAPSHOT)

    assert first == direct       # numbers unchanged by the cache
    assert second == direct
    assert calls["n"] == 1       # second call served from cache, no re-query
    stats = cache.BASELINE_CACHE.stats()
    assert (stats["hits"], stats["misses"]) == (1, 1)


def test_returned_dict_is_isolated_from_the_cache(api_db):
    scope = _full_scope()
    first = cache.cached_baseline_matchups(api_db, scope, SNAPSHOT)
    first.clear()   # mutating the caller's copy must not poison the cache
    second = cache.cached_baseline_matchups(api_db, scope, SNAPSHOT)
    assert second   # still the full result
    assert (HERO_ME, E_WEAK) in second


def test_item_stats_cache_matches_direct_query(api_db):
    scope = _full_scope()
    direct = queries.baseline_item_stats(api_db, scope, HERO_ME, SNAPSHOT)
    cached = cache.cached_baseline_item_stats(api_db, scope, HERO_ME, SNAPSHOT)
    assert cached == direct


# ── Cache key sensitivity ────────────────────────────────────────────────────

def test_badge_range_changes_key_and_result(api_db):
    full = _full_scope(badge_min=0, badge_max=116)
    narrow = _full_scope(badge_min=0, badge_max=30)

    full_res = cache.cached_baseline_matchups(api_db, full, SNAPSHOT)
    narrow_res = cache.cached_baseline_matchups(api_db, narrow, SNAPSHOT)

    # E_BRACKET is split across three brackets in the seed; the full range sums
    # all three (300), the narrow range only the [0,30] bracket (100).
    assert full_res[(HERO_ME, E_BRACKET)]["matches"] == 300
    assert narrow_res[(HERO_ME, E_BRACKET)]["matches"] == 100
    assert cache.BASELINE_CACHE.stats()["misses"] == 2   # two distinct keys


def test_in_lane_changes_key(api_db):
    overall = _full_scope(in_lane=False)
    laning = _full_scope(in_lane=True)

    overall_res = cache.cached_baseline_matchups(api_db, overall, SNAPSHOT)
    laning_res = cache.cached_baseline_matchups(api_db, laning, SNAPSHOT)

    assert overall_res          # seed rows are same_lane=0
    assert laning_res == {}     # no same-lane baseline rows seeded
    assert cache.BASELINE_CACHE.stats()["misses"] == 2


def test_era_ids_change_key(api_db):
    all_time = _full_scope(era_ids=None)
    era_two = _full_scope(era_ids=(2,))

    cache.cached_baseline_matchups(api_db, all_time, SNAPSHOT)
    cache.cached_baseline_matchups(api_db, era_two, SNAPSHOT)

    assert cache.BASELINE_CACHE.stats()["misses"] == 2


def test_account_id_and_game_mode_do_not_change_key(api_db):
    """The baseline SQL ignores account_id and game_mode, so scopes differing
    only in those fields must share one cached entry (the whole point)."""
    a = _full_scope(account_id=1, game_mode="1")
    b = _full_scope(account_id=999, game_mode="4")

    cache.cached_baseline_matchups(api_db, a, SNAPSHOT)
    cache.cached_baseline_matchups(api_db, b, SNAPSHOT)

    stats = cache.BASELINE_CACHE.stats()
    assert (stats["hits"], stats["misses"]) == (1, 1)   # second call hit


# ── Invalidation ─────────────────────────────────────────────────────────────

def test_new_snapshot_invalidates_even_for_same_key(api_db):
    scope = _full_scope()
    cache.cached_baseline_matchups(api_db, scope, SNAPSHOT)
    assert cache.BASELINE_CACHE.stats()["hits"] == 0

    # A brand-new snapshot moves MAX(snapshot_id): the version token changes, so
    # the next lookup (even at the SAME key) must reload rather than hit.
    api_db.execute("INSERT INTO baseline_snapshots(snapshot_id, fetched_at, notes)"
                   " VALUES (2, '2026-06-20T00:00:00+00:00', 'newer')")
    api_db.commit()

    cache.cached_baseline_matchups(api_db, scope, SNAPSHOT)
    stats = cache.BASELINE_CACHE.stats()
    assert (stats["hits"], stats["misses"]) == (0, 2)


def test_staggered_refresh_never_serves_stale_baselines(api_db):
    """The critical case: a staggered refresh keeps the same snapshot_id and only
    rewrites rows in place, recording the fetch in baseline_refresh_state. A
    snapshot-id-only token would miss this and serve stale numbers."""
    scope = _full_scope()
    before = cache.cached_baseline_matchups(api_db, scope, SNAPSHOT)
    assert before[(HERO_ME, E_WEAK)]["matches"] == 500

    # Mutate the baseline in place under the SAME snapshot, the way an era refresh
    # would, and stamp baseline_refresh_state (which is what actually moves).
    api_db.execute(
        "UPDATE baseline_hero_matchups SET matches = matches + 1000"
        " WHERE snapshot_id = ? AND hero_id = ? AND enemy_hero_id = ?",
        (SNAPSHOT, HERO_ME, E_WEAK))
    api_db.execute(
        "INSERT INTO baseline_refresh_state(era_id, last_refreshed_at)"
        " VALUES (0, '2026-06-21T00:00:00+00:00')"
        " ON CONFLICT(era_id) DO UPDATE SET last_refreshed_at = excluded.last_refreshed_at")
    api_db.commit()

    after = cache.cached_baseline_matchups(api_db, scope, SNAPSHOT)
    assert after[(HERO_ME, E_WEAK)]["matches"] == 1500   # fresh, not the stale 500


# ── LRU eviction and concurrency (cache mechanics, isolated instances) ────────

def test_lru_evicts_least_recently_used(api_db):
    c = cache.BaselineCache(maxsize=2)

    def load(n):
        return c.get_or_load(api_db, ("k", n), lambda: {"v": n})

    load(1)
    load(2)
    load(1)        # touch k1 so k2 becomes the eviction candidate
    load(3)        # over capacity -> evict k2 (least recently used)

    assert c.stats()["size"] == 2
    assert load(1) == {"v": 1}                    # still cached -> hit
    assert c.stats()["hits"] == 2                 # load(1) twice were hits
    load(2)                                       # k2 was evicted -> miss/reload
    assert c.stats()["misses"] == 4               # k1,k2,k3, then k2 again


def test_concurrent_access_is_safe(api_db, monkeypatch):
    # Pin the version so threads exercise only the in-memory LRU, not the shared
    # sqlite connection (which is single-thread by construction).
    monkeypatch.setattr(queries, "baseline_version", lambda conn: ("v", 1))
    c = cache.BaselineCache(maxsize=64)
    errors = []

    def worker():
        try:
            for i in range(200):
                key = ("k", i % 8)
                res = c.get_or_load(None, key, lambda: {"v": key[1]})
                assert res["v"] == key[1]
        except Exception as exc:   # noqa: BLE001 -- surface any thread failure
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert c.stats()["size"] <= 64
