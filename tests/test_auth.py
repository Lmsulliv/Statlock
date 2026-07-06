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
    """Drive the callback with Steam verification mocked; cookies land in the jar.
    The lambda takes **kw because verify_callback now also receives return_to."""
    monkeypatch.setattr(api.auth, "verify_callback", lambda params, **kw: account_id)
    resp = client.get("/api/auth/callback?openid.claimed_id=x", follow_redirects=False)
    assert resp.status_code == 303
    return resp


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies["csrf"]}


# ── verify_callback (OpenID validation, network injected) ─────────────────────
_CLAIMED = "https://steamcommunity.com/openid/id/76561198851497247"
RETURN_TO = "http://stats.example.com/api/auth/callback"


def _signed_params(**overrides) -> dict:
    """A well-formed Steam callback: claimed_id and return_to are both in the
    signed set and return_to points back at us. Overrides mutate one field so a
    test can forge exactly one thing."""
    params = {
        "openid.signed": "signed,claimed_id,return_to",
        "openid.claimed_id": _CLAIMED,
        "openid.return_to": RETURN_TO,
        "openid.sig": "abc",
    }
    params.update(overrides)
    return params


def _boom(data):
    raise AssertionError("verify_callback contacted Steam before validating signed fields")


def test_verify_callback_returns_account_id_when_steam_confirms():
    account_id = api.auth.verify_callback(
        _signed_params(), return_to=RETURN_TO, post=lambda data: "ns:...\nis_valid:true\n")
    assert account_id == 891231519     # SteamID64 normalized to 32-bit


def test_verify_callback_none_when_steam_rejects():
    assert api.auth.verify_callback(
        _signed_params(), return_to=RETURN_TO, post=lambda data: "is_valid:false\n") is None


def test_verify_callback_none_when_claimed_id_missing():
    # claimed_id is in the signed set, but its value doesn't resolve to a SteamID.
    params = _signed_params(**{"openid.claimed_id": "not-a-steam-id"})
    assert api.auth.verify_callback(
        params, return_to=RETURN_TO, post=lambda data: "is_valid:true\n") is None


# ── verify_callback: forged-signature hardening (openid.signed + return_to) ───
def test_verify_callback_rejects_claimed_id_not_in_signed():
    # The attack: strip claimed_id from the signed set so an unsigned, forged
    # claimed_id would be trusted. Must reject WITHOUT even contacting Steam.
    params = _signed_params(**{"openid.signed": "signed,return_to"})
    assert api.auth.verify_callback(params, return_to=RETURN_TO, post=_boom) is None


def test_verify_callback_rejects_return_to_not_in_signed():
    params = _signed_params(**{"openid.signed": "signed,claimed_id"})
    assert api.auth.verify_callback(params, return_to=RETURN_TO, post=_boom) is None


def test_verify_callback_rejects_missing_signed():
    params = _signed_params()
    del params["openid.signed"]
    assert api.auth.verify_callback(params, return_to=RETURN_TO, post=_boom) is None


def test_verify_callback_rejects_foreign_return_to():
    # A signed assertion minted for another relying party, replayed against us.
    params = _signed_params(**{"openid.return_to": "http://evil.example.com/api/auth/callback"})
    assert api.auth.verify_callback(params, return_to=RETURN_TO, post=_boom) is None


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
    monkeypatch.setattr(api.auth, "verify_callback", lambda params, **kw: None)
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
                    "account_id": None, "display_name": None, "demo_account_id": None}


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


def test_me_surfaces_demo_account_id_when_set(api_db, monkeypatch):
    # The demo id is deployment config, so it rides on the /me response for both
    # the logged-out and the local-default-user branches.
    monkeypatch.setenv("DEMO_ACCOUNT_ID", "424242")
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)
    assert _client().get("/api/auth/me").json()["demo_account_id"] == 424242
    monkeypatch.delenv("DEADLOCK_BASE_URL", raising=False)
    assert _client().get("/api/auth/me").json()["demo_account_id"] == 424242


def test_me_demo_account_id_null_when_unset(api_db):
    # Autouse _no_demo_account clears the var; the field is present but null.
    assert _client().get("/api/auth/me").json()["demo_account_id"] is None


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
    # DEADLOCK_OPEN_WRITES=1 (set by the conftest _open_writes fixture), no base
    # URL: the write succeeds with no session and lands on user 1.
    resp = _client().put("/api/accounts/900/name", json={"display_name": "LocalName"})
    assert resp.status_code == 200
    assert api_db.execute(
        "SELECT display_name FROM account_labels WHERE user_id = 1 AND account_id = 900"
    ).fetchone()["display_name"] == "LocalName"


# ── fail closed: neither variable set -> writes 403, reads still 200 ──────────
def test_write_403_when_neither_variable_set(api_db, monkeypatch):
    # Undo the conftest opt-in: no DEADLOCK_BASE_URL and no DEADLOCK_OPEN_WRITES.
    monkeypatch.delenv("DEADLOCK_OPEN_WRITES", raising=False)
    resp = _client().put("/api/accounts/900/name", json={"display_name": "x"})
    assert resp.status_code == 403
    # The message names both escape hatches so an operator knows how to proceed.
    detail = resp.json()["detail"]
    assert "DEADLOCK_BASE_URL" in detail and "DEADLOCK_OPEN_WRITES" in detail


def test_reads_still_open_when_neither_variable_set(api_db, monkeypatch):
    monkeypatch.delenv("DEADLOCK_OPEN_WRITES", raising=False)
    assert _client().get("/api/matchups").status_code == 200
