"""Shared pytest fixtures."""
import os
import sqlite3

import pytest
from hypothesis import settings

from tracker.db import connect
from tracker.migrate import migrate


# Hypothesis profiles.
# "default" is used locally and in most CI runs.
# Set HYPOTHESIS_PROFILE=ci for a longer exhaustive run (e.g., nightly).
# deadline=None prevents slow CI machines from failing timing checks.
settings.register_profile("default", max_examples=200, deadline=None)
settings.register_profile("ci", max_examples=500, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    """Fresh migrated SQLite database for each test."""
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    return conn


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Hard rule 3: no test may hit the live API. Any urlopen call fails loudly."""
    def _blocked(*args, **kwargs):
        raise AssertionError("Network access attempted in a test (urlopen blocked)")
    monkeypatch.setattr("urllib.request.urlopen", _blocked)


@pytest.fixture(autouse=True)
def _no_steam_key(monkeypatch):
    """Default every test to the keyless path so a STEAM_API_KEY in the dev's
    real environment can't make persona fetching fire mid-test. Persona tests
    opt in explicitly with monkeypatch.setenv('STEAM_API_KEY', ...)."""
    monkeypatch.delenv("STEAM_API_KEY", raising=False)


# ── Presentation-layer fixture (api_db) ──────────────────────────────────────
#
# A small, fully controlled world for the API/CLI acceptance scenarios. Counts
# are hand-picked so each scenario's verdict is predictable (see comments).

ME = 1                 # the tracked "self" account
ENEMY = 900_000        # synthetic enemy account (not tracked)
SNAPSHOT = 1

HERO_ME = 7            # the hero "I" play in Normal games
HERO_BRAWL = 81        # the hero "I" play in Street Brawl

E_TWO = 17             # scenario 1: only 2 games -> never a verdict
E_BRACKET = 10         # scenario 2: split across rank brackets
E_ERA = 15             # scenario 3: split across the E1/E2 boundary
E_WEAK = 20            # scenario 5: confirmed weakness
E_WATCH = 25           # scenario 5: large delta, unconfirmed -> watch list
E_BRAWL = 30           # scenario 7: Street Brawl opponent

IT_WEAK = 100          # item paralleling the confirmed weakness
IT_WATCH = 101         # item paralleling the watch-list entry

ERA1, ERA2 = 1, 2      # autoincrement era ids (E1 seeded first)
MAY = "2026-05-15T12:00:00+00:00"     # before the E2 boundary
JUNE = "2026-06-15T12:00:00+00:00"    # after the E2 boundary
BADGE_LOW, BADGE_MID = 20, 50         # land in the 0-30 and 31-60 brackets


def _seed(conn: sqlite3.Connection) -> None:
    heroes = {
        HERO_ME: "Wraith", HERO_BRAWL: "Abrams", E_TWO: "Haze", E_BRACKET: "McGinnis",
        E_ERA: "Bebop", E_WEAK: "Lash", E_WATCH: "Vindicta", E_BRAWL: "Brawler",
    }
    for hid, name in heroes.items():
        conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, ?)",
                     (hid, name, JUNE))
    for iid, name in {IT_WEAK: "Headshot Booster", IT_WATCH: "Extra Health"}.items():
        conn.execute("INSERT INTO items(item_id, name, fetched_at) VALUES (?, ?, ?)",
                     (iid, name, JUNE))

    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (?, 1, ?)", (ME, JUNE))
    # is_self lives on the per-user link now (migration 011); user 1 is seeded.
    conn.execute("INSERT INTO user_accounts(user_id, account_id, is_self, added_at)"
                 " VALUES (1, ?, 1, ?)", (ME, JUNE))
    # Migration 013 pre-seeds 12 curated eras; this fixture builds its own E1/E2
    # world and relies on autoincrement giving them ids 1 and 2 (see ERA1, ERA2).
    conn.execute("DELETE FROM patch_eras")
    conn.execute("DELETE FROM sqlite_sequence WHERE name = 'patch_eras'")
    conn.execute("INSERT INTO patch_eras(label, started_at) VALUES ('E1', '2020-01-01T00:00:00+00:00')")
    conn.execute("INSERT INTO patch_eras(label, started_at) VALUES ('E2', '2026-06-01T00:00:00+00:00')")

    def match(match_id, *, start, mode, my_hero, enemy, badge, won, era, items=()):
        winning_team = 0 if won else 1   # I am always team 0
        conn.execute(
            "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
            " winning_team, era_id, average_badge_team0, average_badge_team1,"
            " raw_json, ingested_at) VALUES (?, ?, 1800, ?, ?, ?, ?, ?, '{}', ?)",
            (match_id, start, mode, winning_team, era, badge, badge, JUNE),
        )
        # player_slot is the per-match key now; ME is slot 1, ENEMY slot 2.
        conn.execute("INSERT INTO match_players(match_id, player_slot, account_id, hero_id, team, won)"
                     " VALUES (?, 1, ?, ?, 0, ?)", (match_id, ME, my_hero, int(winning_team == 0)))
        conn.execute("INSERT INTO match_players(match_id, player_slot, account_id, hero_id, team, won)"
                     " VALUES (?, 2, ?, ?, 1, ?)", (match_id, ENEMY, enemy, int(winning_team == 1)))
        for item_id, buy_s in items:
            conn.execute("INSERT INTO match_item_purchases(match_id, player_slot, account_id, item_id,"
                         " purchase_time_s, sold_time_s) VALUES (?, 1, ?, ?, ?, 0)",
                         (match_id, ME, item_id, buy_s))

    # Scenario 1: 2 games vs Haze, both wins -> interval too wide for a verdict.
    for i in range(2):
        match(1000 + i, start=JUNE, mode="1", my_hero=HERO_ME, enemy=E_TWO,
              badge=BADGE_MID, won=True, era=ERA2)

    # Scenario 2: vs McGinnis, 4 games at low badge (2W) + 4 at mid badge (3W).
    for i in range(4):
        match(2000 + i, start=JUNE, mode="1", my_hero=HERO_ME, enemy=E_BRACKET,
              badge=BADGE_LOW, won=(i < 2), era=ERA2)
    for i in range(4):
        match(2100 + i, start=JUNE, mode="1", my_hero=HERO_ME, enemy=E_BRACKET,
              badge=BADGE_MID, won=(i < 3), era=ERA2)

    # Scenario 3: vs Bebop, 2 games in May (E1) + 2 in June (E2).
    match(3000, start=MAY, mode="1", my_hero=HERO_ME, enemy=E_ERA, badge=BADGE_MID, won=True, era=ERA1)
    match(3001, start=MAY, mode="1", my_hero=HERO_ME, enemy=E_ERA, badge=BADGE_MID, won=False, era=ERA1)
    match(3100, start=JUNE, mode="1", my_hero=HERO_ME, enemy=E_ERA, badge=BADGE_MID, won=True, era=ERA2)
    match(3101, start=JUNE, mode="1", my_hero=HERO_ME, enemy=E_ERA, badge=BADGE_MID, won=False, era=ERA2)

    # Scenario 5: confirmed weakness vs Lash (6W/30) + item IT_WEAK on each.
    for i in range(30):
        match(4000 + i, start=JUNE, mode="1", my_hero=HERO_ME, enemy=E_WEAK,
              badge=BADGE_MID, won=(i < 6), era=ERA2, items=[(IT_WEAK, 600)])
    # Scenario 5: watch list vs Vindicta (1W/5) + item IT_WATCH on each.
    for i in range(5):
        match(4100 + i, start=JUNE, mode="1", my_hero=HERO_ME, enemy=E_WATCH,
              badge=BADGE_MID, won=(i < 1), era=ERA2, items=[(IT_WATCH, 350)])

    # Scenario 7: Street Brawl games (game_mode=4) must never mix with Normal.
    for i in range(3):
        match(5000 + i, start=JUNE, mode="4", my_hero=HERO_BRAWL, enemy=E_BRAWL,
              badge=BADGE_MID, won=(i < 2), era=ERA2)

    # ── Global baselines (snapshot 1) ────────────────────────────────────────
    conn.execute("INSERT INTO baseline_snapshots(snapshot_id, fetched_at, notes)"
                 " VALUES (?, ?, 'test')", (SNAPSHOT, JUNE))

    def base_matchup(hero, enemy, era, bmin, bmax, wins, matches):
        conn.execute(
            "INSERT INTO baseline_hero_matchups(snapshot_id, hero_id, enemy_hero_id,"
            " era_id, badge_min, badge_max, wins, matches, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (SNAPSHOT, hero, enemy, era, bmin, bmax, wins, matches, JUNE),
        )

    # All-time (era 0) baselines at the full bracket, global rate ~0.5.
    base_matchup(HERO_ME, E_TWO, 0, 0, 116, 50, 100)
    base_matchup(HERO_ME, E_WEAK, 0, 0, 116, 250, 500)
    base_matchup(HERO_ME, E_WATCH, 0, 0, 116, 250, 500)
    # vs McGinnis split across THREE brackets so a slider change re-sums them.
    base_matchup(HERO_ME, E_BRACKET, 0, 0, 30, 30, 100)
    base_matchup(HERO_ME, E_BRACKET, 0, 31, 60, 60, 100)
    base_matchup(HERO_ME, E_BRACKET, 0, 61, 116, 10, 100)
    # Era-2 baseline for the era-redraw scenario.
    base_matchup(HERO_ME, E_ERA, ERA2, 0, 116, 50, 100)

    for item, avg in ((IT_WEAK, 540.0), (IT_WATCH, 300.0)):
        conn.execute(
            "INSERT INTO baseline_hero_item_stats(snapshot_id, hero_id, item_id,"
            " era_id, badge_min, badge_max, wins, matches, avg_purchase_s, fetched_at)"
            " VALUES (?, ?, ?, 0, 0, 116, 250, 500, ?, ?)",
            (SNAPSHOT, HERO_ME, item, avg, JUNE),
        )

    conn.commit()


@pytest.fixture
def api_db(tmp_path, monkeypatch) -> sqlite3.Connection:
    """A migrated DB seeded with the presentation-layer scenarios, with
    DEADLOCK_DB pointed at it so the FastAPI app and the CLI read the same data."""
    path = tmp_path / "api.db"
    conn = connect(path)
    migrate(conn)
    _seed(conn)
    monkeypatch.setenv("DEADLOCK_DB", str(path))
    return conn


@pytest.fixture
def empty_db_path(tmp_path, monkeypatch):
    """A migrated-but-empty DB, exposed via DEADLOCK_DB for empty-state tests."""
    path = tmp_path / "empty.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    monkeypatch.setenv("DEADLOCK_DB", str(path))
    return path
