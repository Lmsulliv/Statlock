"""In-process cache for the baseline lookups.

Two families of baseline read repeat identical work under concurrent load:

- The SNAPSHOT baselines (`baseline_matchups`, `baseline_item_stats`) re-`SUM`
  the decade-bracket baseline rows on every request. That work is identical for
  every user sharing a scope and the data only changes when the nightly refresh
  runs.

- The LIVE baselines (`baseline_performance`, `baseline_laning`) AVG over every
  match_players / laning_stats row -- there is no stored continuous baseline --
  so they slow down linearly as matches accumulate, and the laning `lane_deaths`
  metric adds a correlated kill_events subquery per row. They change only when a
  new match ingests.

This module memoizes both families behind a bounded, version-gated LRU.

Design points worth calling out:

- Invalidation is by VERSION TOKEN, not surgical eviction. Each cache instance
  carries a `version_fn(conn)` returning a cheap token that moves when its data
  could have changed: `queries.baseline_version` (new snapshot OR staggered era
  refresh) for the snapshot cache, `queries.ingest_version` (MAX(match_id), moves
  on every ingest) for the live cache. When the token moves we drop the whole
  cache. The token lives in the DB, so this works across processes: the API
  notices the token changed on its next request.

- The live cache adds a TTL FLOOR (`ttl_s`): the version is re-read at most once
  per `ttl_s`, so a burst of ingestion doesn't invalidate a cached population
  mean per match. A cached scope survives until a new match has ingested AND the
  floor has lapsed -- a mean over thousands of matches does not meaningfully move
  with one more, so this trades a few minutes of staleness for a stable cache.
  `ttl_s=0` (the snapshot cache) re-reads the version every call: unchanged.

- The cache key contains ONLY the scope fields the underlying SQL filters on.
  The snapshot baselines ignore account_id / game_mode, so two users at the same
  rank/era/lane share one entry. The live baselines DO filter on account_id (they
  exclude the scoped account -- "you vs the field") and game_mode, so those go in
  the key; in_lane / min_games are ignored by both, so they never do.
"""
import threading
import time
from collections import OrderedDict
from typing import Callable

import sqlite3

from api import queries
from api.scope import Scope

# How many distinct (lookup, snapshot, scope) results to retain. Scopes cluster
# tightly in practice (a handful of badge ranges x eras x heroes), so a few
# hundred entries holds the working set with room to spare.
DEFAULT_MAXSIZE = 256


