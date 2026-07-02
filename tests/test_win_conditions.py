"""Win-condition analysis ("what wins your games") on the Improvement tab.

Exercises the assembly: per-match laning rows + outcome
(queries.personal_laning_outcomes), the live field baseline
(queries.baseline_laning), and the split/tier math (stats.split_tier) wired up in
service.win_conditions and surfaced through service.improvement.

Hard rule 3 (no live API) holds trivially: every read here is a local SQLite
query, and the autouse _no_network fixture blocks urlopen.
"""
import sqlite3

import pytest

from api import service
from api.scope import make_scope
from stats import wilson_interval
from tracker.db import connect
from tracker.migrate import migrate

ME = 1            # tracked self account
POP = 2           # a non-owner population player -> sets the field baseline
HERO = 7
HERO_B = 8        # second hero, for the hero_id-narrowing test
WHEN = "2026-06-15T12:00:00+00:00"
DUR = 1800
BADGE = 50

# The field baseline the population pins, and the two sides of each split.
BASE_NW, BASE_LH = 4000, 50
HI_NW, LO_NW = 5000, 3000      # >= / < BASE_NW  -> won_lane met / not-met
HI_LH, LO_LH = 60, 40          # >  / <= BASE_LH -> last_hit_lead met / not-met


def _db(tmp_path, monkeypatch) -> sqlite3.Connection:
    path = tmp_path / "wc.db"
    conn = connect(path)
    migrate(conn)
    for hid, name in ((HERO, "Wraith"), (HERO_B, "Abrams")):
        conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, ?)",
                     (hid, name, WHEN))
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (?, 1, ?)", (ME, WHEN))
    conn.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
                 " VALUES (1, ?, 1, ?)", (ME, WHEN))
    monkeypatch.setenv("DEADLOCK_DB", str(path))
    return conn


def _add_match(conn, match_id, *, hero, won, net_worth, last_hits):
    """One of my matches: I am slot 1 (team 0), with my lane-end snapshot; a
    population opponent is slot 2 (team 1) pinning the field baseline."""
    winning_team = 0 if won else 1
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
        " winning_team, era_id, average_badge_team0, average_badge_team1,"
        " raw_json, ingested_at) VALUES (?, ?, ?, '1', ?, NULL, ?, ?, '{}', ?)",
        (match_id, WHEN, DUR, winning_team, BADGE, BADGE, WHEN),
    )
    conn.execute("INSERT INTO match_players(match_id, player_slot, account_id,"
                 " hero_id, team, won) VALUES (?, 1, ?, ?, 0, ?)",
                 (match_id, ME, hero, int(winning_team == 0)))
    conn.execute("INSERT INTO match_players(match_id, player_slot, account_id,"
                 " hero_id, team, won) VALUES (?, 2, ?, ?, 1, ?)",
                 (match_id, POP, hero, int(winning_team == 1)))
    conn.execute("INSERT INTO laning_stats(match_id, player_slot, net_worth,"
                 " last_hits, denies, sampled_at_s) VALUES (?, 1, ?, ?, 5, 540)",
                 (match_id, net_worth, last_hits))
    # Population snapshot at the same mark pins BASE_NW / BASE_LH.
    conn.execute("INSERT INTO laning_stats(match_id, player_slot, net_worth,"
                 " last_hits, denies, sampled_at_s) VALUES (?, 2, ?, ?, 5, 540)",
                 (match_id, BASE_NW, BASE_LH))


# A 2x2 world over HERO: net worth (won_lane axis) and last hits (last_hit_lead
# axis) are set independently per cell, with win counts chosen so BOTH conditions
# clear and won_lane's gap (0.6) is the larger lever than last_hit_lead's (0.4).
#   cell  net_worth  last_hits  games  wins
#   A     HI         HI         15     15
#   B     HI         LO         15      9
#   C     LO         HI         15      6
#   D     LO         LO         15      0
# won_lane:      met = A+B (24/30 = .80), not = C+D (6/30 = .20) -> gap .60
# last_hit_lead: met = A+C (21/30 = .70), not = B+D (9/30 = .30) -> gap .40
CELLS = [("A", HI_NW, HI_LH, 15), ("B", HI_NW, LO_LH, 9),
         ("C", LO_NW, HI_LH, 6), ("D", LO_NW, LO_LH, 0)]


