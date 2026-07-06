"""Tests for the LIVE baseline cache (api/cache.py cached_baseline_performance /
cached_baseline_laning).

These baselines AVG over every match_players / laning_stats row, so they are
cached separately from the snapshot baselines: they invalidate on ingestion
(queries.ingest_version, which moves with MAX(match_id)) and carry a TTL floor so
an ingestion burst doesn't churn the cache. Two behaviours matter most: the key
includes account_id (unlike the snapshot cache, two accounts don't share a live
baseline), and a cached scope survives until a new match ingests AND the floor
lapses.

The suite pins LIVE_BASELINE_CACHE.ttl_s = 0 (conftest._fresh_baseline_caches),
so the wrapper tests below exercise pure version-token invalidation; the TTL
floor itself is exercised with an isolated BaselineCache and a fake clock.
"""
from api import cache, queries
from api.cache import BaselineCache
from api.scope import Scope

WHEN = "2026-06-15T12:00:00+00:00"
DUR = 600
BADGE = 50


def _insert_match(conn, match_id: int):
    """Minimal matches row -- enough to move MAX(match_id) for ingest_version."""
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
        " winning_team, era_id, average_badge_team0, average_badge_team1,"
        " raw_json, ingested_at) VALUES (?, ?, ?, '1', 0, NULL, ?, ?, '{}', ?)",
        (match_id, WHEN, DUR, BADGE, BADGE, WHEN),
    )
    conn.commit()


def _count(monkeypatch, name: str) -> dict:
    """Replace queries.<name> with a call-counting stub returning a constant, so a
    cache hit is observable as "loader not re-run". Attribute access at call time
    means the wrapper's lambda picks up the stub."""
    calls = {"n": 0}

    def stub(conn, scope, hero_ids):
        calls["n"] += 1
        return {"overall": {"n": 1}}

    monkeypatch.setattr(queries, name, stub)
    return calls


# ── Wrapper caching + stats surface ──────────────────────────────────────────

def test_perf_second_call_hits_and_exposes_stats(db, monkeypatch):
    _insert_match(db, 1)
    scope = Scope(account_id=1)
    calls = _count(monkeypatch, "baseline_performance")

    cache.cached_baseline_performance(db, scope, [7])
    cache.cached_baseline_performance(db, scope, [7])

    assert calls["n"] == 1                       # second served from cache
    s = cache.LIVE_BASELINE_CACHE.stats()
    assert (s["hits"], s["misses"]) == (1, 1)
    assert s["size"] == 1 and s["maxsize"] > 0   # same surface as the snapshot cache


def test_laning_second_call_hits(db, monkeypatch):
    _insert_match(db, 1)
    scope = Scope(account_id=1)
    calls = _count(monkeypatch, "baseline_laning")

    cache.cached_baseline_laning(db, scope, [7])
    cache.cached_baseline_laning(db, scope, [7])

    assert calls["n"] == 1
    assert cache.LIVE_BASELINE_CACHE.stats()["hits"] == 1


def test_perf_and_laning_do_not_collide(db, monkeypatch):
    _insert_match(db, 1)
    scope = Scope(account_id=1)
    _count(monkeypatch, "baseline_performance")
    _count(monkeypatch, "baseline_laning")

    cache.cached_baseline_performance(db, scope, [7])
    cache.cached_baseline_laning(db, scope, [7])

    assert cache.LIVE_BASELINE_CACHE.stats()["misses"] == 2   # distinct key prefixes


# ── Key sensitivity (fields the SQL filters on) ──────────────────────────────

def test_account_id_changes_key(db, monkeypatch):
    _insert_match(db, 1)
    _count(monkeypatch, "baseline_performance")

    cache.cached_baseline_performance(db, Scope(account_id=1), [7])
    cache.cached_baseline_performance(db, Scope(account_id=2), [7])

    # Unlike the snapshot cache, the live baseline excludes the scoped account,
    # so two accounts do NOT share an entry.
    assert cache.LIVE_BASELINE_CACHE.stats()["misses"] == 2


def test_hero_mix_changes_key(db, monkeypatch):
    _insert_match(db, 1)
    _count(monkeypatch, "baseline_performance")
    scope = Scope(account_id=1)

    cache.cached_baseline_performance(db, scope, [7])
    cache.cached_baseline_performance(db, scope, [7, 8])

    assert cache.LIVE_BASELINE_CACHE.stats()["misses"] == 2   # overall IN list differs


def test_badge_range_changes_key(db, monkeypatch):
    _insert_match(db, 1)
    _count(monkeypatch, "baseline_performance")

    cache.cached_baseline_performance(db, Scope(account_id=1, badge_min=0, badge_max=116), [7])
    cache.cached_baseline_performance(db, Scope(account_id=1, badge_min=0, badge_max=30), [7])

    assert cache.LIVE_BASELINE_CACHE.stats()["misses"] == 2


def test_in_lane_and_min_games_do_not_change_key(db, monkeypatch):
    """The live baseline SQL ignores in_lane and min_games, so scopes differing
    only in those share one entry."""
    _insert_match(db, 1)
    _count(monkeypatch, "baseline_performance")

    cache.cached_baseline_performance(db, Scope(account_id=1, in_lane=False, min_games=3), [7])
    cache.cached_baseline_performance(db, Scope(account_id=1, in_lane=True, min_games=9), [7])

    s = cache.LIVE_BASELINE_CACHE.stats()
    assert (s["hits"], s["misses"]) == (1, 1)


# ── Ingestion invalidation (version token) ───────────────────────────────────

