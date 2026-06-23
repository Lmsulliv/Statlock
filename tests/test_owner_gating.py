"""Write-gating for era management (api.app.require_user).

Era confirm/dismiss are state-changing writes, so they go through require_user.
In local/dev mode (no DEADLOCK_BASE_URL) there is no login and writes run as the
default user -- so they're open, like every other write. In auth mode they need a
valid session. The read path (GET /api/eras) is never gated.
"""
from fastapi.testclient import TestClient

from api.app import app


def _insert_pending_candidate(conn) -> int:
    cur = conn.execute(
        "INSERT INTO era_candidates"
        " (post_url, post_title, posted_at, change_lines, score, status)"
        " VALUES ('http://x/1', 'Urn rework', '2026-06-04T00:00:00+00:00',"
        "         14, 14, 'pending')"
    )
    conn.commit()
    return cur.lastrowid


# ── local/dev mode (auth off): writes open as the default user ────────────────
def test_confirm_open_in_local_mode(api_db):
    candidate_id = _insert_pending_candidate(api_db)
    resp = TestClient(app).post(f"/api/eras/candidates/{candidate_id}/confirm")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_dismiss_open_in_local_mode(api_db):
    candidate_id = _insert_pending_candidate(api_db)
    resp = TestClient(app).post(f"/api/eras/candidates/{candidate_id}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_unknown_candidate_is_404_in_local_mode(api_db):
    # No gate in local mode, so an unknown id falls through to the normal 404.
    resp = TestClient(app).post("/api/eras/candidates/999999/confirm")
    assert resp.status_code == 404


def test_reading_eras_stays_open(api_db):
    assert TestClient(app).get("/api/eras").status_code == 200


# ── auth mode: a login is required ────────────────────────────────────────────
def test_confirm_requires_login_in_auth_mode(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", "https://stats.example.com")
    # No session cookie -> the gate rejects before any candidate lookup.
    resp = TestClient(app).post("/api/eras/candidates/1/confirm")
    assert resp.status_code == 401
