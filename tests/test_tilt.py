"""Service + endpoint tests for tilt analytics (/api/tilt).

Seeds one tracked account with time-spaced matches forming two known sessions,
so the bucket counts are hand-verifiable. The pure grouping math is covered in
tests/test_sessions.py; here we check the SQL -> stats -> JSON assembly and the
route, mirroring tests/test_presentation.py's style.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api import service
from api.app import app
from api.scope import make_scope
from stats import VERDICT_NOT_ENOUGH_DATA
from tracker.db import connect
from tracker.migrate import migrate

ME = 1
HERO = 7
BASE = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

# Two sittings, six minutes between games. Session B starts six hours after
# session A's last game (well past the 3h gap), so they never merge.
SESSION_A = "WLLWWL"   # 3 wins / 6
SESSION_B = "LLLLL"    # 0 wins / 5  -> a textbook losing-streak tail


def _add_match(conn, match_id: int, start: str, won: bool) -> None:
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode,"
        " winning_team, raw_json, ingested_at) VALUES (?, ?, 1800, '1', ?, '{}', ?)",
        (match_id, start, 0 if won else 1, start),
    )
    conn.execute(
        "INSERT INTO match_players(match_id, player_slot, account_id, hero_id, team, won)"
        " VALUES (?, 1, ?, ?, 0, ?)",
        (match_id, ME, HERO, 1 if won else 0),
    )


def _seed_tilt(conn) -> None:
    conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, 'Wraith', ?)",
                 (HERO, BASE.isoformat()))
    conn.execute("INSERT INTO tracked_accounts(account_id, is_self, added_at)"
                 " VALUES (?, 1, ?)", (ME, BASE.isoformat()))

    mid = 1000
    for i, c in enumerate(SESSION_A):
        _add_match(conn, mid, (BASE + timedelta(minutes=6 * i)).isoformat(), c == "W")
        mid += 1
    b0 = BASE + timedelta(hours=6)
    for i, c in enumerate(SESSION_B):
        _add_match(conn, mid, (b0 + timedelta(minutes=6 * i)).isoformat(), c == "W")
        mid += 1
    conn.commit()


@pytest.fixture
def tilt_db(tmp_path, monkeypatch):
    path = tmp_path / "tilt.db"
    conn = connect(path)
    migrate(conn)
    _seed_tilt(conn)
    monkeypatch.setenv("DEADLOCK_DB", str(path))
    return conn


# ── service.tilt ─────────────────────────────────────────────────────────────

def test_tilt_overall_and_session_count(tilt_db):
    result = service.tilt(tilt_db, make_scope())
    assert result["sessions"] == 2
    assert result["session_gap_hours"] == 3.0
    assert result["overall"] == {"games": 11, "wins": 3, "winrate": pytest.approx(3 / 11, abs=1e-4)}


def test_tilt_session_index_buckets(tilt_db):
    rows = service.tilt(tilt_db, make_scope())["by_session_index"]
    by_idx = {r["index"]: r for r in rows}
    # Indices 1-5 each have one game from A and one from B; index 6 is A-only.
    assert [r["index"] for r in rows] == [1, 2, 3, 4, 5, 6]
    assert by_idx[1]["games"] == 2 and by_idx[1]["wins"] == 1   # A:W, B:L
    assert by_idx[6]["games"] == 1 and by_idx[6]["label"] == "6+" and by_idx[6]["capped"]
    # Every index bucket here is <= 2 games, so none earns a verdict.
    assert all(r["verdict"] == VERDICT_NOT_ENOUGH_DATA for r in rows)


def test_tilt_loss_streak_buckets(tilt_db):
    rows = service.tilt(tilt_db, make_scope())["by_loss_streak"]
    by_s = {r["streak"]: r for r in rows}
    # Combined across both sessions (see SESSION_A/B):
    assert by_s[0]["games"] == 5 and by_s[0]["wins"] == 2 and by_s[0]["label"] == "0 losses"
    assert by_s[1]["games"] == 2 and by_s[1]["wins"] == 0
    assert by_s[2]["games"] == 2 and by_s[2]["wins"] == 1
    assert by_s[3]["games"] == 2 and by_s[3]["capped"] and by_s[3]["label"] == "3+ losses"
    # Thin buckets (< 5 games) never earn a verdict.
    assert by_s[3]["verdict"] == VERDICT_NOT_ENOUGH_DATA


def test_tilt_buckets_carry_stat_fields(tilt_db):
    row = service.tilt(tilt_db, make_scope())["by_session_index"][0]
    # The shared StatFields block is present and compares to the account's own
    # overall rate as the baseline.
    for key in ("winrate", "ci_low", "ci_high", "global_rate", "adjusted_rate",
                "delta", "raw_delta", "verdict"):
        assert key in row
    assert row["global_rate"] == pytest.approx(3 / 11, abs=1e-4)


def test_tilt_empty_scope_returns_empty_shape(db):
    # Migrated but no tracked account -> resolves to nothing -> empty shape.
    result = service.tilt(db, make_scope())
    assert result["by_session_index"] == []
    assert result["by_loss_streak"] == []
    assert result["overall"]["games"] == 0
    assert result["sessions"] == 0
    assert result["session_gap_hours"] == 3.0


# ── /api/tilt endpoint ───────────────────────────────────────────────────────

def test_tilt_endpoint_returns_documented_shape(tilt_db):
    res = TestClient(app).get("/api/tilt")
    assert res.status_code == 200
    body = res.json()
    assert set(body) >= {"by_session_index", "by_loss_streak", "overall",
                         "sessions", "session_gap_hours"}
    assert body["sessions"] == 2
    assert len(body["by_session_index"]) == 6


def test_tilt_endpoint_empty_db(empty_db_path):
    res = TestClient(app).get("/api/tilt")
    assert res.status_code == 200
    body = res.json()
    assert body["by_session_index"] == []
    assert body["overall"]["games"] == 0
