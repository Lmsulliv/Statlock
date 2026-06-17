"""Unit tests for the pure recurring-player helper in stats/recurring.py.

No database, no network: split_recurring works on the plain count rows one DB
pass returns, exactly as stats/sessions.py is tested. The co-occurrence gate and
the teammate/opponent split are project decisions (see docs/data-model.md), so
they are pinned here.
"""
from stats.recurring import MIN_CO_OCCURRENCE, split_recurring


def row(account_id: int, same_team: int, games: int, wins: int) -> dict:
    return {"account_id": account_id, "same_team": same_team,
            "games": games, "wins": wins}


# ── the co-occurrence gate ───────────────────────────────────────────────────

def test_floor_constant_is_below_the_verdict_floor():
    # The whole point of the two-threshold design: a player can be "recurring"
    # (>= MIN_CO_OCCURRENCE) yet still too thin for a verdict (< VERDICT_FLOOR).
    from stats import VERDICT_FLOOR
    assert MIN_CO_OCCURRENCE < VERDICT_FLOOR


def test_drops_players_below_the_minimum():
    # Two shared games is below the default floor of 3 -> not listed at all.
    out = split_recurring([row(10, 1, 2, 2), row(11, 0, 2, 0)])
    assert out["teammates"] == []
    assert out["opponents"] == []


def test_keeps_players_at_the_minimum():
    out = split_recurring([row(10, 1, MIN_CO_OCCURRENCE, 2)])
    assert [r["account_id"] for r in out["teammates"]] == [10]


def test_minimum_is_overridable():
    out = split_recurring([row(10, 1, 2, 1)], min_co_occurrence=2)
    assert [r["account_id"] for r in out["teammates"]] == [10]


# ── teammate / opponent split ────────────────────────────────────────────────

def test_splits_on_same_team():
    out = split_recurring([row(10, 1, 5, 3), row(20, 0, 5, 2)])
    assert [r["account_id"] for r in out["teammates"]] == [10]
    assert [r["account_id"] for r in out["opponents"]] == [20]


def test_same_account_can_be_both_teammate_and_opponent():
    # A player you've been with AND against shows up on both sides, judged apart.
    out = split_recurring([row(10, 1, 4, 3), row(10, 0, 6, 1)])
    assert [r["account_id"] for r in out["teammates"]] == [10]
    assert [r["account_id"] for r in out["opponents"]] == [10]
    assert out["teammates"][0]["games"] == 4 and out["teammates"][0]["wins"] == 3
    assert out["opponents"][0]["games"] == 6 and out["opponents"][0]["wins"] == 1


def test_rows_carry_only_account_games_wins():
    [r] = split_recurring([row(10, 1, 5, 3)])["teammates"]
    assert r == {"account_id": 10, "games": 5, "wins": 3}  # same_team dropped


# ── ordering ─────────────────────────────────────────────────────────────────

def test_sorted_most_shared_first():
    out = split_recurring([row(10, 1, 5, 1), row(11, 1, 9, 4), row(12, 1, 7, 7)])
    assert [r["account_id"] for r in out["teammates"]] == [11, 12, 10]  # 9,7,5


def test_ties_break_by_account_id():
    out = split_recurring([row(20, 0, 5, 1), row(10, 0, 5, 4)])
    assert [r["account_id"] for r in out["opponents"]] == [10, 20]


def test_empty():
    assert split_recurring([]) == {"teammates": [], "opponents": []}