def _seed_2x2(conn):
    mid = 1000
    for _, nw, lh, wins in CELLS:
        for i in range(15):
            _add_match(conn, mid, hero=HERO, won=(i < wins), net_worth=nw, last_hits=lh)
            mid += 1
    conn.commit()


def _by_key(conditions):
    return {c["key"]: c for c in conditions}


# ── A real split surfaces with the right rates and intervals ──────────────────

def test_won_lane_surfaces_with_correct_rates_and_intervals(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    _seed_2x2(conn)

    conds = service.win_conditions(conn, make_scope(account_id=ME))
    won_lane = _by_key(conds)["won_lane"]

    assert won_lane["tier"] == "clear"
    assert won_lane["met"]["n"] == 30 and won_lane["met"]["wins"] == 24
    assert won_lane["not_met"]["n"] == 30 and won_lane["not_met"]["wins"] == 6
    assert won_lane["met"]["rate"] == round(24 / 30, 4)
    assert won_lane["not_met"]["rate"] == round(6 / 30, 4)
    assert won_lane["gap"] == round(24 / 30 - 6 / 30, 4)

    low, high = wilson_interval(24, 30)
    assert (won_lane["met"]["ci_low"], won_lane["met"]["ci_high"]) == (round(low, 4), round(high, 4))


def test_win_conditions_reach_the_improvement_response(tmp_path, monkeypatch):
    # The digest carries the same list the standalone helper computes.
    conn = _db(tmp_path, monkeypatch)
    _seed_2x2(conn)
    imp = service.improvement(conn, make_scope(account_id=ME))
    assert imp["win_conditions"] == service.win_conditions(conn, make_scope(account_id=ME))
    assert "won_lane" in _by_key(imp["win_conditions"])


# ── Surfaced conditions sort by gap, biggest lever first ──────────────────────

def test_conditions_sort_by_gap(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    _seed_2x2(conn)
    conds = service.win_conditions(conn, make_scope(account_id=ME))

    keys = [c["key"] for c in conds]
    assert keys[:2] == ["won_lane", "last_hit_lead"]      # .60 gap before .40 gap
    assert conds[0]["gap"] >= conds[1]["gap"]


# ── A side below the floor hides the condition ────────────────────────────────

def test_condition_hidden_when_a_side_is_below_floor(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    # Many won-lane games but only 4 lost-lane games: the not-met side is below
    # VERDICT_FLOOR (5), so won_lane is dropped however lopsided the records.
    mid = 2000
    for i in range(20):
        _add_match(conn, mid, hero=HERO, won=(i < 16), net_worth=HI_NW, last_hits=HI_LH)
        mid += 1
    for i in range(4):
        _add_match(conn, mid, hero=HERO, won=False, net_worth=LO_NW, last_hits=HI_LH)
        mid += 1
    conn.commit()

    conds = service.win_conditions(conn, make_scope(account_id=ME))
    assert "won_lane" not in _by_key(conds)


# ── hero_id narrows the splits to that hero's games ───────────────────────────

def test_hero_id_narrows_the_splits(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    # HERO: a clean won-lane split (16/20 met wins vs 4/20 not-met wins).
    mid = 3000
    for i in range(20):
        _add_match(conn, mid, hero=HERO, won=(i < 16), net_worth=HI_NW, last_hits=HI_LH)
        mid += 1
    for i in range(20):
        _add_match(conn, mid, hero=HERO, won=(i < 4), net_worth=LO_NW, last_hits=HI_LH)
        mid += 1
    # HERO_B: outcome independent of net worth (10/20 on each side) -> no split.
    for i in range(20):
        _add_match(conn, mid, hero=HERO_B, won=(i < 10), net_worth=HI_NW, last_hits=HI_LH)
        mid += 1
    for i in range(20):
        _add_match(conn, mid, hero=HERO_B, won=(i < 10), net_worth=LO_NW, last_hits=HI_LH)
        mid += 1
    conn.commit()

    scoped_a = service.win_conditions(conn, make_scope(account_id=ME), hero_id=HERO)
    scoped_b = service.win_conditions(conn, make_scope(account_id=ME), hero_id=HERO_B)

    assert "won_lane" in _by_key(scoped_a)            # HERO's split surfaces
    assert _by_key(scoped_b) == {}                    # HERO_B's games never split
    # Sanity: HERO's met side really is only its own 20 games, not all 40.
    assert _by_key(scoped_a)["won_lane"]["met"]["n"] == 20
