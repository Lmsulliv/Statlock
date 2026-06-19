"""The in-app account importer + namer (api.app POST/PATCH /api/accounts).

These exercise the owner-gated write endpoints that back the Accounts screen.
The autouse `_no_network` fixture (conftest) blocks `urlopen`, so a passing POST
is itself proof that ingestion was NOT run in the request -- the endpoint only
records the account (the enqueue) and returns 202; the worker does the fetching.

DEADLOCK_OWNER is unset by default, so the "forbidden" cases need no setup
beyond the seeded `api_db`. `891231519` is the canonical normalized id (the
SteamID64 76561198851497247) and is distinct from the seeded self account (1).
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


# ── POST /api/accounts: owner gate ───────────────────────────────────────────

def test_add_forbidden_without_owner_flag(api_db):
    # The gate runs before any work, so even a well-formed body is 403.
    resp = _client().post("/api/accounts", json={"account_id": ACCOUNT_ID})
    assert resp.status_code == 403
    assert _tracked(api_db, ACCOUNT_ID) is None  # nothing was written


# ── POST /api/accounts: the happy path is "enqueue, don't ingest" ────────────

def test_add_enqueues_and_returns_202_without_ingesting(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
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
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    resp = _client().post("/api/accounts", json={"account_id": STEAMID64})
    assert resp.status_code == 202
    assert resp.json()["account_id"] == ACCOUNT_ID
    assert _tracked(api_db, ACCOUNT_ID) is not None


def test_add_normalizes_profile_url(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    resp = _client().post("/api/accounts", json={"account_id": PROFILE_URL})
    assert resp.status_code == 202
    assert resp.json()["account_id"] == ACCOUNT_ID


# ── POST /api/accounts: invalid identifiers ──────────────────────────────────

def test_add_vanity_url_is_400(api_db, monkeypatch):
    # Vanity URLs can't be resolved offline -> to_account_id raises -> 400.
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    resp = _client().post(
        "/api/accounts",
        json={"account_id": "https://steamcommunity.com/id/somename"},
    )
    assert resp.status_code == 400


def test_add_non_positive_is_400(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    assert _client().post("/api/accounts", json={"account_id": -5}).status_code == 400


def test_add_garbage_is_400(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    resp = _client().post("/api/accounts", json={"account_id": "not an id"})
    assert resp.status_code == 400


def test_add_missing_account_id_is_422(api_db, monkeypatch):
    # Schema validation (missing required field) is Pydantic's job -> 422.
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    assert _client().post("/api/accounts", json={}).status_code == 422


# ── POST /api/accounts: idempotency ──────────────────────────────────────────

def test_add_is_idempotent(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    first = _client().post("/api/accounts", json={"account_id": ACCOUNT_ID})
    second = _client().post("/api/accounts", json={"account_id": ACCOUNT_ID})
    assert first.status_code == 202 and second.status_code == 202
    count = api_db.execute(
        "SELECT COUNT(*) FROM tracked_accounts WHERE account_id=?", (ACCOUNT_ID,)
    ).fetchone()[0]
    assert count == 1


# ── PATCH /api/accounts/{id}: the namer ──────────────────────────────────────

def test_rename_forbidden_without_owner_flag(api_db):
    resp = _client().patch("/api/accounts/1", json={"display_name": "x"})
    assert resp.status_code == 403


def test_rename_sets_display_name(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    # The seeded self account (1) starts with no display_name.
    resp = _client().patch("/api/accounts/1", json={"display_name": "Main"})
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Main"
    assert _tracked(api_db, 1)["display_name"] == "Main"


def test_rename_unknown_account_is_404(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_OWNER", "true")
    resp = _client().patch("/api/accounts/999999", json={"display_name": "x"})
    assert resp.status_code == 404
