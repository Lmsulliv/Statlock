"""Regression tests for the concurrency pragmas set by tracker.db.connect().

These prove the deploy gate: the drain daemon and the API can share one SQLite
file. WAL lets readers proceed while a writer holds the lock, and a non-zero
busy_timeout makes a competing writer wait-and-retry instead of immediately
raising "database is locked".

All connections are file-backed (pytest's tmp_path), so WAL actually engages and
the -wal / -shm sidecars are auto-cleaned with the temp dir after each test.
"""
import sqlite3
import threading
import time

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
