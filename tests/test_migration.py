"""Tests: schema migration correctness and idempotency."""
import pytest
import sqlite3

from tracker.db import connect
from tracker.migrate import migrate

EXPECTED_TABLES = {
    "heroes", "items", "patch_eras",
    "matches", "match_players", "account_rank_history", "match_item_purchases",
    "baseline_hero_matchups", "baseline_hero_item_stats", "baseline_snapshots",
    "tracked_accounts", "sync_state", "fetch_queue", "raw_api_responses",
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


def test_user_version_is_1(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    assert version == 1


def test_migrate_is_idempotent(tmp_path):
    conn = connect(tmp_path / "idem.db")
    migrate(conn)
    migrate(conn)  # second call must not raise
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 1


def test_foreign_keys_enforced(db):
    """Inserting a match_players row with an unknown hero_id must fail."""
    db.execute(
        "INSERT INTO matches VALUES (1,'2026-01-01T00:00:00Z',1800,NULL,0,NULL,NULL,NULL,'{}','2026-01-01T00:00:00Z')"
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO match_players VALUES (1,100,9999,0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,1)"
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