class BaselineCache:
    """A thread-safe, version-gated LRU. One shared instance backs each family of
    module wrappers, but it is a plain class so tests can spin up isolated
    instances.

    `version_fn(conn)` returns the invalidation token (defaults to
    `queries.baseline_version`; late-bound through the module so tests can
    monkeypatch it). `ttl_s` is the TTL floor described in the module docstring:
    0 means re-read the token on every call; a positive value re-reads it at most
    once per `ttl_s` seconds. `clock` is injectable for deterministic TTL tests."""

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE,
                 version_fn: Callable[[sqlite3.Connection], tuple] | None = None,
                 ttl_s: float = 0.0,
                 clock: Callable[[], float] = time.monotonic):
        self.maxsize = maxsize
        self.ttl_s = ttl_s
        self._version_fn = version_fn
        self._clock = clock
        self._store: "OrderedDict[tuple, dict]" = OrderedDict()
        self._lock = threading.Lock()
        self._version: tuple | None = None
        self._version_read_at: float | None = None
        self._hits = 0
        self._misses = 0

    def _current_version(self, conn: sqlite3.Connection) -> tuple:
        """The invalidation token, honoring the TTL floor. Within `ttl_s` of the
        last read we reuse the adopted token WITHOUT touching the DB, so a burst
        of ingestion can't invalidate the cache per match. Runs OUTSIDE the lock
        (like the original design) so the version query and the loader never
        serialize requests on the lock; the timestamp races near the floor
        boundary are benign (worst case two threads read the same cheap token)."""
        now = self._clock()
        read_at = self._version_read_at
        if (self.ttl_s and read_at is not None and now - read_at < self.ttl_s):
            return self._version   # inside the floor: serve under the adopted token
        version_fn = self._version_fn or queries.baseline_version
        version = version_fn(conn)
        self._version_read_at = now
        return version

    def get_or_load(self, conn: sqlite3.Connection, key: tuple,
                    loader: Callable[[], dict]) -> dict:
        """Return the cached result for `key`, or run `loader()` and cache it.

        The version read and the DB I/O in `loader` both run OUTSIDE the lock so
        requests don't serialize on them; the only thing the lock guards is the
        small in-memory dict."""
        version = self._current_version(conn)
        with self._lock:
            if version != self._version:
                # Baseline data moved (new snapshot, staggered refresh, or -- for
                # the live cache -- a newly ingested match past the TTL floor):
                # the whole cache is suspect, so drop it and adopt the new version.
                self._store.clear()
                self._version = version
            hit = self._store.get(key)
            if hit is not None:
                self._store.move_to_end(key)
                self._hits += 1
                return dict(hit)   # shallow copy: callers never mutate our mapping
            self._misses += 1

        result = loader()

        with self._lock:
            # Re-check the version: a refresh may have landed while we loaded, in
            # which case storing under the old generation would poison the cache.
            if version == self._version:
                self._store[key] = result
                self._store.move_to_end(key)
                while len(self._store) > self.maxsize:
                    self._store.popitem(last=False)   # evict least-recently-used
        return dict(result)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._version = None
            self._version_read_at = None

    def stats(self) -> dict:
        """Hit/miss counters and current size -- read by the benchmark and tests."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._store),
                "maxsize": self.maxsize,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0


# The process-wide instance the API and CLI share through the wrappers below.
BASELINE_CACHE = BaselineCache()


def cached_baseline_matchups(conn: sqlite3.Connection, scope: Scope,
                             snapshot_id: int) -> dict[tuple[int, int], dict]:
    """Cached `queries.baseline_matchups`. Same signature and return shape."""
    key = ("matchups", snapshot_id, scope.era_ids,
           scope.badge_min, scope.badge_max, bool(scope.in_lane))
    return BASELINE_CACHE.get_or_load(
        conn, key, lambda: queries.baseline_matchups(conn, scope, snapshot_id))


def cached_baseline_item_stats(conn: sqlite3.Connection, scope: Scope,
                               hero_id: int, snapshot_id: int) -> dict[int, dict]:
    """Cached `queries.baseline_item_stats`. Same signature and return shape."""
    key = ("items", snapshot_id, hero_id, scope.era_ids,
           scope.badge_min, scope.badge_max)
    return BASELINE_CACHE.get_or_load(
        conn, key, lambda: queries.baseline_item_stats(conn, scope, hero_id, snapshot_id))


# ── Live (continuous) baseline cache ─────────────────────────────────────────
# A SEPARATE instance from BASELINE_CACHE: it invalidates on ingestion
# (queries.ingest_version) rather than on the nightly refresh, and it carries a
# TTL floor so an ingestion burst doesn't churn the cache. See the module
# docstring for the account_id-in-key rationale.
LIVE_TTL_S = 300.0   # 5 min: the floor a cached population mean survives across.
LIVE_BASELINE_CACHE = BaselineCache(version_fn=queries.ingest_version, ttl_s=LIVE_TTL_S)


def cached_baseline_performance(conn: sqlite3.Connection, scope: Scope,
                                my_hero_ids: list[int]) -> dict:
    """Cached `queries.baseline_performance`. Same signature and return shape.

    The key carries every field the query filters on: account_id (the baseline
    EXCLUDES the scoped account, so two accounts don't share an entry), game_mode,
    era ids, badge range, and the hero-mix tuple (it drives the pooled `overall`
    row's IN list). in_lane / min_games aren't filtered on, so they stay out."""
    key = ("perf", scope.account_id, scope.game_mode, scope.era_ids,
           scope.badge_min, scope.badge_max, tuple(my_hero_ids))
    return LIVE_BASELINE_CACHE.get_or_load(
        conn, key, lambda: queries.baseline_performance(conn, scope, my_hero_ids))


def cached_baseline_laning(conn: sqlite3.Connection, scope: Scope,
                           my_hero_ids: list[int]) -> dict:
    """Cached `queries.baseline_laning`. Same signature, key, and shape as
    cached_baseline_performance (different metric set, same scope fields)."""
    key = ("laning", scope.account_id, scope.game_mode, scope.era_ids,
           scope.badge_min, scope.badge_max, tuple(my_hero_ids))
    return LIVE_BASELINE_CACHE.get_or_load(
        conn, key, lambda: queries.baseline_laning(conn, scope, my_hero_ids))
