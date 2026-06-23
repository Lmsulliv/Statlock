"""Service + endpoint tests for recurring-player analytics (/api/recurring-players).

Seeds one tracked account whose matches share other real players a known number
of times, so the co-occurrence counts and the self-baseline are hand-verifiable.
The pure gate/split math is covered in tests/test_recurring.py; here we check the
SQL -> stats -> JSON assembly and the route, mirroring tests/test_tilt.py.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api import queries, service
from api.app import app
from api.scope import make_scope
from stats import VERDICT_NOT_ENOUGH_DATA
from stats.recurring import MIN_CO_OCCURRENCE
from tracker.db import connect
from tracker.migrate import migrate

ME = 1            # the tracked "self" account (team 0 in every match)
FRIEND = 2        # a tracked co-player (has a display_name)
MATE = 100        # untracked recurring teammate
NEMESIS = 200     # untracked recurring opponent
NOISE = 300       # untracked, only 2 shared games -> below the floor
H7, H8 = 7, 8     # ME plays hero 7 mostly, hero 8 in two games
BASE = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _match(conn, mid: int, won: bool, hero: int, co: list[tuple[int, int]]) -> None:
    """One match: ME (team 0, given hero, given result) plus the co-players.
    `co` is a list of (account_id, team); team 0 is ME's side, 1 the enemy."""
    winning_team = 0 if won else 1
    start = (BASE + timedelta(minutes=mid)).isoformat()
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
        " winning_team, raw_json, ingested_at) VALUES (?, ?, 1800, '1', ?, '{}', ?)",
        (mid, start, winning_team, start),
    )
    # ME is slot 1; co-players take slots 2, 3, ... within the match.
    conn.execute(
        "INSERT INTO match_players(match_id, player_slot, account_id, hero_id, team, won)"
        " VALUES (?, 1, ?, ?, 0, ?)", (mid, ME, hero, int(won)))
    for slot, (account_id, team) in enumerate(co, start=2):
        conn.execute(
            "INSERT INTO match_players(match_id, player_slot, account_id, hero_id, team, won)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (mid, slot, account_id, H7, team, int(team == winning_team)))


def _seed(conn) -> None:
    for hid, name in {H7: "Wraith", H8: "Abrams"}.items():
        conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, ?)",
                     (hid, name, BASE.isoformat()))
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (?, 1, ?)", (ME, BASE.isoformat()))
    conn.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
                 " VALUES (1, ?, 1, ?)", (ME, BASE.isoformat()))
    conn.execute("INSERT INTO tracked_accounts(account_id, display_name, is_self,"
                 " added_at) VALUES (?, 'Pocket', 0, ?)", (FRIEND, BASE.isoformat()))
    # One of each resolver precedence level: a manual label (FRIEND) and a Steam
    # persona (NEMESIS); MATE has neither and falls back to its bare id.
    conn.execute("INSERT INTO account_labels(user_id, account_id, display_name,"
                 " updated_at) VALUES (1, ?, 'Pocket', ?)", (FRIEND, BASE.isoformat()))
    conn.execute("INSERT INTO steam_personas(account_id, persona_name, avatar_url,"
                 " fetched_at) VALUES (?, 'NemesisHandle', NULL, ?)", (NEMESIS, BASE.isoformat()))

    # Teammate MATE: 5 hero-7 games (4 won) + 2 hero-8 games (0 won) on my team.
    for mid, won in ((1, True), (2, True), (3, True), (4, True), (5, False)):
        _match(conn, mid, won, H7, [(MATE, 0)])
    for mid in (6, 7):
        _match(conn, mid, False, H8, [(MATE, 0)])
    # Opponent NEMESIS: 5 hero-7 games on the enemy team, I won only one.
    for mid, won in ((8, False), (9, False), (10, False), (11, False), (12, True)):
        _match(conn, mid, won, H7, [(NEMESIS, 1)])
    # Tracked teammate FRIEND: 3 hero-7 games (2 won) -> recurring but thin.
    for mid, won in ((13, True), (14, False), (15, True)):
        _match(conn, mid, won, H7, [(FRIEND, 0)])
    # NOISE: only 2 shared games -> must never be listed.
    for mid, won in ((16, True), (17, False)):
        _match(conn, mid, won, H7, [(NOISE, 0)])
    conn.commit()


@pytest.fixture
def rec_db(tmp_path, monkeypatch):
    path = tmp_path / "rec.db"
    conn = connect(path)
    migrate(conn)
    _seed(conn)
    monkeypatch.setenv("DEADLOCK_DB", str(path))
    return conn


def _by_id(rows):
    return {r["account_id"]: r for r in rows}


# ── service.recurring_players ────────────────────────────────────────────────

def test_overall_is_the_self_baseline(rec_db):
    # 17 matches total, 8 wins (m1-4, m12, m13, m15, m16).
    result = service.recurring_players(rec_db, make_scope())
    assert result["overall"] == {"games": 17, "wins": 8,
                                 "winrate": pytest.approx(8 / 17, abs=1e-4)}
    assert result["min_co_occurrence"] == MIN_CO_OCCURRENCE
    assert result["hero_id"] is None


