"""Hero skill-order: query -> service -> endpoint + CLI.

The pure aggregation (opening sequence, first-maxed, split gating) is covered in
test_ability_order.py; these tests exercise the assembly layer: grouping
ability_events into per-game sequences, decorating ability ids with names, the
sample-size split gate, the empty state, and API==CLI parity.
"""
import pytest
from fastapi.testclient import TestClient

from api import service
from api.app import app
from api.scope import make_scope
from stats import VERDICT_FLOOR
from stats import __main__ as cli
from tracker.db import connect
from tracker.migrate import migrate

ME = 1
WRAITH = 7
SOLO = 8          # a hero the owner never played -> empty state
WHEN = "2026-06-15T12:00:00+00:00"
BADGE = 50

# Abilities: (id, name, slot). Q is the signature the owner tends to max first.
ABILITIES = ((10, "Splatter", "signature"), (20, "Card Trick", "ultimate"),
             (30, "Fixation", "signature"), (40, "Smoke Bomb", "innate"))
Q, W, E, R = 10, 20, 30, 40


def _add_match(conn, match_id, won):
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
        " winning_team, era_id, average_badge_team0, average_badge_team1,"
        " raw_json, ingested_at) VALUES (?, ?, 1800, '1', ?, NULL, ?, ?, '{}', ?)",
        (match_id, WHEN, 0 if won else 1, BADGE, BADGE, WHEN))
    conn.execute(
        "INSERT INTO match_players(match_id, player_slot, account_id, hero_id,"
        " team, won) VALUES (?, 1, ?, ?, 0, ?)", (match_id, ME, WRAITH, int(won)))


def _add_skill(conn, match_id, points):
    """Insert one player's ability points (slot 1 = ME) in the given order."""
    for point_number, ability_id in enumerate(points, start=1):
        conn.execute(
            "INSERT INTO ability_events(match_id, player_slot, account_id, hero_id,"
            " ability_id, point_number, game_time_s) VALUES (?, 1, ?, ?, ?, ?, ?)",
            (match_id, ME, WRAITH, ability_id, point_number, point_number * 30))


def _maxed(first, *rest):
    """A game where `first` reaches level 4 first, after the opening `first,*rest`."""
    return [first, *rest, first, first, first]


def _seed(conn):
    for hid, name in ((WRAITH, "Wraith"), (SOLO, "Solo")):
        conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, ?)",
                     (hid, name, WHEN))
    for aid, name, slot in ABILITIES:
        conn.execute("INSERT INTO abilities(ability_id, name, ability_type, image_url,"
                     " fetched_at) VALUES (?, ?, ?, ?, ?)",
                     (aid, name, slot, f"http://img/a{aid}.png", WHEN))
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (?, 1, ?)", (ME, WHEN))
    conn.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
                 " VALUES (1, ?, 1, ?)", (ME, WHEN))
    conn.commit()


@pytest.fixture
def skill_db(tmp_path, monkeypatch):
    path = tmp_path / "skill.db"
    conn = connect(path)
    migrate(conn)
    _seed(conn)
    monkeypatch.setenv("DEADLOCK_DB", str(path))
    return conn


def test_empty_state_for_unplayed_hero(skill_db):
    out = service.hero_skill_order(skill_db, make_scope(), SOLO)
    assert out == {"hero_id": SOLO, "games": 0, "opening": None,
                   "first_maxed": None, "split": None}


def test_overall_opening_and_first_maxed_are_decorated(skill_db):
    # Three Wraith games, all opening Q W E R with Q maxed first.
    for mid in range(100, 103):
        _add_match(skill_db, mid, won=True)
        _add_skill(skill_db, mid, _maxed(Q, W, E, R))
    skill_db.commit()

    out = service.hero_skill_order(skill_db, make_scope(), WRAITH)
    assert out["games"] == 3
    assert [a["ability_name"] for a in out["opening"]["sequence"]] == [
        "Splatter", "Card Trick", "Fixation", "Smoke Bomb"]
    assert out["opening"]["games"] == 3
    assert out["first_maxed"]["ability"]["ability_name"] == "Splatter"
    assert out["first_maxed"]["ability"]["image_url"] == "http://img/a10.png"
    assert out["split"] is None                      # all wins -> no losses side


def test_split_shows_when_each_side_meets_floor(skill_db):
    mid = 200
    for _ in range(VERDICT_FLOOR):                   # wins: max Q first
        _add_match(skill_db, mid, won=True)
        _add_skill(skill_db, mid, _maxed(Q, W, E, R))
        mid += 1
    for _ in range(VERDICT_FLOOR):                   # losses: max W first
        _add_match(skill_db, mid, won=False)
        _add_skill(skill_db, mid, _maxed(W, Q, E, R))
        mid += 1
    skill_db.commit()

    out = service.hero_skill_order(skill_db, make_scope(), WRAITH)
    assert out["split"] is not None
    assert out["split"]["wins"]["first_maxed"]["ability"]["ability_name"] == "Splatter"
    assert out["split"]["losses"]["first_maxed"]["ability"]["ability_name"] == "Card Trick"
    assert out["split"]["wins"]["games"] == VERDICT_FLOOR


def test_endpoint_matches_service(skill_db):
    for mid in range(300, 303):
        _add_match(skill_db, mid, won=True)
        _add_skill(skill_db, mid, _maxed(Q, W, E, R))
    skill_db.commit()

    client = TestClient(app)
    body = client.get("/api/hero-skill-order", params={"hero_id": WRAITH}).json()
    assert body == service.hero_skill_order(skill_db, make_scope(), WRAITH)


def test_cli_renders_without_verdict(skill_db, capsys):
    for mid in range(400, 403):
        _add_match(skill_db, mid, won=True)
        _add_skill(skill_db, mid, _maxed(Q, W, E, R))
    skill_db.commit()

    cli.main(["skill-order", "--hero", str(WRAITH), "--db", str(skill_db.execute(
        "PRAGMA database_list").fetchone()[2])])
    out = capsys.readouterr().out
    assert "Splatter" in out and "Opening (first 4)" in out
    assert "Verdict" not in out                       # descriptive, never a verdict
