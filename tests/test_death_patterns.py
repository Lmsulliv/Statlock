"""Death patterns: kill_events aggregated for coaching (queries -> service ->
endpoint + CLI).

Two views, with the honesty rules the prompt asks for:
- by_enemy_hero: raw deaths + games faced, NO verdict (no stored baseline).
- timeline: deaths bucketed by game-minute vs a LIVE population baseline, with a
  lower-is-better verdict (fewer deaths than the field reads as a strength).

The pure binning is covered in test_stats_deaths.py; these tests exercise the
assembly, the attribution-by-slot, and the API==CLI parity.

Hard rule 3 (no live API) holds trivially: every read is local SQLite; the autouse
_no_network fixture blocks urlopen.
"""
import pytest
from fastapi.testclient import TestClient

from api import service
from api.app import app
from api.scope import make_scope
from stats import VERDICT_CLEAR_STRENGTH, VERDICT_CLEAR_WEAKNESS, VERDICT_NOT_ENOUGH_DATA
from stats import __main__ as cli
from tracker.db import connect
from tracker.migrate import migrate

WHEN = "2026-06-15T12:00:00+00:00"
SELF = 100
H_SELF, E1, E2, E3 = 1, 2, 3, 4          # E3 = an enemy faced but never fatal


def _heroes(conn):
    for hid, name in {H_SELF: "Wraith", E1: "Bebop", E2: "Lash", E3: "Haze"}.items():
        conn.execute("INSERT INTO heroes(hero_id, name, image_url, fetched_at)"
                     " VALUES (?, ?, ?, ?)", (hid, name, f"http://img/{hid}.png", WHEN))


def _match(conn, match_id, *, era=None, mode="1", badge=50, won=True, dur=1800):
    winning_team = 0 if won else 1
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode, winning_team,"
        " era_id, average_badge_team0, average_badge_team1, raw_json, ingested_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)",
        (match_id, WHEN, dur, mode, winning_team, era, badge, badge, WHEN))


def _player(conn, match_id, slot, account_id, hero_id, team):
    won = int(team == 0)
    conn.execute("INSERT INTO match_players(match_id, player_slot, account_id, hero_id,"
                 " team, lane, won) VALUES (?, ?, ?, ?, ?, 1, ?)",
                 (match_id, slot, account_id, hero_id, team, won))


def _kill(conn, match_id, killer_slot, victim_slot, game_time_s):
    conn.execute("INSERT INTO kill_events(match_id, game_time_s, victim_slot, killer_slot)"
                 " VALUES (?, ?, ?, ?)", (match_id, game_time_s, victim_slot, killer_slot))


def _damage(conn, match_id, victim_slot, source_slot, damage_taken):
    conn.execute("INSERT INTO damage_taken_sources(match_id, victim_slot, source_slot,"
                 " damage_taken) VALUES (?, ?, ?, ?)",
                 (match_id, victim_slot, source_slot, damage_taken))


# ── By enemy hero (raw counts + games faced, no verdict) ─────────────────────

@pytest.fixture
def by_hero_db(db):
    """Two matches. Match 1 has E1, the anonymized E2 (account 0), and E3 on the
    enemy team; match 2 has E1 and E3. E1 kills me 3x in M1 (one with no timestamp)
    + 1x in M2; the anon E2 kills me once; a tower kills me once (no hero); E3 never
    does. I also kill E1 once (a population death, not mine)."""
    _heroes(db)
    # Match 1
    _match(db, 1)
    _player(db, 1, 1, SELF, H_SELF, 0)
    _player(db, 1, 2, 200, E1, 1)
    _player(db, 1, 3, 0, E2, 1)          # anonymized opponent
    _player(db, 1, 4, 201, E3, 1)
    _kill(db, 1, 2, 1, 120)              # E1 -> me, minute 2
    _kill(db, 1, 2, 1, 650)             # E1 -> me, minute 10
    _kill(db, 1, 2, 1, None)            # E1 -> me, no timestamp (by-hero only)
    _kill(db, 1, 3, 1, 130)             # anon E2 -> me, minute 2
    _kill(db, 1, None, 1, 200)          # tower -> me (NULL killer, no hero)
    _kill(db, 1, 1, 2, 300)             # me -> E1 (population death, not mine)
    _damage(db, 1, 1, 2, 1000)          # E1 -> me, 1000 gross
    _damage(db, 1, 1, 3, 500)           # anon E2 -> me, 500 (still attributed to Lash)
    _damage(db, 1, 1, None, 9999)       # environment -> me (NULL source, excluded)
    _damage(db, 1, 2, 1, 700)           # me -> E1 (population's victim, not mine)
    # Match 2 (no E2)
    _match(db, 2)
    _player(db, 2, 1, SELF, H_SELF, 0)
    _player(db, 2, 2, 200, E1, 1)
    _player(db, 2, 4, 201, E3, 1)
    _kill(db, 2, 2, 1, 300)             # E1 -> me, minute 5
    _damage(db, 2, 1, 2, 2000)          # E1 -> me, 2000 gross (E3 never damages me)
    db.commit()
    return db


