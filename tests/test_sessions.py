"""Unit tests for the pure tilt-analysis helpers in stats/sessions.py.

No database, no network: these exercise session grouping and the two bucketings
on plain dicts, exactly as stats/__init__.py is tested. The session model and the
reset-each-session rule are project decisions (see docs/data-model.md), so they
are pinned here.
"""
from datetime import datetime, timedelta, timezone

from stats.sessions import (
    SESSION_GAP_S,
    by_loss_streak,
    by_session_index,
    group_sessions,
)

BASE = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def at(minutes: float, won: int = 1) -> dict:
    """A match `minutes` after BASE with the given result."""
    return {"start_time": (BASE + timedelta(minutes=minutes)).isoformat(), "won": won}


def sess(results: str) -> list[dict]:
    """A session from a 'WLLW' string of results (one match per character)."""
    return [{"won": 1 if c == "W" else 0} for c in results]


# ── group_sessions ───────────────────────────────────────────────────────────

def test_group_sessions_empty():
    assert group_sessions([]) == []


def test_group_sessions_single_match():
    matches = [at(0)]
    assert group_sessions(matches) == [matches]


def test_group_sessions_splits_on_large_gap():
    # Two close games, then a 4h gap, then one more -> two sessions.
    matches = [at(0), at(60), at(60 + 240)]
    sessions = group_sessions(matches)
    assert [len(s) for s in sessions] == [2, 1]
    assert sessions[1][0] is matches[2]


def test_group_sessions_gap_boundary_is_inclusive():
    # A gap of exactly SESSION_GAP_S starts a new session (>= boundary); one
    # second under keeps the same session.
    gap_min = SESSION_GAP_S / 60
    assert [len(s) for s in group_sessions([at(0), at(gap_min)])] == [1, 1]
    assert [len(s) for s in group_sessions([at(0), at(gap_min - 1 / 60)])] == [2]


def test_group_sessions_preserves_results_in_order():
    matches = [at(0, won=1), at(30, won=0), at(60, won=1)]
    [only] = group_sessions(matches)
    assert [m["won"] for m in only] == [1, 0, 1]


# ── by_session_index ─────────────────────────────────────────────────────────

def test_by_session_index_positions_and_cap():
    # One 8-game session; cap 6 collapses positions 6,7,8 into the "6+" bucket.
    rows = by_session_index([sess("WWWWWWWW")], cap=6)
    by_idx = {r["index"]: r for r in rows}
    assert sorted(by_idx) == [1, 2, 3, 4, 5, 6]
    assert by_idx[1]["games"] == 1 and not by_idx[1]["capped"]
    assert by_idx[6]["games"] == 3 and by_idx[6]["capped"]  # positions 6,7,8


def test_by_session_index_aggregates_across_sessions():
    # First game of every session lands in index 1.
    rows = by_session_index([sess("WL"), sess("LWW"), sess("W")], cap=6)
    by_idx = {r["index"]: r for r in rows}
    assert by_idx[1]["games"] == 3 and by_idx[1]["wins"] == 2  # W, L, W
    assert by_idx[2]["games"] == 2 and by_idx[2]["wins"] == 1  # L, W
    assert by_idx[3]["games"] == 1 and by_idx[3]["wins"] == 1  # W


def test_by_session_index_is_ordered_and_skips_empty():
    rows = by_session_index([sess("WW")], cap=6)
    assert [r["index"] for r in rows] == [1, 2]  # no empty 3..6 rows


def test_by_session_index_empty():
    assert by_session_index([]) == []


# ── by_loss_streak ───────────────────────────────────────────────────────────

def test_by_loss_streak_resets_after_a_win():
    # W L L W L -> preceding-streak buckets: 0,0,1,2,0.
    rows = by_loss_streak([sess("WLLWL")], cap=3)
    by_s = {r["streak"]: r for r in rows}
    assert by_s[0]["games"] == 3 and by_s[0]["wins"] == 1   # games 1,2,5
    assert by_s[1]["games"] == 1 and by_s[1]["wins"] == 0   # game 3
    assert by_s[2]["games"] == 1 and by_s[2]["wins"] == 1   # game 4
    assert 3 not in by_s


def test_by_loss_streak_caps_long_streaks():
    # Five straight losses; cap 3 collapses preceding-streaks 3 and 4 into "3+".
    rows = by_loss_streak([sess("LLLLL")], cap=3)
    by_s = {r["streak"]: r for r in rows}
    assert by_s[3]["games"] == 2 and by_s[3]["capped"]      # games 4,5
    assert not by_s[0]["capped"]


def test_by_loss_streak_resets_each_session():
    # A session that ends on two losses does NOT carry into the next session:
    # the next session's first game sits in streak bucket 0.
    rows = by_loss_streak([sess("LL"), sess("W")], cap=3)
    by_s = {r["streak"]: r for r in rows}
    assert by_s[0]["games"] == 2 and by_s[0]["wins"] == 1   # 1st of each session
    assert by_s[1]["games"] == 1                            # 2nd game of session 1


def test_by_loss_streak_empty():
    assert by_loss_streak([]) == []
