"""Interim owner gating for the era-management writes (api.app.require_owner).

This is NOT a test of authentication -- there is none yet. It checks the stopgap
config gate: confirm/dismiss return 403 unless the DEADLOCK_OWNER flag is set,
and the read path (GET /api/eras) stays open regardless. The flag is unset by
default, so the "forbidden" cases need no setup beyond the seeded `api_db`.
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


def test_confirm_forbidden_without_owner_flag(api_db):
    # No DEADLOCK_OWNER set: the gate runs before any candidate lookup, so even a
    # nonexistent id is 403 (not 404).
    resp = TestClient(app).post("/api/eras/candidates/1/confirm")
    assert resp.status_code == 403


def test_dismiss_forbidden_without_owner_flag(api_db):
    resp = TestClient(app).post("/api/eras/candidates/1/dismiss")
    assert resp.status_code == 403


def test_reading_eras_stays_open_without_owner_flag(api_db):
    # The GET is read-only and never gated; only the writes are.
    assert TestClient(app).get("/api/eras").status_code == 200


def test_confirm_allowed_with_owner_flag(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    candidate_id = _insert_pending_candidate(api_db)

    resp = TestClient(app).post(f"/api/eras/candidates/{candidate_id}/confirm")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_dismiss_allowed_with_owner_flag(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    candidate_id = _insert_pending_candidate(api_db)

    resp = TestClient(app).post(f"/api/eras/candidates/{candidate_id}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_unknown_candidate_with_flag_is_404_not_403(api_db, monkeypatch):
    # With the gate open, an unknown id falls through to the normal 404 -- proof
    # the 403 above is the gate, not a missing-candidate error.
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    resp = TestClient(app).post("/api/eras/candidates/999999/confirm")
    assert resp.status_code == 404