def _by_hero(result, hero_id):
    return next(r for r in result["by_enemy_hero"] if r["enemy_hero_id"] == hero_id)


def test_by_hero_counts_deaths_and_games_faced(by_hero_db):
    result = service.death_patterns(by_hero_db, make_scope(account_id=SELF))
    e1 = _by_hero(result, E1)
    assert e1["games_faced"] == 2            # faced in both matches
    assert e1["deaths"] == 4                 # 3 in M1 (incl. the untimed one) + 1 in M2
    assert e1["enemy_hero_name"] == "Bebop"
    assert e1["enemy_hero_image_url"] == f"http://img/{E1}.png"


def test_by_hero_keeps_a_faced_but_never_fatal_hero_at_zero(by_hero_db):
    e3 = _by_hero(service.death_patterns(by_hero_db, make_scope(account_id=SELF)), E3)
    assert e3["games_faced"] == 2 and e3["deaths"] == 0


def test_by_hero_attributes_an_anonymized_killer_by_its_hero(by_hero_db):
    e2 = _by_hero(service.death_patterns(by_hero_db, make_scope(account_id=SELF)), E2)
    assert e2["deaths"] == 1                 # account_id 0, still counted under Lash
    assert e2["games_faced"] == 1


def test_by_hero_excludes_tower_and_creep_deaths(by_hero_db):
    result = service.death_patterns(by_hero_db, make_scope(account_id=SELF))
    # The tower kill belongs to no hero, so it inflates nobody's count: the hero
    # totals sum to 5 (E1 4 + E2 1), not the 6 deaths I actually suffered with a
    # killer recorded.
    assert sum(r["deaths"] for r in result["by_enemy_hero"]) == 5


def test_by_hero_is_ordered_worst_killer_first(by_hero_db):
    rows = service.death_patterns(by_hero_db, make_scope(account_id=SELF))["by_enemy_hero"]
    assert [r["enemy_hero_id"] for r in rows] == [E1, E2, E3]   # 4, 1, 0 deaths


# ── Damage by enemy hero (gross avg/game, no verdict) ────────────────────────

def _by_damage(result, hero_id):
    return next(r for r in result["by_damage_source"] if r["enemy_hero_id"] == hero_id)


def test_by_damage_averages_gross_damage_per_game(by_hero_db):
    e1 = _by_damage(service.death_patterns(by_hero_db, make_scope(account_id=SELF)), E1)
    assert e1["games_faced"] == 2            # faced in both matches
    assert e1["total_damage"] == 3000        # 1000 in M1 + 2000 in M2
    assert e1["avg_per_game"] == 1500.0      # 3000 / 2 games faced
    assert e1["enemy_hero_name"] == "Bebop"
    assert e1["enemy_hero_image_url"] == f"http://img/{E1}.png"


def test_by_damage_attributes_an_anonymized_source_by_its_hero(by_hero_db):
    e2 = _by_damage(service.death_patterns(by_hero_db, make_scope(account_id=SELF)), E2)
    assert e2["total_damage"] == 500         # account_id 0, still counted under Lash
    assert e2["games_faced"] == 1
    assert e2["avg_per_game"] == 500.0


def test_by_damage_keeps_a_faced_but_harmless_hero_at_zero(by_hero_db):
    e3 = _by_damage(service.death_patterns(by_hero_db, make_scope(account_id=SELF)), E3)
    assert e3["games_faced"] == 2 and e3["total_damage"] == 0 and e3["avg_per_game"] == 0.0


def test_by_damage_excludes_environment_and_my_own_damage(by_hero_db):
    rows = service.death_patterns(by_hero_db, make_scope(account_id=SELF))["by_damage_source"]
    # The 9999 environment row (NULL source) and the 700 I dealt to E1 belong to
    # no enemy facing me, so the hero totals sum to exactly 3000 + 500, not more.
    assert sum(r["total_damage"] for r in rows) == 3500


def test_by_damage_is_ordered_hardest_hitter_first(by_hero_db):
    rows = service.death_patterns(by_hero_db, make_scope(account_id=SELF))["by_damage_source"]
    assert [r["enemy_hero_id"] for r in rows] == [E1, E2, E3]   # 1500, 500, 0 avg


# ── Timeline (game-minute bins vs a live population baseline) ─────────────────

ERA1, ERA2 = 1, 2


