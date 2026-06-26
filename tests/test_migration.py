"""Tests: schema migration correctness and idempotency."""
import pytest
import sqlite3

from tracker.db import connect
from tracker.migrate import migrate

EXPECTED_TABLES = {
    "heroes", "items", "ranks", "patch_eras",
    "matches", "match_players", "account_rank_history", "match_item_purchases",
    "baseline_hero_matchups", "baseline_hero_item_stats", "baseline_snapshots",
    "baseline_refresh_state",
    "tracked_accounts", "sync_state", "fetch_queue", "raw_api_responses",
    "era_candidates", "worker_meta", "kill_events", "steam_personas", "account_labels",
    "laning_stats", "users", "user_accounts", "sessions",
}
EXPECTED_VIEWS   = {"v_my_matchups", "v_my_item_stats"}
EXPECTED_INDEXES = {"idx_mp_account", "idx_mp_hero",
                    "idx_ke_match_victim", "idx_ke_match_killer",
                    "idx_ls_match_slot"}


def names_of_type(conn: sqlite3.Connection, obj_type: str) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ?", (obj_type,)
    ).fetchall()
    return {r["name"] for r in rows}


def test_all_tables_created(db):
    assert EXPECTED_TABLES <= names_of_type(db, "table")


def test_all_views_created(db):
    assert EXPECTED_VIEWS <= names_of_type(db, "view")


def test_all_indexes_created(db):
    assert EXPECTED_INDEXES <= names_of_type(db, "index")


