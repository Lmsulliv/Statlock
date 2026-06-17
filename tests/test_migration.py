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
    "era_candidates", "worker_meta",
}
EXPECTED_VIEWS   = {"v_my_matchups", "v_my_item_stats"}
EXPECTED_INDEXES = {"idx_mp_account", "idx_mp_hero"}


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


def test_user_version_is_5(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    assert version == 5


def test_migrate_is_idempotent(tmp_path):
    conn = connect(tmp_path / "idem.db")
    migrate(conn)
    migrate(conn)  # second call must not raise
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 5


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

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
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

    migrate(conn)  # applies 005

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
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