def test_teammates_and_opponents_counts(rec_db):
    result = service.recurring_players(rec_db, make_scope())
    mates, opps = _by_id(result["teammates"]), _by_id(result["opponents"])
    assert mates[MATE]["games"] == 7 and mates[MATE]["wins"] == 4
    assert opps[NEMESIS]["games"] == 5 and opps[NEMESIS]["wins"] == 1


def test_below_minimum_co_occurrence_is_omitted(rec_db):
    result = service.recurring_players(rec_db, make_scope())
    assert NOISE not in _by_id(result["teammates"])
    assert NOISE not in _by_id(result["opponents"])


def test_thin_recurring_player_reads_not_enough_data(rec_db):
    # FRIEND shares 3 games -> listed (>= floor of 3) but no verdict (< 5).
    friend = _by_id(service.recurring_players(rec_db, make_scope())["teammates"])[FRIEND]
    assert friend["games"] == 3
    assert friend["verdict"] == VERDICT_NOT_ENOUGH_DATA


def test_display_name_resolves_label_persona_then_id(rec_db):
    result = service.recurring_players(rec_db, make_scope())
    mates, opps = _by_id(result["teammates"]), _by_id(result["opponents"])
    assert mates[FRIEND]["display_name"] == "Pocket"          # manual label wins
    assert opps[NEMESIS]["display_name"] == "NemesisHandle"   # Steam persona
    assert mates[MATE]["display_name"] == str(MATE)           # bare id fallback


def test_baseline_is_the_accounts_own_rate(rec_db):
    mate = _by_id(service.recurring_players(rec_db, make_scope())["teammates"])[MATE]
    assert mate["global_matches"] == 17
    assert mate["global_rate"] == pytest.approx(8 / 17, abs=1e-4)


def test_teammates_sorted_most_shared_first(rec_db):
    teammates = service.recurring_players(rec_db, make_scope())["teammates"]
    # MATE (7 games) outranks FRIEND (3 games).
    assert [r["account_id"] for r in teammates] == [MATE, FRIEND]


def test_hero_filter_rebaselines_to_that_hero(rec_db):
    # hero_id=7 drops MATE's two hero-8 games from both the baseline and the
    # co-occurrence, so the whole screen is "you on Wraith".
    result = service.recurring_players(rec_db, make_scope(), hero_id=H7)
    assert result["hero_id"] == H7
    assert result["overall"]["games"] == 15        # 17 minus the two hero-8 games
    mate = _by_id(result["teammates"])[MATE]
    assert mate["games"] == 5 and mate["wins"] == 4


def test_anonymized_coplayers_excluded_real_ones_kept(db):
    """recurring_co_players drops anonymized (account_id 0) co-players -- they are
    not real recurring people and would otherwise collapse a whole lobby's zeros
    into one inflated bucket -- while still surfacing real co-players."""
    db.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (7, 'Wraith', 't')")
    db.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at) VALUES (1, 1, 't')")
    db.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at) VALUES (1, 1, 1, 't')")
    for i in range(4):
        mid = 700 + i
        start = (BASE + timedelta(minutes=mid)).isoformat()
        db.execute(
            "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
            " winning_team, raw_json, ingested_at) VALUES (?, ?, 1800, '1', 0, '{}', ?)",
            (mid, start, start))
        db.execute("INSERT INTO match_players(match_id, player_slot, account_id, hero_id, team, won)"
                   " VALUES (?, 1, 1, 7, 0, 1)", (mid,))     # me
        db.execute("INSERT INTO match_players(match_id, player_slot, account_id, hero_id, team, won)"
                   " VALUES (?, 2, 100, 7, 0, 1)", (mid,))   # real teammate
        db.execute("INSERT INTO match_players(match_id, player_slot, account_id, hero_id, team, won)"
                   " VALUES (?, 3, 0, 7, 1, 0)", (mid,))     # anonymized opponent
    db.commit()

    ids = {r["account_id"] for r in queries.recurring_co_players(db, make_scope(account_id=1))}
    assert 100 in ids
    assert 0 not in ids


def test_empty_scope_returns_empty_shape(db):
    # Migrated but no tracked account -> resolves to nothing -> empty shape.
    result = service.recurring_players(db, make_scope())
    assert result["teammates"] == [] and result["opponents"] == []
    assert result["overall"]["games"] == 0
    assert result["min_co_occurrence"] == MIN_CO_OCCURRENCE


# ── /api/recurring-players endpoint ──────────────────────────────────────────

def test_endpoint_returns_documented_shape(rec_db):
    res = TestClient(app).get("/api/recurring-players")
    assert res.status_code == 200
    body = res.json()
    assert set(body) >= {"teammates", "opponents", "overall",
                         "min_co_occurrence", "hero_id"}
    assert _by_id(body["teammates"])[MATE]["games"] == 7


def test_endpoint_honors_hero_filter(rec_db):
    res = TestClient(app).get("/api/recurring-players", params={"hero_id": H7})
    assert res.status_code == 200
    assert res.json()["overall"]["games"] == 15


def test_endpoint_empty_db(empty_db_path):
    res = TestClient(app).get("/api/recurring-players")
    assert res.status_code == 200
    body = res.json()
    assert body["teammates"] == [] and body["opponents"] == []
    assert body["overall"]["games"] == 0
