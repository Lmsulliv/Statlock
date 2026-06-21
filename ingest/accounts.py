"""Account ID normalization and registration of tracked accounts.

Friends paste IDs in several formats (docs/api-findings.md); everything is
normalized client-side to the 32-bit account ID before it touches the API,
because SteamID64 acceptance by deadlock-api is undocumented behavior.
"""
import re

from ingest.util import utcnow

STEAMID64_OFFSET = 76561197960265728

_PROFILE_URL_RE = re.compile(r"steamcommunity\.com/profiles/(\d+)")
_VANITY_URL_RE = re.compile(r"steamcommunity\.com/id/")


def to_account_id(value: str | int) -> int:
    """Normalize an account ID, SteamID64, or profile URL to a 32-bit account ID."""
    text = str(value).strip().rstrip("/")

    if _VANITY_URL_RE.search(text):
        raise ValueError(
            "Vanity URLs (steamcommunity.com/id/...) can't be resolved offline. "
            "Open the profile and copy the /profiles/<number> URL, or paste the "
            "SteamID64 / friend ID directly."
        )

    match = _PROFILE_URL_RE.search(text)
    if match:
        text = match.group(1)

    if not text.isdigit():
        raise ValueError(f"Not a recognizable account ID, SteamID64, or profile URL: {value!r}")

    number = int(text)
    if number >= STEAMID64_OFFSET:
        return number - STEAMID64_OFFSET
    if number <= 0:
        raise ValueError(f"Account ID must be positive: {value!r}")
    return number


def to_steamid64(account_id: int) -> int:
    """32-bit account id -> SteamID64. Inverse of to_account_id()."""
    return account_id + STEAMID64_OFFSET


def add_account(conn, value: str | int, *, display_name: str | None = None,
                is_self: bool = False, now=utcnow) -> int:
    """Register a tracked account (idempotent) and ensure its sync_state row."""
    account_id = to_account_id(value)
    conn.execute(
        "INSERT OR IGNORE INTO tracked_accounts(account_id, display_name, is_self, added_at)"
        " VALUES (?, ?, ?, ?)",
        (account_id, display_name, int(is_self), now().isoformat()),
    )
    conn.execute(
        "INSERT OR IGNORE INTO sync_state(account_id) VALUES (?)", (account_id,)
    )
    conn.commit()
    return account_id
