"""Kill-trade views: per-match trades (note 2) and aggregate hero trades (note 3).

Both read the materialized kill_events table. These are honest raw COUNTS, not
rates -- no verdict is computed here (a kill-trade verdict would need a baseline
and would live in stats/, per hard rule 1). The per-match view embeds a `trades`
array in match_detail's response; the aggregate view extends each matchups row
with the two directional counts alongside the unchanged games-faced figure.

Hard rule 3 (no live API) holds trivially: every function under test is a local
SQLite read or a pure parse, and the autouse _no_network fixture blocks urlopen.
"""
import json

import pytest

from api import queries, service
from api.scope import make_scope
from ingest.parse import insert_match, parse_metadata

JUNE = "2026-06-15T12:00:00+00:00"


# ── Per-match trades (note 2) ────────────────────────────────────────────────

MATCH_ID = 999
ME, ALLY, ENEMY1 = 1, 222, 333          # ENEMY2 is anonymized (account_id 0)
H_ME, H_ALLY, H_ENEMY1, H_ENEMY2 = 7, 10, 15, 20


def _meta(winning_team: int = 0) -> dict:
    """Two players per team, slots 1-4. The death_details encode the trades:
    slot 3 (enemy) kills me (slot 1) twice and my ally (slot 2) once; I kill
    slot 3 once and the anonymized slot 4 once; one of slot 4's deaths is to a
    non-player (slot 99 -> stored killer_slot NULL), which is no trade."""
    return {
        "match_info": {
            "match_id": MATCH_ID,
            "start_time": 0,
            "duration_s": 1800,
            "game_mode": 1,
            "winning_team": winning_team,
            "average_badge_team0": 50,
            "average_badge_team1": 52,
            "players": [
                {"player_slot": 1, "account_id": ME, "hero_id": H_ME, "team": 0,
                 "assigned_lane": 1, "death_details": [
                     {"game_time_s": 420, "killer_player_slot": 3},
                     {"game_time_s": 1300, "killer_player_slot": 3}]},
                {"player_slot": 2, "account_id": ALLY, "hero_id": H_ALLY, "team": 0,
                 "assigned_lane": 2, "death_details": [
                     {"game_time_s": 300, "killer_player_slot": 3}]},
                {"player_slot": 3, "account_id": ENEMY1, "hero_id": H_ENEMY1, "team": 1,
                 "assigned_lane": 1, "death_details": [
                     {"game_time_s": 900, "killer_player_slot": 1}]},
                {"player_slot": 4, "account_id": 0, "hero_id": H_ENEMY2, "team": 1,
                 "assigned_lane": 2, "death_details": [
                     {"game_time_s": 1000, "killer_player_slot": 1},
                     {"game_time_s": 1200, "killer_player_slot": 99}]},
            ],
        }
    }


def _seed_match(db) -> None:
    """Seed one whole match through the ingest path so kill_events is
    materialized exactly as production would store it."""
    for hid, name in {H_ME: "Wraith", H_ALLY: "McGinnis",
                      H_ENEMY1: "Bebop", H_ENEMY2: "Lash"}.items():
        db.execute("INSERT INTO heroes(hero_id, name, image_url, fetched_at)"
                   " VALUES (?, ?, ?, ?)", (hid, name, f"http://img/{hid}.png", JUNE))
    db.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
               " VALUES (?, 1, ?)", (ME, JUNE))
    db.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
               " VALUES (1, ?, 1, ?)", (ME, JUNE))
    meta = _meta()
    parsed = parse_metadata(meta, json.dumps(meta), set(), None, JUNE)
    with db:
        insert_match(db, parsed)


def test_per_match_trades_enemy_only_with_counts(db):
    _seed_match(db)
    trades = service.match_detail(db, MATCH_ID)["trades"]   # perspective = self
    # Enemy team only: opponents are slots 3 and 4; teammate slot 2 is excluded.
    assert {t["player_slot"] for t in trades} == {3, 4}
    by_slot = {t["player_slot"]: t for t in trades}
    assert by_slot[3]["kills_by_them_on_you"] == 2          # slot 3 killed me twice
    assert by_slot[3]["kills_by_you_on_them"] == 1          # I killed slot 3 once
    # Slot 4's second "death" was to a non-player; it never becomes a trade.
    assert by_slot[4]["kills_by_them_on_you"] == 0
    assert by_slot[4]["kills_by_you_on_them"] == 1


