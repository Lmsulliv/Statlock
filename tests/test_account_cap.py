"""Per-user account cap (api.service.MAX_ACCOUNTS_PER_USER).

A user may link at most MAX_ACCOUNTS_PER_USER accounts; the self account from
first login is one of them. The (MAX+1)th distinct account is a 409, but
re-adding an account the user already has stays idempotent (never 409).
"""
import api.auth
from fastapi.testclient import TestClient

from api.app import app
from api.service import MAX_ACCOUNTS_PER_USER

BASE = "http://stats.example.com"      # http:// so login cookies aren't Secure-only
STEAM_ACCOUNT_ID = 555


def _add(client, account_id, **kw):
    return client.post("/api/accounts", json={"account_id": account_id}, **kw)


# ── local/dev (open writes): the cap applies to the default user ──────────────
def test_cap_reached_returns_409(api_db):
    # api_db seeds user 1 with one self account (ME=1), so that link counts. Fill
    # up to the cap (self + MAX-1 more), then the next distinct account overflows.
    client = TestClient(app)
    for i in range(MAX_ACCOUNTS_PER_USER - 1):
        assert _add(client, 8_000_001 + i).status_code == 202
    resp = _add(client, 8_009_999)
    assert resp.status_code == 409
    assert str(MAX_ACCOUNTS_PER_USER) in resp.json()["detail"]


def test_readd_at_cap_is_idempotent_not_409(api_db):
    client = TestClient(app)
    for i in range(MAX_ACCOUNTS_PER_USER - 1):
        assert _add(client, 8_000_001 + i).status_code == 202
    # Re-adding accounts already linked (one just-added, plus the seeded self) is
    # a no-op that must NOT be rejected even though the user is at the cap.
    assert _add(client, 8_000_001).status_code == 202
    assert _add(client, 1).status_code == 202


# ── auth mode: the self account from login counts toward the cap ──────────────
def _login(client, monkeypatch):
    monkeypatch.setattr(api.auth, "verify_callback", lambda params, **kw: STEAM_ACCOUNT_ID)
    resp = client.get("/api/auth/callback?openid.claimed_id=x", follow_redirects=False)
    assert resp.status_code == 303


def test_self_account_counts_toward_cap_in_auth_mode(api_db, monkeypatch):
    monkeypatch.setenv("DEADLOCK_BASE_URL", BASE)   # auth on -> open_writes ignored
    client = TestClient(app)
    _login(client, monkeypatch)                     # user keyed by 555, self = 1 link
    hdr = {"X-CSRF-Token": client.cookies["csrf"]}  # writes are CSRF-protected here
    for i in range(MAX_ACCOUNTS_PER_USER - 1):      # self + MAX-1 more = the cap
        assert _add(client, 7_000_001 + i, headers=hdr).status_code == 202
    assert _add(client, 7_009_999, headers=hdr).status_code == 409
