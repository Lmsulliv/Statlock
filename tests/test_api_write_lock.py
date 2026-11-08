"""The web process's discovery-mailbox write under SQLite write-lock contention.

GET /api/overview and GET /api/accounts/{id}/progress both call
service._maybe_request_discovery, which INSERTs a discovery_requests row when the
account's last sync is stale. That INSERT needs SQLite's single write lock, so it
is the one statement on these otherwise read-only endpoints that can fail with
"database is locked" while another connection (the worker, a script) holds the
lock longer than the busy_timeout.

All connections are file-backed (tmp_path) so WAL and real locking engage.
"""
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

import api.app as app_module
from api.app import app
from tests.conftest import ME
from tracker.db import connect

STALE_SYNC = "2026-01-01T00:00:00+00:00"   # far older than STALE_DISCOVERY_S
SHORT_BUSY_TIMEOUT_MS = 200


@pytest.fixture
def stale_account(api_db):
    """ME has been synced, but long ago: every overview/progress hit files a request."""
    api_db.execute("INSERT OR REPLACE INTO sync_state(account_id, last_synced_at)"
                   " VALUES (?, ?)", (ME, STALE_SYNC))
    api_db.commit()
    return api_db


@pytest.fixture
def short_busy_timeout(monkeypatch):
    """API connections wait 200 ms for the lock instead of 5 s, so the lock-held
    cases finish fast. Patched where app.get_conn looks connect() up."""
    def fast_connect(path, **kwargs):
        conn = connect(path, **kwargs)
        conn.execute(f"PRAGMA busy_timeout = {SHORT_BUSY_TIMEOUT_MS}")
        return conn
    monkeypatch.setattr(app_module, "connect", fast_connect)


def _hold_write_lock(path) -> sqlite3.Connection:
    """A second connection (standing in for the worker) that takes the write lock
    and keeps it until the caller rolls back."""
    holder = connect(path, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO worker_meta(key, value) VALUES ('holder', '1')")
    return holder


def _request_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM discovery_requests").fetchone()[0]


def test_overview_then_progress_back_to_back(stale_account):
    """The reported sequence: Overview succeeds, then progress for the same account.
    The second INSERT OR IGNORE hits the existing row and must still be a 200."""
    client = TestClient(app)
    assert client.get(f"/api/overview?account_id={ME}").status_code == 200
    assert client.get(f"/api/accounts/{ME}/progress").status_code == 200
    assert _request_count(stale_account) == 1


def test_concurrent_overview_and_progress(stale_account):
    """Overview and progress fired together (as the onboarding UI does): the API's
    own connections contend only for an instant and busy_timeout absorbs it."""
    statuses = []

    def hit(path):
        statuses.append(TestClient(app).get(path).status_code)

    paths = [f"/api/overview?account_id={ME}", f"/api/accounts/{ME}/progress"] * 4
    threads = [threading.Thread(target=hit, args=(p,)) for p in paths]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert statuses == [200] * len(paths)
    assert _request_count(stale_account) == 1


def test_discovery_write_waits_out_a_brief_lock(stale_account, tmp_path):
    """Another connection holds the write lock for less than busy_timeout: the
    request waits and succeeds. An immediate failure here would mean the API
    connection was already inside a read transaction (SQLite skips the busy
    handler when upgrading a read transaction to a write)."""
    hold_s = 0.5
    holder = _hold_write_lock(tmp_path / "api.db")
    threading.Timer(hold_s, holder.rollback).start()

    start = time.monotonic()
    resp = TestClient(app).get(f"/api/accounts/{ME}/progress")
    elapsed = time.monotonic() - start
    holder.close()

    assert resp.status_code == 200
    assert elapsed >= hold_s - 0.1, "write did not wait for the lock"
    assert _request_count(stale_account) == 1


@pytest.mark.parametrize("path", [f"/api/overview?account_id={ME}",
                                  f"/api/accounts/{ME}/progress"])
def test_discovery_write_skipped_while_lock_held_past_timeout(
        stale_account, short_busy_timeout, tmp_path, path):
    """The reported 500: another connection holds the write lock past the API's
    busy_timeout. The discovery request is best-effort, so the endpoint must still
    answer 200 (without filing the request) rather than fail the whole page."""
    holder = _hold_write_lock(tmp_path / "api.db")
    try:
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/api/heroes").status_code == 200   # reads unaffected
        resp = client.get(path)
    finally:
        holder.rollback()
        holder.close()

    assert resp.status_code == 200
    assert _request_count(stale_account) == 0   # skipped, not queued
