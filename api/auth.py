"""Steam OpenID login and server-side sessions.

Steam authenticates with OpenID 2.0 (not OIDC): we redirect the user to Steam,
Steam redirects back with signed openid.* params, and we verify them by echoing
them back with mode=check_authentication. A valid reply hands us the user's
SteamID64, which we normalize to the same 32-bit account id the importer uses --
so a logged-in user is automatically linked to their own Deadlock account.

No login secret is needed: sessions are random opaque tokens stored server-side
(sessions table), and CSRF uses a double-submit token compared by equality. The
only network call here is the verification POST to Steam; it is injectable so
tests never hit the network.
"""
import re
import secrets
import sqlite3
import urllib.parse
import urllib.request
from datetime import timedelta

from ingest.accounts import add_account, to_account_id
from ingest.util import utcnow

STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
_OPENID_NS = "http://specs.openid.net/auth/2.0"
_IDENTIFIER_SELECT = "http://specs.openid.net/auth/2.0/identifier_select"
# Steam returns the identity as .../openid/id/<steamid64>.
_CLAIMED_ID_RE = re.compile(r"steamcommunity\.com/openid/id/(\d+)")

SESSION_TTL = timedelta(days=30)


# ── Steam OpenID flow ────────────────────────────────────────────────────────

def login_redirect_url(return_to: str, realm: str) -> str:
    """The Steam URL to send the user to. return_to is our callback; realm is the
    origin Steam shows the user and scopes the login to."""
    params = {
        "openid.ns": _OPENID_NS,
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": realm,
        "openid.identity": _IDENTIFIER_SELECT,
        "openid.claimed_id": _IDENTIFIER_SELECT,
    }
    return f"{STEAM_OPENID_URL}?{urllib.parse.urlencode(params)}"


def _post_to_steam(data: dict) -> str:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(STEAM_OPENID_URL, data=body)
    with urllib.request.urlopen(req, timeout=10) as resp:   # pragma: no cover - network
        return resp.read().decode()


def verify_callback(params: dict, *, return_to: str, post=_post_to_steam) -> int | None:
    """Validate the OpenID callback params with Steam and return the user's 32-bit
    account id, or None if verification fails. `return_to` is our own callback URL;
    `post` is injectable for tests.

    Two checks guard the fields we trust BEFORE asking Steam to confirm the
    signature:

    - openid.signed lists (comma-separated, without the `openid.` prefix) exactly
      which fields Steam actually signed. We require both `claimed_id` and
      `return_to` to be in it. Without this a forger can strip `claimed_id` from the
      signed set and hand us any SteamID: check_authentication would still return
      is_valid:true (it only vouches for the signed fields), and we'd read an
      unsigned, attacker-controlled claimed_id -- authenticating as anyone.
    - openid.return_to must point back at our own callback, so a signed assertion
      minted for another relying party can't be replayed against us."""
    signed = set(params.get("openid.signed", "").split(","))
    if "claimed_id" not in signed or "return_to" not in signed:
        return None
    if not params.get("openid.return_to", "").startswith(return_to):
        return None
    # Re-send every returned param with mode flipped to check_authentication; Steam
    # replies with a body containing "is_valid:true" only if it really signed them.
    data = {k: v for k, v in params.items() if k.startswith("openid.")}
    data["openid.mode"] = "check_authentication"
    if "is_valid:true" not in post(data):
        return None
    match = _CLAIMED_ID_RE.search(params.get("openid.claimed_id", ""))
    if not match:
        return None
    return to_account_id(match.group(1))   # SteamID64 -> 32-bit account id


# ── Users ────────────────────────────────────────────────────────────────────

def find_or_create_user(conn: sqlite3.Connection, steam_account_id: int,
                        *, now=utcnow) -> int:
    """The user for this Steam account, creating one on first login. A new user's
    Steam account is registered as their self account (tracked + linked is_self),
    so the worker ingests their matches and the resolvers anchor to it."""
    row = conn.execute("SELECT user_id FROM users WHERE steam_account_id = ?",
                       (steam_account_id,)).fetchone()
    if row:
        return row["user_id"]
    cur = conn.execute("INSERT INTO users(steam_account_id, created_at) VALUES (?, ?)",
                       (steam_account_id, now().isoformat()))
    user_id = cur.lastrowid
    add_account(conn, steam_account_id, is_self=True, user_id=user_id, now=now)
    conn.commit()
    return user_id


# ── Sessions ─────────────────────────────────────────────────────────────────

def create_session(conn: sqlite3.Connection, user_id: int, *, now=utcnow) -> str:
    """Open a session and return its token (the cookie value)."""
    token = secrets.token_urlsafe(32)
    issued = now()
    conn.execute(
        "INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, issued.isoformat(), (issued + SESSION_TTL).isoformat()),
    )
    conn.commit()
    return token


def user_for_session(conn: sqlite3.Connection, token: str | None,
                     *, now=utcnow) -> int | None:
    """The user id for a session token, or None if it's missing, unknown, or
    expired. Expired rows are cleaned up on the way out."""
    if not token:
        return None
    row = conn.execute("SELECT user_id, expires_at FROM sessions WHERE token = ?",
                       (token,)).fetchone()
    if row is None:
        return None
    if row["expires_at"] <= now().isoformat():
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        return None
    return row["user_id"]


def delete_session(conn: sqlite3.Connection, token: str | None) -> None:
    """Revoke a session (idempotent)."""
    if token:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()


def delete_expired_sessions(conn: sqlite3.Connection, *, now=utcnow) -> int:
    """Sweep every expired session in one pass and return how many were removed.

    user_for_session only deletes an expired row when that exact token is
    presented, so abandoned sessions (the user never comes back) linger forever.
    The nightly maintenance job calls this to keep the table from growing without
    bound."""
    cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= ?",
                          (now().isoformat(),))
    conn.commit()
    return cursor.rowcount


def new_csrf_token() -> str:
    """A fresh CSRF token for the double-submit cookie."""
    return secrets.token_urlsafe(32)