def test_user_version_is_13(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    assert version == 13


def test_migrate_is_idempotent(tmp_path):
    conn = connect(tmp_path / "idem.db")
    migrate(conn)
    migrate(conn)  # second call must not raise
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 13


def test_steam_personas_columns(db):
    cols = {row[1] for row in db.execute("PRAGMA table_info(steam_personas)").fetchall()}
    assert cols == {"account_id", "persona_name", "avatar_url", "fetched_at"}


def test_account_labels_columns(db):
    cols = {row[1] for row in db.execute("PRAGMA table_info(account_labels)").fetchall()}
    assert cols == {"user_id", "account_id", "display_name", "updated_at"}


def test_v9_copies_tracked_display_names_into_labels(tmp_path):
    """Upgrading to v9 must seed account_labels from every existing
    tracked_accounts.display_name, so no manual name is lost when the resolver
    stops reading that column. Empty/NULL names are not copied. v11 then re-keys
    the GLOBAL_OWNER 0 rows onto the seeded first user (user 1)."""
    from tracker.migrate import _STEPS

    conn = connect(tmp_path / "v8.db")
    for sql_file in _STEPS[:8]:                 # build the schema up to v8
        conn.executescript(sql_file.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 8")
    conn.execute("INSERT INTO tracked_accounts(account_id, display_name, added_at)"
                 " VALUES (5, 'Named', 't')")
    conn.execute("INSERT INTO tracked_accounts(account_id, display_name, added_at)"
                 " VALUES (6, NULL, 't')")          # no name -> not copied
    conn.commit()

    migrate(conn)  # applies 009 (and onward to the latest version)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    labels = {r["account_id"]: r["display_name"] for r in
              conn.execute("SELECT account_id, display_name FROM account_labels"
                           " WHERE user_id = 1")}
    assert labels == {5: "Named"}


def test_v12_adds_auth_schema(db):
    """v12 adds the Steam identity column to users and the sessions table."""
    user_cols = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
    assert {"user_id", "created_at", "steam_account_id"} <= user_cols
    session_cols = {row[1] for row in db.execute("PRAGMA table_info(sessions)").fetchall()}
    assert session_cols == {"token", "user_id", "created_at", "expires_at"}


def test_v12_steam_account_id_unique(db):
    """Two users can't claim the same Steam account (the login uniqueness guard)."""
    db.execute("INSERT INTO users(steam_account_id, created_at) VALUES (42, 't')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO users(steam_account_id, created_at) VALUES (42, 't')")


def test_v11_seeds_first_user(db):
    """A fresh migrate seeds the default user (id 1) so local/dev has an identity
    even before any account is tracked."""
    user_ids = [r["user_id"] for r in db.execute("SELECT user_id FROM users")]
    assert user_ids == [1]


def test_v11_links_tracked_accounts_to_first_user(tmp_path):
    """Upgrading to v11 must mirror every existing tracked account onto user 1 via
    user_accounts, carrying the is_self flag so resolve_self keeps working."""
    from tracker.migrate import _STEPS

    conn = connect(tmp_path / "v10.db")
    for sql_file in _STEPS[:10]:                # build the schema up to v10
        conn.executescript(sql_file.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 10")
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (50, 1, 't')")        # the self account
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (60, 0, 't')")        # a non-self tracked account
    conn.commit()

    migrate(conn)  # applies 011

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    links = {r["account_id"]: r["is_self"] for r in
             conn.execute("SELECT account_id, is_self FROM user_accounts WHERE user_id = 1")}
    assert links == {50: 1, 60: 0}


def test_v11_rekeys_account_labels_owner_to_user(tmp_path):
    """The GLOBAL_OWNER 0 label rows must move onto user 1 (no name lost)."""
    from tracker.migrate import _STEPS

    conn = connect(tmp_path / "v10.db")
    for sql_file in _STEPS[:10]:
        conn.executescript(sql_file.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 10")
    conn.execute("INSERT INTO account_labels(owner_id, account_id, display_name, updated_at)"
                 " VALUES (0, 77, 'Rival', 't')")
    conn.commit()

    migrate(conn)  # applies 011

    rows = conn.execute(
        "SELECT user_id, display_name FROM account_labels WHERE account_id = 77"
    ).fetchall()
    assert [(r["user_id"], r["display_name"]) for r in rows] == [(1, "Rival")]


def test_fetch_queue_has_deferred_since(db):
    cols = {row[1] for row in db.execute("PRAGMA table_info(fetch_queue)").fetchall()}
    assert "deferred_since" in cols


def test_upgrade_from_v1_preserves_data(tmp_path):
    """A database stopped at v1 (pre-worker schema) upgrades in place."""
    from tracker.migrate import _STEPS

    conn = connect(tmp_path / "v1.db")
    conn.executescript(_STEPS[0].read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO tracked_accounts(account_id, display_name, added_at) "
        "VALUES (891231519, 'me', '2026-06-01T00:00:00Z')"
    )
    conn.commit()

    migrate(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    assert conn.execute("SELECT COUNT(*) FROM tracked_accounts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM era_candidates").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ranks").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM baseline_refresh_state").fetchone()[0] == 0


def test_v5_backfills_player_slot_from_raw_json(tmp_path):
    """Upgrading a v4 DB must read each match's archived raw_json and stamp every
    match_players / match_item_purchases row with its player_slot, including the
    single anonymized (account_id 0) player a stored match may hold."""
    import json
    from tracker.migrate import _STEPS

    conn = connect(tmp_path / "v4.db")
    for sql_file in _STEPS[:4]:                 # build the schema up to v4
        conn.executescript(sql_file.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 4")
    conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (7, 'Wraith', 't')")
    conn.execute("INSERT INTO items(item_id, name, fetched_at) VALUES (100, 'Boots', 't')")

    # raw_json maps account_id -> player_slot (slots deliberately non-contiguous).
    raw = {"match_info": {"match_id": 1, "players": [
        {"player_slot": 4, "account_id": 500},
        {"player_slot": 7, "account_id": 0},      # one anonymized player
        {"player_slot": 9, "account_id": 600},
    ]}}
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, winning_team,"
        " raw_json, ingested_at) VALUES (1, 't', 1800, 0, ?, 't')",
        (json.dumps(raw),),
    )
    for account_id, team, won in ((500, 0, 1), (0, 1, 0), (600, 1, 0)):
        conn.execute(
            "INSERT INTO match_players(match_id, account_id, hero_id, team, won)"
            " VALUES (1, ?, 7, ?, ?)", (account_id, team, won))
    conn.execute(
        "INSERT INTO match_item_purchases(match_id, account_id, item_id,"
        " purchase_time_s, sold_time_s) VALUES (1, 500, 100, 600, 0)")
    conn.commit()

    migrate(conn)  # applies 005 (and onward)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    slots = {r["account_id"]: r["player_slot"] for r in
             conn.execute("SELECT account_id, player_slot FROM match_players WHERE match_id = 1")}
    assert slots == {500: 4, 0: 7, 600: 9}
    # the purchase follows its buyer to slot 4
    assert conn.execute(
        "SELECT player_slot FROM match_item_purchases WHERE match_id = 1 AND item_id = 100"
    ).fetchone()["player_slot"] == 4


def test_era_candidates_post_url_unique(db):
    db.execute(
        "INSERT INTO era_candidates(post_url, posted_at) VALUES ('http://x', '2026-06-01')"
    )
    db.execute(
        "INSERT OR IGNORE INTO era_candidates(post_url, posted_at) VALUES ('http://x', '2026-06-01')"
    )
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM era_candidates").fetchone()[0] == 1


def test_foreign_keys_enforced(db):
    """Inserting a match_players row with an unknown hero_id must fail."""
    db.execute(
        "INSERT INTO matches VALUES (1,'2026-01-01T00:00:00Z',1800,NULL,0,NULL,NULL,NULL,'{}','2026-01-01T00:00:00Z')"
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        # Columns: match_id, player_slot, account_id, hero_id, team, lane..healing (10 NULLs), won.
        db.execute(
            "INSERT INTO match_players VALUES (1,1,100,9999,0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,1)"
        )
        db.commit()


def test_fetch_queue_has_next_retry_at(db):
    cols = {row[1] for row in db.execute("PRAGMA table_info(fetch_queue)").fetchall()}
    assert "next_retry_at" in cols


def test_matches_has_two_average_badge_columns(db):
    cols = {row[1] for row in db.execute("PRAGMA table_info(matches)").fetchall()}
    assert "average_badge_team0" in cols
    assert "average_badge_team1" in cols
    assert "average_badge" not in cols


def test_match_item_purchases_has_sold_time_s(db):
    cols = {row[1] for row in db.execute("PRAGMA table_info(match_item_purchases)").fetchall()}
    assert "sold_time_s" in cols
    assert "sold" not in cols


def test_v13_reseeds_curated_eras(tmp_path):
    """v13 replaces all eras with the 12 curated ones, re-bins matches against the
    new boundaries (pre-first-era matches go NULL), and drops orphaned per-era
    fetched baselines while preserving the all-time sentinel (era_id = 0)."""
    from tracker.migrate import _STEPS

    conn = connect(tmp_path / "v12.db")
    for sql_file in _STEPS[:12]:               # build the schema up to v12
        conn.executescript(sql_file.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 12")

    # Matches spanning the new boundaries (era_id starts NULL; v13 re-bins them).
    def add_match(mid, start):
        conn.execute(
            "INSERT INTO matches(match_id, start_time, duration_s, winning_team,"
            " era_id, raw_json, ingested_at) VALUES (?, ?, 1800, 0, NULL, '{}', 't')",
            (mid, start),
        )
    add_match(1, "2024-12-01T00:00:00Z")        # before Major Map Rework -> NULL
    add_match(2, "2026-05-26T12:00:00Z")        # inside Urn Update 2 (2026-05-25)

    # A surviving all-time sentinel row and an orphaned per-era row in each table.
    conn.execute(
        "INSERT INTO baseline_hero_matchups(snapshot_id, hero_id, enemy_hero_id,"
        " era_id, badge_min, badge_max, same_lane, wins, matches, fetched_at)"
        " VALUES (1, 1, 2, 0, 0, 116, 0, 5, 10, 't'),"
        "        (1, 1, 2, 999, 0, 116, 0, 5, 10, 't')")
    conn.execute(
        "INSERT INTO baseline_refresh_state(era_id, last_refreshed_at)"
        " VALUES (0, 't'), (999, 't')")
    conn.commit()

    migrate(conn)  # applies 013

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13

    eras = conn.execute(
        "SELECT label, started_at FROM patch_eras ORDER BY started_at"
    ).fetchall()
    assert len(eras) == 12
    assert eras[0]["label"] == "Major Map Rework"
    assert eras[0]["started_at"] == "2025-02-25T00:00:00Z"
    assert eras[-1]["label"] == "Minor Update (Jun 11)"
    assert eras[-1]["started_at"] == "2026-06-11T00:00:00Z"

    # Re-binning: pre-first-era match stays NULL; the other binds to Urn Update 2.
    assert conn.execute("SELECT era_id FROM matches WHERE match_id = 1").fetchone()[0] is None
    urn2_id = conn.execute(
        "SELECT era_id FROM patch_eras WHERE label = 'Urn Update 2'"
    ).fetchone()[0]
    assert conn.execute("SELECT era_id FROM matches WHERE match_id = 2").fetchone()[0] == urn2_id

    # All-time sentinel survives; the orphaned era_id=999 rows are swept.
    matchup_eras = {r["era_id"] for r in
                    conn.execute("SELECT era_id FROM baseline_hero_matchups")}
    assert matchup_eras == {0}
    refresh_eras = {r["era_id"] for r in
                    conn.execute("SELECT era_id FROM baseline_refresh_state")}
    assert refresh_eras == {0}
