"""Match detail view: pure-parser, service-assembly, and endpoint tests.

The detail view reads one match back out of storage -- the 12-player roster and
the kill/death timeline come from parsing matches.raw_json (only that payload
carries player_slot, which death_details references), while purchases come from
the relational match_item_purchases table. No statistics, so no stats/ here.

Hard rule 3 (no live API) is satisfied trivially: every function under test is a
pure parse or a local-SQLite read; the autouse _no_network fixture also blocks
urlopen.
"""
import json

import pytest
from fastapi.testclient import TestClient

from api import match_detail, queries, service
from api.app import app
from tracker.db import connect
from tracker.migrate import migrate

ME = 1                     # the tracked "self" account
ALLY = 222                 # teammate (team 0), untracked
ENEMY1 = 333               # opponent (team 1), untracked
ENEMY2 = 444               # opponent (team 1), untracked
MATCH_ID = 999

H_ME, H_ALLY, H_ENEMY1, H_ENEMY2 = 7, 10, 15, 20
IT_A, IT_B = 100, 101
JUNE = "2026-06-15T12:00:00+00:00"


def _meta(winning_team: int = 0) -> dict:
    """A compact but representative metadata payload: two players per team, slots
    1-4 (deliberately not 0-based), and a few death_details -- including one whose
    killer slot (99) matches no player, standing in for an environment kill."""
    return {
        "match_info": {
            "match_id": MATCH_ID,
            "duration_s": 1800,
            "game_mode": 1,
            "winning_team": winning_team,
            "average_badge_team0": 50,
            "average_badge_team1": 52,
            "players": [
                {"player_slot": 1, "account_id": ME, "hero_id": H_ME, "team": 0,
                 "assigned_lane": 1, "kills": 5, "deaths": 2, "assists": 7,
                 "net_worth": 12000, "last_hits": 100, "denies": 10,
                 "death_details": [
                     {"game_time_s": 420, "killer_player_slot": 3},
                     {"game_time_s": 1200, "killer_player_slot": 99},
                 ]},
                {"player_slot": 2, "account_id": ALLY, "hero_id": H_ALLY, "team": 0,
                 "assigned_lane": 2, "kills": 2, "deaths": 1, "assists": 3,
                 "net_worth": 9000, "last_hits": 60, "denies": 5,
                 "death_details": [{"game_time_s": 300, "killer_player_slot": 4}]},
                {"player_slot": 3, "account_id": ENEMY1, "hero_id": H_ENEMY1, "team": 1,
                 "assigned_lane": 1, "kills": 6, "deaths": 1, "assists": 2,
                 "net_worth": 14000, "last_hits": 120, "denies": 12,
                 "death_details": [{"game_time_s": 900, "killer_player_slot": 1}]},
                {"player_slot": 4, "account_id": ENEMY2, "hero_id": H_ENEMY2, "team": 1,
                 "assigned_lane": 2, "kills": 1, "deaths": 0, "assists": 8,
                 "net_worth": 8000, "last_hits": 40, "denies": 2,
                 "death_details": []},
            ],
        }
    }


# ── Pure parser ──────────────────────────────────────────────────────────────

def test_parse_players_reads_roster_and_derives_won():
    players = match_detail.parse_players(_meta(winning_team=0))
    assert len(players) == 4
    me = next(p for p in players if p["player_slot"] == 1)
    assert (me["account_id"], me["hero_id"], me["team"], me["lane"]) == (ME, H_ME, 0, 1)
    assert (me["kills"], me["deaths"], me["assists"]) == (5, 2, 7)
    assert me["won"] is True                                   # team 0 == winning_team
    enemy = next(p for p in players if p["player_slot"] == 3)
    assert enemy["won"] is False                               # team 1 lost


def test_parse_deaths_sorted_and_killer_mapped_by_slot():
    deaths = match_detail.parse_deaths(_meta())
    assert [d["game_time_s"] for d in deaths] == [300, 420, 900, 1200]

    first = deaths[0]                                          # ally dies to enemy2
    assert first["victim_slot"] == 2 and first["victim_hero_id"] == H_ALLY
    assert first["killer_slot"] == 4 and first["killer_hero_id"] == H_ENEMY2
    assert first["killer_team"] == 1


def test_parse_deaths_unknown_killer_slot_is_none():
    deaths = match_detail.parse_deaths(_meta())
    env = next(d for d in deaths if d["game_time_s"] == 1200)
    assert env["killer_slot"] == 99                            # slot preserved...
    assert env["killer_hero_id"] is None                       # ...but no hero maps to it
    assert env["killer_team"] is None


def test_parse_detail_tolerates_empty_payload():
    assert match_detail.parse_detail({}) == {"players": [], "deaths": []}
    assert match_detail.parse_detail(json.loads("{}")) == {"players": [], "deaths": []}


