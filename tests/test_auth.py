"""Steam-login authentication (api.auth + api.app auth routes).

Auth turns on when DEADLOCK_BASE_URL is set. Steam's verification is mocked
(monkeypatching api.auth.verify_callback) so no test hits the network -- conftest's
_no_network would fail it anyway. The base URL is http:// (not https) so the login
cookies aren't Secure-only and the TestClient, which talks http://testserver, sends
them back on later requests.

A logged-in TestClient keeps its cookies in its jar, so after the callback the
session + csrf cookies ride along automatically; writes echo the csrf cookie in the
X-CSRF-Token header (double-submit).
"""
import urllib.parse

import api.auth
from fastapi.testclient import TestClient

from api.app import app

BASE = "http://stats.example.com"
STEAM_ACCOUNT_ID = 555            # the account_id verify_callback resolves the login to


def _client(follow_redirects=True):
    return TestClient(app, follow_redirects=follow_redirects)


def _login(client, monkeypatch, account_id=STEAM_ACCOUNT_ID):
    """Drive the callback with Steam verification mocked; cookies land in the jar."""
    monkeypatch.setattr(api.auth, "verify_callback", lambda params: account_id)
    resp = client.get("/api/auth/callback?openid.claimed_id=x", follow_redirects=False)
    assert resp.status_code == 303
    return resp


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies["csrf"]}


# ── verify_callback (OpenID validation, network injected) ─────────────────────
_CLAIMED = "https://steamcommunity.com/openid/id/76561198851497247"


def test_verify_callback_returns_account_id_when_steam_confirms():
    params = {"openid.claimed_id": _CLAIMED, "openid.sig": "abc"}
    account_id = api.auth.verify_callback(params, post=lambda data: "ns:...\nis_valid:true\n")
    assert account_id == 891231519     # SteamID64 normalized to 32-bit


def test_verify_callback_none_when_steam_rejects():
    params = {"openid.claimed_id": _CLAIMED}
    assert api.auth.verify_callback(params, post=lambda data: "is_valid:false\n") is None


def test_verify_callback_none_when_claimed_id_missing():
    assert api.auth.verify_callback({}, post=lambda data: "is_valid:true\n") is None


# ── login redirect ────────────────────────────────────────────────────────────
def test_login_redirects_to_steam(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)
    resp = _client().get("/api/auth/login", follow_redirects=False)
    assert resp.status_code in (302, 307)
    loc = resp.headers["location"]
    assert loc.startswith("https://steamcommunity.com/openid/login")
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(loc).query))
    assert params["openid.return_to"] == f"{BASE}/api/auth/callback"
    assert params["openid.realm"] == f"{BASE}/"


def test_login_is_404_in_local_mode(api_db):
    # No DEADLOCK_BASE_URL -> auth is off -> the route doesn't exist.
    assert _client().get("/api/auth/login").status_code == 404


# ── callback creates the user, session, and cookies ───────────────────────────
def test_callback_creates_user_session_and_cookies(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)
    client = _client()
    resp = _login(client, monkeypatch)

    assert resp.headers["location"] == f"{BASE}/"
    assert "session" in client.cookies and "csrf" in client.cookies

    # A user keyed by the Steam account exists, with its self-account link.
    user = api_db.execute(
        "SELECT user_id FROM users WHERE steam_account_id = ?", (STEAM_ACCOUNT_ID,)
    ).fetchone()
    assert user is not None
    link = api_db.execute(
        "SELECT is_self FROM user_accounts WHERE user_id = ? AND account_id = ?",
        (user["user_id"], STEAM_ACCOUNT_ID),
    ).fetchone()
    assert link["is_self"] == 1
    sessions = api_db.execute(
        "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user["user_id"],)
    ).fetchone()[0]
    assert sessions == 1


def test_callback_rejects_unverified_login(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)
    monkeypatch.setattr(api.auth, "verify_callback", lambda params: None)
    resp = _client().get("/api/auth/callback?openid.claimed_id=x", follow_redirects=False)
    assert resp.status_code == 400


def test_repeat_login_reuses_user(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)
    _login(_client(), monkeypatch)
    _login(_client(), monkeypatch)
    count = api_db.execute(
        "SELECT COUNT(*) FROM users WHERE steam_account_id = ?", (STEAM_ACCOUNT_ID,)
    ).fetchone()[0]
    assert count == 1


# ── /api/auth/me ──────────────────────────────────────────────────────────────
def test_me_anonymous_when_logged_out(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)
    body = _client().get("/api/auth/me").json()
    assert body == {"auth_enabled": True, "authenticated": False, "user_id": None,
                    "account_id": None, "display_name": None}


def test_me_after_login(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)
    client = _client()
    _login(client, monkeypatch)
    body = client.get("/api/auth/me").json()
    assert body["authenticated"] is True
    assert body["account_id"] == STEAM_ACCOUNT_ID
    assert body["user_id"] is not None


def test_me_reports_local_mode(api_db):
    # No DEADLOCK_BASE_URL: auth is off; /me reports the default user, not logged in.
    body = _client().get("/api/auth/me").json()
    assert body["auth_enabled"] is False
    assert body["authenticated"] is False
    assert body["user_id"] == 1


# ── write gating: session + CSRF ──────────────────────────────────────────────
def test_write_rejected_without_session(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)
    resp = _client().put("/api/accounts/900/name", json={"display_name": "x"})
    assert resp.status_code == 401


def test_write_rejected_without_csrf_header(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)
    client = _client()
    _login(client, monkeypatch)
    # Session cookie present but no X-CSRF-Token header -> 403.
    resp = client.put("/api/accounts/900/name", json={"display_name": "x"})
    assert resp.status_code == 403


def test_write_succeeds_with_session_and_csrf(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)
    client = _client()
    _login(client, monkeypatch)
    resp = client.put("/api/accounts/900/name",
                      json={"display_name": "Rival"}, headers=_csrf(client))
    assert resp.status_code == 200
    # The label is private to the logged-in user (user 2, not the default user 1).
    user_id = api_db.execute(
        "SELECT user_id FROM users WHERE steam_account_id = ?", (STEAM_ACCOUNT_ID,)
    ).fetchone()["user_id"]
    label = api_db.execute(
        "SELECT display_name FROM account_labels WHERE user_id = ? AND account_id = 900",
        (user_id,),
    ).fetchone()
    assert label["display_name"] == "Rival"
    # The default user did NOT get the label.
    assert api_db.execute(
        "SELECT COUNT(*) FROM account_labels WHERE user_id = 1 AND account_id = 900"
    ).fetchone()[0] == 0


# ── logout revokes the session ────────────────────────────────────────────────
def test_logout_revokes_session(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)
    client = _client()
    _login(client, monkeypatch)
    resp = client.post("/api/auth/logout", headers=_csrf(client))
    assert resp.status_code == 204
    assert api_db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


# ── local/dev mode: no login needed, runs as the default user ─────────────────
def test_local_mode_write_open_as_default_user(api_db):
    # No DEADLOCK_BASE_URL: the write succeeds with no session and lands on user 1.
    resp = _client().put("/api/accounts/900/name", json={"display_name": "LocalName"})
    assert resp.status_code == 200
    assert api_db.execute(
        "SELECT display_name FROM account_labels WHERE user_id = 1 AND account_id = 900"
    ).fetchone()["display_name"] == "LocalName"
