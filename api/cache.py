"""In-process cache for the global-baseline lookups.

`baseline_matchups` and `baseline_item_stats` re-`SUM` the decade-bracket
baseline rows on every request (api.queries). That work is identical for every
user sharing a scope and the baseline data only changes when the nightly refresh
runs, so it is pure repeated effort under concurrent load. This module memoizes
those two lookups behind a bounded LRU that invalidates whenever the baseline
data could have changed.

Two design points worth calling out:

- Invalidation is by VERSION TOKEN, not surgical eviction. `queries.baseline_version`
  returns a cheap token that moves on a new snapshot OR a staggered era refresh
  (which keeps the same snapshot_id -- see that function). When the token moves we
  drop the whole cache. The token lives in the DB, so this works even though the
  API and the refresh worker are separate processes: the API simply notices the
  token changed on its next request.

- The cache key contains ONLY the scope fields the baseline SQL actually filters
  on (era_ids, badge range, same_lane / hero_id). account_id and game_mode are
  deliberately excluded: the baseline SQL ignores them, so two different users at
  the same rank/era/lane share one cached baseline -- which is the entire point.
"""
import threading
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
    """A thread-safe, version-gated LRU. One shared instance backs the module
    wrappers, but it is a plain class so tests can spin up isolated instances."""

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE):
        self.maxsize = maxsize
        self._store: "OrderedDict[tuple, dict]" = OrderedDict()
        self._lock = threading.Lock()
        self._version: tuple | None = None
        self._hits = 0
        self._misses = 0

    def get_or_load(self, conn: sqlite3.Connection, key: tuple,
                    loader: Callable[[], dict]) -> dict:
        """Return the cached result for `key`, or run `loader()` and cache it.

        The DB I/O in `loader` runs OUTSIDE the lock so requests don't serialize
        on it; the only thing the lock guards is the small in-memory dict."""
        version = queries.baseline_version(conn)
        with self._lock:
            if version != self._version:
                # Baseline data moved (new snapshot or staggered refresh): the
                # whole cache is suspect, so drop it and adopt the new version.
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
