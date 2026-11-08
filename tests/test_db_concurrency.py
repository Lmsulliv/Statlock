"""Regression tests for SQLite concurrency: the pragmas set by tracker.db.connect(),
and the worker never holding the write lock across a network call or backoff sleep.

These prove the deploy gate: the drain daemon and the API can share one SQLite
file. WAL lets readers proceed while a writer holds the lock, and a non-zero
busy_timeout makes a competing writer wait-and-retry instead of immediately
raising "database is locked".

All connections are file-backed (pytest's tmp_path), so WAL actually engages and
the -wal / -shm sidecars are auto-cleaned with the temp dir after each test.
"""
import json
import sqlite3
import threading
import time

from ingest import runner
from ingest.client import NetworkError
from ingest.maintenance import refresh_baselines
from tracker.db import connect


def test_connect_sets_expected_pragmas(db):
    """connect() applies WAL + busy_timeout and still enforces foreign keys."""
    journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    busy_timeout = db.execute("PRAGMA busy_timeout").fetchone()[0]
    foreign_keys = db.execute("PRAGMA foreign_keys").fetchone()[0]

    assert journal_mode == "wal"
    assert busy_timeout == 5000
    assert foreign_keys == 1


def test_wal_reader_not_blocked_by_writer(db, tmp_path):
    """A reader sees the last committed snapshot while a writer holds the lock."""
    # Commit a baseline row the reader can read back later.
    db.execute("INSERT INTO worker_meta(key, value) VALUES ('baseline', '1')")
    db.commit()

    # Second connection to the same file takes (and holds) the write lock.
    writer = connect(tmp_path / "test.db")
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO worker_meta(key, value) VALUES ('pending', '2')")
        # Writer's transaction is still open and uncommitted here.

        # In WAL the reader is not blocked by the open writer: this must not
        # raise "database is locked", and it sees the committed snapshot only.
        row = db.execute(
            "SELECT value FROM worker_meta WHERE key = 'baseline'"
        ).fetchone()
        assert row["value"] == "1"

        # The writer's uncommitted row is not visible to the reader's snapshot.
        assert (
            db.execute(
                "SELECT COUNT(*) FROM worker_meta WHERE key = 'pending'"
            ).fetchone()[0]
            == 0
        )
    finally:
        writer.rollback()
        writer.close()


def test_competing_writer_waits_then_succeeds(db, tmp_path):
    """A second writer waits on busy_timeout and succeeds once the lock frees."""
    path = tmp_path / "test.db"
    hold_seconds = 0.3

    # Main thread grabs and holds the write lock with an uncommitted transaction.
    db.execute("BEGIN IMMEDIATE")
    db.execute("INSERT INTO worker_meta(key, value) VALUES ('holder', '1')")

    result: dict = {}

    def competing_write():
        # Own connection, created and used entirely within this thread, so the
        # default check_same_thread=True is fine.
        conn = connect(path)
        start = time.monotonic()
        try:
            conn.execute("INSERT INTO worker_meta(key, value) VALUES ('waiter', '2')")
            conn.commit()
            result["error"] = None
        except sqlite3.OperationalError as exc:  # e.g. "database is locked"
            result["error"] = exc
        finally:
            result["elapsed"] = time.monotonic() - start
            conn.close()

    thread = threading.Thread(target=competing_write)
    thread.start()

    # Keep the lock for a beat so the competing write is forced to wait, then
    # release it. The waiter should then acquire the lock and succeed.
    time.sleep(hold_seconds)
    db.commit()
    thread.join(timeout=10)

    assert not thread.is_alive(), "competing writer did not finish"
    assert result["error"] is None, f"competing write failed: {result['error']!r}"
    # It waited for the lock rather than erroring immediately.
    assert result["elapsed"] >= hold_seconds - 0.1
    # And the write actually landed.
    assert (
        db.execute("SELECT value FROM worker_meta WHERE key = 'waiter'").fetchone()[
            "value"
        ]
        == "2"
    )


# ── No write transaction may stay open across a network call ─────────────────
#
# client.get() waits on the token bucket (5 s + up to 2 s jitter) before it even
# sends the request. If the worker's connection has an uncommitted write open at
# that moment, it holds SQLite's single write lock for longer than the 5 s
# busy_timeout, and every API write in the meantime fails with "database is
# locked". These tests pin that no worker path sleeps or fetches mid-transaction.

_COUNTER_BODY = json.dumps(
    [{"hero_id": 1, "enemy_hero_id": 2, "wins": 5, "matches_played": 10}])
_ITEM_BODY = json.dumps(
    [{"bucket": 1, "item_id": 7, "wins": 5, "matches": 10, "avg_buy_time_s": 300}])


class _BaselineClient:
    """Answers every analytics URL with one row, calling on_get(url) first so a
    test can inspect the worker's connection at the moment of the network call."""

    def __init__(self, on_get=lambda url: None):
        self.on_get = on_get

    def get(self, url):
        self.on_get(url)
        return 200, {}, _ITEM_BODY if "item-stats" in url else _COUNTER_BODY


def test_baseline_refresh_has_no_open_transaction_during_fetch(db):
    """Every client.get() in a baseline span runs with the write lock released."""
    in_txn_at_get = []
    client = _BaselineClient(lambda url: in_txn_at_get.append(db.in_transaction))

    refresh_baselines(db, client, max_spans=1)

    assert in_txn_at_get, "refresh_baselines made no fetches"
    assert not any(in_txn_at_get), (
        f"{sum(in_txn_at_get)}/{len(in_txn_at_get)} fetches ran inside a write transaction")


def test_api_write_succeeds_during_baseline_fetch(db, tmp_path):
    """End-to-end shape of the bug: a second connection (the API) writes while the
    worker is mid-fetch. A short busy_timeout keeps the test fast; with the lock
    released during fetches the write needs no waiting at all."""
    errors = []

    def competing_write(url):
        api = connect(tmp_path / "test.db")
        try:
            api.execute("PRAGMA busy_timeout = 100")
            api.execute("INSERT OR REPLACE INTO worker_meta(key, value) VALUES ('api', ?)",
                        (url,))
            api.commit()
        except sqlite3.OperationalError as exc:   # "database is locked"
            errors.append(exc)
        finally:
            api.close()

    refresh_baselines(db, _BaselineClient(competing_write), max_spans=1)

    assert errors == [], f"{len(errors)} API write(s) failed: {errors[0]!r}"


def test_daemon_backoff_sleep_releases_write_lock(db, monkeypatch):
    """An unexpected error mid-transaction (here a network timeout partway through
    a baseline span) must not leave the write lock held through the daemon's 30 s
    backoff sleep."""
    monkeypatch.setattr(runner, "_touch_heartbeat", lambda: None)
    # Maintenance not due; no accounts or queue rows -> the idle baseline branch runs.
    db.execute("INSERT INTO worker_meta(key, value) VALUES ('last_maintenance_at', '9999')")
    db.commit()

    calls = []

    def fail_on_third(url):
        calls.append(url)
        if len(calls) == 3:
            raise NetworkError("timed out")

    sleeps = []
    runner.run_daemon(db, _BaselineClient(fail_on_third),
                      sleep=lambda s: sleeps.append((s, db.in_transaction)),
                      max_iterations=1)

    assert sleeps == [(runner.UNEXPECTED_ERROR_SLEEP_S, False)]