def test_per_match_trades_surface_anonymized_opponent_hero(db):
    _seed_match(db)
    trades = service.match_detail(db, MATCH_ID)["trades"]
    anon = next(t for t in trades if t["player_slot"] == 4)
    assert anon["account_id"] == 0                          # private profile
    assert anon["hero_id"] == H_ENEMY2                      # ...still attributed by slot
    assert anon["hero_name"] == "Lash"                      # ...with its hero surfaced
    assert anon["image_url"] == f"http://img/{H_ENEMY2}.png"


def test_per_match_trades_perspective_override(db):
    _seed_match(db)
    # From slot 3's seat (team 1), the opponents are team 0: slots 1 and 2.
    trades = service.match_detail(db, MATCH_ID, account_id=ENEMY1)["trades"]
    by_slot = {t["player_slot"]: t for t in trades}
    assert set(by_slot) == {1, 2}
    assert by_slot[1]["kills_by_them_on_you"] == 1          # slot 1 killed slot 3 once
    assert by_slot[1]["kills_by_you_on_them"] == 2          # slot 3 killed slot 1 twice
    assert by_slot[2]["kills_by_them_on_you"] == 0
    assert by_slot[2]["kills_by_you_on_them"] == 1          # slot 3 killed slot 2 once


def test_per_match_trades_empty_when_perspective_absent(db):
    _seed_match(db)
    detail = service.match_detail(db, MATCH_ID, account_id=99999)   # never played
    assert detail["trades"] == []


# ── Aggregate hero trades (note 3) ───────────────────────────────────────────

SELF_ACC = 100
ENEMY_ACC, ENEMY_ACC2 = 200, 201
H_SELF, E1, E2 = 1, 2, 3


def _ke(db, match_id, killer_slot, victim_slot, n) -> None:
    for _ in range(n):
        db.execute(
            "INSERT INTO kill_events(match_id, game_time_s, victim_slot, killer_slot)"
            " VALUES (?, NULL, ?, ?)", (match_id, victim_slot, killer_slot))


def _agg_match(db, match_id, *, era, mode, badge, won, enemy_lane) -> None:
    """One scoped match: me (slot 1, team 0, hero H_SELF, lane 1) vs one enemy
    (slot 2, team 1, hero E1) at a given era / mode / badge / enemy lane."""
    winning_team = 0 if won else 1
    db.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode, winning_team,"
        " era_id, average_badge_team0, average_badge_team1, raw_json, ingested_at)"
        " VALUES (?, '2026-01-01T00:00:00Z', 1800, ?, ?, ?, ?, ?, '{}', ?)",
        (match_id, mode, winning_team, era, badge, badge, JUNE))
    db.execute("INSERT INTO match_players(match_id, player_slot, account_id, hero_id,"
               " team, lane, won) VALUES (?, 1, ?, ?, 0, 1, ?)",
               (match_id, SELF_ACC, H_SELF, int(winning_team == 0)))
    db.execute("INSERT INTO match_players(match_id, player_slot, account_id, hero_id,"
               " team, lane, won) VALUES (?, 2, ?, ?, 1, ?, ?)",
               (match_id, ENEMY_ACC, E1, enemy_lane, int(winning_team == 1)))