@pytest.fixture
def timeline_db(tmp_path, monkeypatch):
    """Era 1: six matches, me (team 0) vs one E1 (team 1). Every game I die once in
    minute 10; every game I kill E1 once in minute 2 (a population death). Match 10
    adds one untimed death of mine. Era 2: five me-only matches (no opponent) where
    I die in minute 5 -- a scope with personal data but no population, to prove the
    no-baseline path. Exposed via DEADLOCK_DB so the app and CLI read it too."""
    path = tmp_path / "deaths.db"
    conn = connect(path)
    migrate(conn)
    _heroes(conn)
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (?, 1, ?)", (SELF, WHEN))
    conn.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
                 " VALUES (1, ?, 1, ?)", (SELF, WHEN))
    # Migration 013 pre-seeds 12 curated eras; reset so E1/E2 get ids 1 and 2.
    conn.execute("DELETE FROM patch_eras")
    conn.execute("DELETE FROM sqlite_sequence WHERE name = 'patch_eras'")
    conn.execute("INSERT INTO patch_eras(label, started_at)"
                 " VALUES ('E1', '2026-01-01T00:00:00+00:00')")
    conn.execute("INSERT INTO patch_eras(label, started_at)"
                 " VALUES ('E2', '2026-04-01T00:00:00+00:00')")

    for i in range(6):
        mid = 10 + i
        _match(conn, mid, era=ERA1)
        _player(conn, mid, 1, SELF, H_SELF, 0)
        _player(conn, mid, 2, 200, E1, 1)
        _kill(conn, mid, 2, 1, 610)         # E1 -> me, minute 10
        _kill(conn, mid, 1, 2, 130)         # me -> E1, minute 2 (population death)
    _kill(conn, 10, 2, 1, None)             # one untimed death of mine (by-hero only)

    for i in range(5):
        mid = 20 + i
        _match(conn, mid, era=ERA2)
        _player(conn, mid, 1, SELF, H_SELF, 0)   # me only: no population this era
        _kill(conn, mid, None, 1, 320)      # tower -> me, minute 5
    conn.commit()
    monkeypatch.setenv("DEADLOCK_DB", str(path))
    return conn


def _bin(result, minute):
    return next(b for b in result["timeline"] if b["minute"] == minute)


def test_timeline_dying_less_than_the_field_is_a_strength(timeline_db):
    result = service.death_patterns(timeline_db, make_scope(account_id=SELF, era_ids="1"))
    assert result["games"] == 6
    minute2 = _bin(result, 2)
    assert minute2["deaths"] == 0             # I never die in minute 2
    assert minute2["mean"] == 0.0
    assert minute2["baseline_mean"] == 1.0    # the field dies once a game here
    assert minute2["verdict"] == VERDICT_CLEAR_STRENGTH


def test_timeline_dying_more_than_the_field_is_a_weakness(timeline_db):
    result = service.death_patterns(timeline_db, make_scope(account_id=SELF, era_ids="1"))
    minute10 = _bin(result, 10)
    assert minute10["deaths"] == 6
    assert minute10["mean"] == 1.0
    assert minute10["baseline_mean"] == 0.0
    assert minute10["verdict"] == VERDICT_CLEAR_WEAKNESS


def test_timeline_excludes_untimed_deaths_but_by_hero_keeps_them(timeline_db):
    result = service.death_patterns(timeline_db, make_scope(account_id=SELF, era_ids="1"))
    # The untimed death can't be placed on the timeline...
    assert result["total_deaths"] == 6
    # ...but it still counts in the by-hero ranking (E1 killed me 6 timed + 1 untimed).
    assert _by_hero(result, E1)["deaths"] == 7


def test_timeline_without_a_population_shows_no_verdict(timeline_db):
    # Era 2 has my games but nobody else -> no baseline, never a fabricated verdict.
    result = service.death_patterns(timeline_db, make_scope(account_id=SELF, era_ids="2"))
    assert result["games"] == 5
    minute5 = _bin(result, 5)
    assert minute5["deaths"] == 5
    assert minute5["baseline_mean"] is None
    assert minute5["verdict"] == VERDICT_NOT_ENOUGH_DATA


# ── Empty scope + two-caller parity ──────────────────────────────────────────

def test_empty_scope_returns_an_empty_response(db):
    _heroes(db)
    db.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
               " VALUES (?, 1, ?)", (SELF, WHEN))
    db.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
               " VALUES (1, ?, 1, ?)", (SELF, WHEN))
    result = service.death_patterns(db, make_scope(account_id=SELF))
    assert result == {"by_enemy_hero": [], "by_damage_source": [], "timeline": [],
                      "total_deaths": 0, "games": 0}


def test_api_and_cli_match_the_service(timeline_db, capsys):
    scope = make_scope(account_id=SELF, era_ids="1")
    result = service.death_patterns(timeline_db, scope)

    api_result = TestClient(app).get("/api/death-patterns?era_ids=1&account_id=100").json()
    assert api_result == result

    cli.main(["deaths", "--account", str(SELF), "--era", "1"])
    out = capsys.readouterr().out
    assert out.strip() == cli.render_deaths(result, scope).strip()