def test_new_match_invalidates_after_ttl(db, monkeypatch):
    """With the suite's ttl_s=0, a newly ingested match (MAX(match_id) moves)
    invalidates the entry on the next call."""
    _insert_match(db, 1)
    _count(monkeypatch, "baseline_performance")
    scope = Scope(account_id=1)

    cache.cached_baseline_performance(db, scope, [7])   # miss
    cache.cached_baseline_performance(db, scope, [7])   # hit
    _insert_match(db, 2)                                 # ingest -> version moves
    cache.cached_baseline_performance(db, scope, [7])   # miss again

    s = cache.LIVE_BASELINE_CACHE.stats()
    assert (s["hits"], s["misses"]) == (1, 2)


# ── Transparency: cached == direct query on real seeded data ─────────────────

def _seed_perf(conn):
    for hid, name in ((7, "Wraith"), (8, "Solo")):
        conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, ?)",
                     (hid, name, WHEN))
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (1, 1, ?)", (WHEN,))
    for i in range(6):
        _insert_match_full(conn, 1000 + i, [
            (1, 1, 7, 0, 1, 3000 + i * 10, 3),   # owner (excluded from baseline)
            (2, 2, 7, 1, 0, 2000 + i * 10, 6),   # population Wraith
        ])
    conn.commit()


def _insert_match_full(conn, match_id, players):
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
        " winning_team, era_id, average_badge_team0, average_badge_team1,"
        " raw_json, ingested_at) VALUES (?, ?, ?, '1', 0, NULL, ?, ?, '{}', ?)",
        (match_id, WHEN, DUR, BADGE, BADGE, WHEN),
    )
    for slot, acct, hero, team, won, nw, deaths in players:
        conn.execute(
            "INSERT INTO match_players(match_id, player_slot, account_id, hero_id,"
            " team, kills, deaths, assists, net_worth, last_hits, denies,"
            " player_damage, obj_damage, healing, player_damage_taken, won)"
            " VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, ?, NULL, NULL, NULL, NULL,"
            " NULL, NULL, ?)",
            (match_id, slot, acct, hero, team, deaths, nw, won),
        )


def test_cached_perf_matches_direct_query(db):
    _seed_perf(db)
    scope = Scope(account_id=1)
    direct = queries.baseline_performance(db, scope, [7])
    cached = cache.cached_baseline_performance(db, scope, [7])
    assert cached == direct                        # cache is transparent
    assert cached[7]["n"] == 6                      # six population Wraith rows
    assert "overall" in cached                      # pooled row for the played hero


# ── TTL floor (isolated instance + fake clock) ───────────────────────────────

def _fake_cache(version, clock, ttl_s=300.0):
    """A BaselineCache whose version and clock are plain mutable holders, so a
    test can move each independently and observe the floor exactly."""
    return BaselineCache(version_fn=lambda conn: version["v"],
                         ttl_s=ttl_s, clock=lambda: clock["t"])


def test_ttl_floor_suppresses_invalidation_until_lapsed():
    clock = {"t": 1000.0}
    version = {"v": (1,)}
    c = _fake_cache(version, clock)
    load = lambda: {"data": version["v"]}

    c.get_or_load(None, ("k",), load)      # miss; adopt (1,), read_at=1000
    version["v"] = (2,)                     # a match ingests, but we're inside the floor
    c.get_or_load(None, ("k",), load)       # floor active -> serve adopted (1,) -> HIT
    assert c.stats()["hits"] == 1

    clock["t"] = 1000 + 301                 # floor lapses
    c.get_or_load(None, ("k",), load)       # re-read version (2,) != (1,) -> clear -> MISS
    assert c.stats()["misses"] == 2


def test_no_new_data_stays_hit_past_ttl():
    clock = {"t": 1000.0}
    version = {"v": (5,)}
    c = _fake_cache(version, clock)
    load = lambda: {"data": version["v"]}

    c.get_or_load(None, ("k",), load)       # miss; adopt (5,)
    clock["t"] += 10_000                    # far past the floor, but no new match
    c.get_or_load(None, ("k",), load)       # re-read version (5,) == (5,) -> HIT
    assert c.stats()["hits"] == 1
    assert c.stats()["misses"] == 1


def test_new_match_and_ttl_both_required_to_invalidate():
    """The acceptance criterion spelled out: neither a bump alone (inside floor)
    nor time alone (no bump) invalidates -- only both together."""
    clock = {"t": 0.0}
    version = {"v": (1,)}
    c = _fake_cache(version, clock)
    load = lambda: {"data": version["v"]}

    c.get_or_load(None, ("k",), load)       # miss
    version["v"] = (2,)                      # new match, still inside floor
    c.get_or_load(None, ("k",), load)       # HIT (bump alone insufficient)
    clock["t"] = 400                         # floor lapsed, version already (2,)
    c.get_or_load(None, ("k",), load)       # MISS (now both conditions met)
    s = c.stats()
    assert (s["hits"], s["misses"]) == (1, 2)


def test_ttl_zero_checks_version_every_call():
    """ttl_s=0 (the snapshot cache's mode) re-reads the token every call -- a bump
    invalidates immediately, no floor."""
    clock = {"t": 0.0}
    version = {"v": (1,)}
    c = _fake_cache(version, clock, ttl_s=0.0)
    load = lambda: {"data": version["v"]}

    c.get_or_load(None, ("k",), load)       # miss
    version["v"] = (2,)                      # bump; clock unchanged
    c.get_or_load(None, ("k",), load)       # no floor -> re-read -> MISS
    assert c.stats()["misses"] == 2