@pytest.fixture
def agg_db(db):
    """Six matches against enemy hero E1 (plus a second enemy hero E2 in M1),
    spread across eras / modes / badges / lanes so each scope filter has
    something to include and something to exclude.

    Hand-counted E1 totals, all-time Normal full-ladder scope (M4 brawl drops):
      you_on_them = 2 + 0 + 5 + 7 + 4 = 18 ;  them_on_you = 1 + 2 = 3 ;  games = 5
    """
    for hid, name in {H_SELF: "Wraith", E1: "Bebop", E2: "Lash"}.items():
        db.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, ?)",
                   (hid, name, JUNE))
    db.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
               " VALUES (?, 1, ?)", (SELF_ACC, JUNE))
    db.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
               " VALUES (1, ?, 1, ?)", (SELF_ACC, JUNE))
    # Migration 013 pre-seeds 12 curated eras (ids 1-12); clear them so the
    # explicit era_id=1,2 inserts below don't collide on the primary key.
    db.execute("DELETE FROM patch_eras")
    db.execute("INSERT INTO patch_eras(era_id, label, started_at)"
               " VALUES (1, 'E1', '2026-01-01T00:00:00Z')")
    db.execute("INSERT INTO patch_eras(era_id, label, started_at)"
               " VALUES (2, 'E2', '2026-04-01T00:00:00Z')")

    # M1: era1, Normal, badge 50, won, E1 in lane. Plus a 2nd enemy hero E2.
    _agg_match(db, 1, era=1, mode="1", badge=50, won=True, enemy_lane=1)
    db.execute("INSERT INTO match_players(match_id, player_slot, account_id, hero_id,"
               " team, lane, won) VALUES (1, 3, ?, ?, 1, 1, 0)", (ENEMY_ACC2, E2))
    _ke(db, 1, 1, 2, 2)        # I kill E1 x2
    _ke(db, 1, 2, 1, 1)        # E1 kills me x1
    _ke(db, 1, 1, 3, 1)        # I kill E2 x1 (separate hero -> separate row)
    _ke(db, 1, None, 1, 1)     # a tower kills me: NULL killer, never a trade

    # M2: era1, Normal, badge 50, lost.
    _agg_match(db, 2, era=1, mode="1", badge=50, won=False, enemy_lane=1)
    _ke(db, 2, 2, 1, 2)        # E1 kills me x2

    # M3: era2, Normal, badge 50, won.
    _agg_match(db, 3, era=2, mode="1", badge=50, won=True, enemy_lane=1)
    _ke(db, 3, 1, 2, 5)        # I kill E1 x5

    # M4: era1, STREET BRAWL, badge 50, won -- must never mix into Normal scope.
    _agg_match(db, 4, era=1, mode="4", badge=50, won=True, enemy_lane=1)
    _ke(db, 4, 1, 2, 10)       # I kill E1 x10 (brawl)

    # M5: era1, Normal, LOW badge (5), won -- excluded by a tightened rank range.
    _agg_match(db, 5, era=1, mode="1", badge=5, won=True, enemy_lane=1)
    _ke(db, 5, 1, 2, 7)        # I kill E1 x7

    # M6: era1, Normal, badge 50, won, E1 in a DIFFERENT lane -- excluded in-lane.
    _agg_match(db, 6, era=1, mode="1", badge=50, won=True, enemy_lane=3)
    _ke(db, 6, 1, 2, 4)        # I kill E1 x4

    db.commit()
    return db


def _row(rows, enemy_hero_id):
    return next(r for r in rows if r["enemy_hero_id"] == enemy_hero_id)


def test_aggregate_counts_both_directions_per_hero(agg_db):
    rows = service.matchups(agg_db, make_scope(account_id=SELF_ACC))   # all-time Normal
    e1 = _row(rows, E1)
    assert e1["kills_by_you_on_them"] == 18
    assert e1["kills_by_them_on_you"] == 3
    # The second enemy hero is grouped on its own row, by killer/victim hero join.
    e2 = _row(rows, E2)
    assert e2["kills_by_you_on_them"] == 1
    assert e2["kills_by_them_on_you"] == 0


def test_games_faced_unchanged_regression(agg_db):
    scope = make_scope(account_id=SELF_ACC)
    e1 = _row(service.matchups(agg_db, scope), E1)
    # games/wins are exactly the existing personal_matchups figures: the
    # kill-trade extension is a separate query and must not perturb them.
    personal = {r["enemy_hero"]: r for r in queries.personal_matchups(agg_db, scope)}
    assert e1["games"] == personal[E1]["games"] == 5
    assert e1["wins"] == personal[E1]["wins"] == 4


def test_aggregate_respects_era_filter(agg_db):
    e1 = _row(service.matchups(agg_db, make_scope(account_id=SELF_ACC, era_ids="1")), E1)
    assert e1["kills_by_you_on_them"] == 13         # era2 M3's 5 kills excluded
    assert e1["kills_by_them_on_you"] == 3
    assert e1["games"] == 4


def test_aggregate_respects_badge_filter(agg_db):
    scope = make_scope(account_id=SELF_ACC, badge_min=40, badge_max=60)
    e1 = _row(service.matchups(agg_db, scope), E1)
    assert e1["kills_by_you_on_them"] == 11         # low-badge M5's 7 kills excluded
    assert e1["kills_by_them_on_you"] == 3
    assert e1["games"] == 4


def test_aggregate_respects_game_mode(agg_db):
    brawl = _row(service.matchups(agg_db, make_scope(account_id=SELF_ACC, game_mode="4")), E1)
    assert brawl["kills_by_you_on_them"] == 10      # only the brawl match
    assert brawl["kills_by_them_on_you"] == 0
    normal = _row(service.matchups(agg_db, make_scope(account_id=SELF_ACC)), E1)
    assert normal["kills_by_you_on_them"] == 18     # ...and Normal never sees the brawl kills


def test_aggregate_respects_in_lane(agg_db):
    e1 = _row(service.matchups(agg_db, make_scope(account_id=SELF_ACC, in_lane=True)), E1)
    assert e1["kills_by_you_on_them"] == 14         # out-of-lane M6's 4 kills excluded
    assert e1["kills_by_them_on_you"] == 3
    assert e1["games"] == 4