# ── Service assembly ─────────────────────────────────────────────────────────

def _seed(conn) -> None:
    for hid, name in {H_ME: "Wraith", H_ALLY: "McGinnis",
                      H_ENEMY1: "Bebop", H_ENEMY2: "Lash"}.items():
        conn.execute("INSERT INTO heroes(hero_id, name, image_url, fetched_at)"
                     " VALUES (?, ?, ?, ?)", (hid, name, f"http://img/{hid}.png", JUNE))
    for iid, name in {IT_A: "Headshot Booster", IT_B: "Extra Health"}.items():
        conn.execute("INSERT INTO items(item_id, name, image_url, fetched_at)"
                     " VALUES (?, ?, ?, ?)", (iid, name, f"http://img/i{iid}.png", JUNE))
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (?, 1, ?)", (ME, JUNE))
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode, winning_team,"
        " average_badge_team0, average_badge_team1, raw_json, ingested_at)"
        " VALUES (?, ?, 1800, '1', 0, 50, 52, ?, ?)",
        (MATCH_ID, JUNE, json.dumps(_meta()), JUNE),
    )
    # match_players row for ME satisfies the match_item_purchases FK.
    conn.execute("INSERT INTO match_players(match_id, account_id, hero_id, team, won)"
                 " VALUES (?, ?, ?, 0, 1)", (MATCH_ID, ME, H_ME))
    for item_id, buy, sold in ((IT_A, 600, 0), (IT_B, 200, 1500)):
        conn.execute("INSERT INTO match_item_purchases(match_id, account_id, item_id,"
                     " purchase_time_s, sold_time_s) VALUES (?, ?, ?, ?, ?)",
                     (MATCH_ID, ME, item_id, buy, sold))
    conn.commit()


def test_service_enriches_players_and_marks_you(db):
    _seed(db)
    detail = service.match_detail(db, MATCH_ID)        # default perspective = self
    assert detail["account_id"] == ME
    assert detail["game_mode"] == "1"                   # string, straight from the column
    assert len(detail["players"]) == 4

    me = next(p for p in detail["players"] if p["account_id"] == ME)
    assert me["hero_name"] == "Wraith" and me["image_url"] == f"http://img/{H_ME}.png"
    assert me["is_you"] is True
    assert all(p["is_you"] is False for p in detail["players"] if p["account_id"] != ME)


def test_service_orders_and_enriches_your_purchases(db):
    _seed(db)
    detail = service.match_detail(db, MATCH_ID)
    buys = detail["purchases"]
    assert [b["purchase_time_s"] for b in buys] == [200, 600]      # ordered by buy time
    assert buys[0]["item_name"] == "Extra Health"
    assert buys[0]["item_image_url"] == f"http://img/i{IT_B}.png"
    assert buys[0]["sold_time_s"] == 1500 and buys[1]["sold_time_s"] == 0


def test_service_marks_your_kills_and_deaths(db):
    _seed(db)
    deaths = service.match_detail(db, MATCH_ID)["deaths"]
    my_death = next(d for d in deaths if d["game_time_s"] == 420)
    assert my_death["victim_is_you"] is True and my_death["killer_is_you"] is False
    my_kill = next(d for d in deaths if d["game_time_s"] == 900)
    assert my_kill["killer_is_you"] is True and my_kill["victim_is_you"] is False


def test_service_perspective_override(db):
    _seed(db)
    detail = service.match_detail(db, MATCH_ID, account_id=ALLY)
    assert detail["account_id"] == ALLY
    ally = next(p for p in detail["players"] if p["account_id"] == ALLY)
    assert ally["is_you"] is True
    assert detail["purchases"] == []                  # ALLY has no purchase rows


def test_service_returns_none_for_missing_match(db):
    _seed(db)
    assert service.match_detail(db, 123456) is None


# ── Endpoint ─────────────────────────────────────────────────────────────────

@pytest.fixture
def detail_db(tmp_path, monkeypatch):
    """A migrated, seeded DB exposed via DEADLOCK_DB so the FastAPI app reads it."""
    path = tmp_path / "detail.db"
    conn = connect(path)
    migrate(conn)
    _seed(conn)
    conn.close()
    monkeypatch.setenv("DEADLOCK_DB", str(path))
    return path


def test_endpoint_returns_detail(detail_db):
    res = TestClient(app).get(f"/api/matches/{MATCH_ID}")
    assert res.status_code == 200
    body = res.json()
    assert len(body["players"]) == 4
    assert len(body["deaths"]) == 4
    assert len(body["purchases"]) == 2


def test_endpoint_unknown_match_is_404(detail_db):
    res = TestClient(app).get("/api/matches/123456")
    assert res.status_code == 404
