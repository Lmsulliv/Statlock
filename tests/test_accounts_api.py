"""The in-app account importer (api.app POST /api/accounts).

These exercise the importer that backs the Accounts screen. The rename API
(PUT/DELETE /api/accounts/{id}/name) lives in tests/test_account_names.py. The
autouse `_no_network` fixture (conftest) blocks `urlopen`, so a passing POST is
itself proof that ingestion was NOT run in the request -- the endpoint only
records the account (the enqueue) and returns 202; the worker does the fetching.

In local/dev mode (no DEADLOCK_BASE_URL) the importer is open as the default user,
so the happy-path cases need no auth setup. `891231519` is the canonical
normalized id (the SteamID64 76561198851497247) and is distinct from the seeded
self account (1).
"""
from fastapi.testclient import TestClient

from api.app import app

ACCOUNT_ID = 891231519
STEAMID64 = "76561198851497247"
PROFILE_URL = "https://steamcommunity.com/profiles/76561198851497247"


def _client() -> TestClient:
    return TestClient(app)


def _tracked(conn, account_id):
    return conn.execute(
        "SELECT * FROM tracked_accounts WHERE account_id=?", (account_id,)
    ).fetchone()


# ── POST /api/accounts: auth gate ────────────────────────────────────────────

def test_add_requires_login_in_auth_mode(api_db, monkeypatch):
    # In auth mode the gate runs before any work, so even a well-formed body is 401.
    monkeypatch.setenv("DEADLOCK_BASE_URL", "https://stats.example.com")
    resp = _client().post("/api/accounts", json={"account_id": ACCOUNT_ID})
    assert resp.status_code == 401
    assert _tracked(api_db, ACCOUNT_ID) is None  # nothing was written


# ── POST /api/accounts: the happy path is "enqueue, don't ingest" ────────────

def test_add_enqueues_and_returns_202_without_ingesting(api_db, monkeypatch):
    queue_before = api_db.execute("SELECT COUNT(*) FROM fetch_queue").fetchone()[0]

    resp = _client().post(
        "/api/accounts",
        json={"account_id": ACCOUNT_ID, "display_name": "smurf"},
    )

    # 202 Accepted: recorded, not done. The body is the created account.
    assert resp.status_code == 202
    body = resp.json()
    assert body["account_id"] == ACCOUNT_ID
    assert body["display_name"] == "smurf"
    assert body["is_self"] is False  # the importer never claims the self account

    # The insert IS the enqueue: tracked_accounts + sync_state rows now exist so
    # the worker's discovery loop will pick the account up on its next cycle.
    tracked = _tracked(api_db, ACCOUNT_ID)
    assert tracked is not None
    assert tracked["display_name"] == "smurf"
    assert tracked["is_self"] == 0
    sync = api_db.execute(
        "SELECT * FROM sync_state WHERE account_id=?", (ACCOUNT_ID,)
    ).fetchone()
    assert sync is not None

    # Nothing was ingested in-request: the queue is untouched (and _no_network
    # would have raised if the handler had tried to fetch anything).
    queue_after = api_db.execute("SELECT COUNT(*) FROM fetch_queue").fetchone()[0]
    assert queue_after == queue_before


def test_add_normalizes_steamid64_string(api_db, monkeypatch):
    resp = _client().post("/api/accounts", json={"account_id": STEAMID64})
    assert resp.status_code == 202
    assert resp.json()["account_id"] == ACCOUNT_ID
    assert _tracked(api_db, ACCOUNT_ID) is not None


def test_add_normalizes_profile_url(api_db, monkeypatch):
    resp = _client().post("/api/accounts", json={"account_id": PROFILE_URL})
    assert resp.status_code == 202
    assert resp.json()["account_id"] == ACCOUNT_ID


# ── POST /api/accounts: invalid identifiers ──────────────────────────────────

def test_add_vanity_url_is_400(api_db, monkeypatch):
    # Vanity URLs can't be resolved offline -> to_account_id raises -> 400.
    resp = _client().post(
        "/api/accounts",
        json={"account_id": "https://steamcommunity.com/id/somename"},
    )
    assert resp.status_code == 400


def test_add_non_positive_is_400(api_db, monkeypatch):
    assert _client().post("/api/accounts", json={"account_id": -5}).status_code == 400


def test_add_garbage_is_400(api_db, monkeypatch):
    resp = _client().post("/api/accounts", json={"account_id": "not an id"})
    assert resp.status_code == 400


def test_add_missing_account_id_is_422(api_db, monkeypatch):
    # Schema validation (missing required field) is Pydantic's job -> 422.
    assert _client().post("/api/accounts", json={}).status_code == 422


# ── POST /api/accounts: idempotency ──────────────────────────────────────────

def test_add_is_idempotent(api_db, monkeypatch):
    first = _client().post("/api/accounts", json={"account_id": ACCOUNT_ID})
    second = _client().post("/api/accounts", json={"account_id": ACCOUNT_ID})
    assert first.status_code == 202 and second.status_code == 202
    count = api_db.execute(
        "SELECT COUNT(*) FROM tracked_accounts WHERE account_id=?", (ACCOUNT_ID,)
    ).fetchone()[0]
    assert count == 1


def test_add_with_name_writes_a_label(api_db, monkeypatch):
    # account_labels is the single source of manual names, so add-with-name also
    # lands a label (otherwise the resolver-backed views wouldn't show it).
    _client().post("/api/accounts", json={"account_id": ACCOUNT_ID, "display_name": "smurf"})
    label = api_db.execute(
        "SELECT display_name FROM account_labels WHERE user_id=1 AND account_id=?",
        (ACCOUNT_ID,),
    ).fetchone()
    assert label["display_name"] == "smurf"
