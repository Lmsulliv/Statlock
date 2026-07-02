"""Loop 1: discovery. Find new match IDs for tracked accounts.

Cheap (one match-history call per account) and idempotent: matches above
each account's high-water mark are queued with INSERT OR IGNORE, so a match
two tracked players share is queued exactly once. The high-water mark
(sync_state.last_match_id) makes restarts cheap — we never re-walk a full
history, just everything newer than the mark.
"""
import json
import logging

from ingest.client import BASE_URL, archive_response
from ingest.util import utcnow

log = logging.getLogger(__name__)


def _history_url(account_id: int) -> str:
    return f"{BASE_URL}/v1/players/{account_id}/match-history"


def discover_account(conn, client, account_id: int, *, now=utcnow) -> int:
    """Discover new matches for one account. Returns count of newly queued matches."""
    state = conn.execute(
        "SELECT last_match_id FROM sync_state WHERE account_id = ?", (account_id,)
    ).fetchone()
    high_water = (state["last_match_id"] if state and state["last_match_id"] is not None else 0)

    url = _history_url(account_id)
    fetched_at = now().isoformat()
    status, _headers, body = client.get(url)
    # Hard rule 2: archive raw before parsing.
    archive_response(conn, url, status, body, fetched_at)
    if status != 200:
        log.warning("discovery: account %s history HTTP %s", account_id, status)
        return 0

    history = json.loads(body)
    new_ids = [row["match_id"] for row in history if row["match_id"] > high_water]

    for match_id in new_ids:
        conn.execute(
            "INSERT OR IGNORE INTO fetch_queue(match_id, discovered_at, status)"
            " VALUES (?, ?, 'pending')",
            (match_id, fetched_at),
        )

    max_seen = max((row["match_id"] for row in history), default=high_water)
    conn.execute(
        "UPDATE sync_state SET last_match_id = ?, last_synced_at = ? WHERE account_id = ?",
        (max_seen, fetched_at, account_id),
    )
    conn.commit()
    log.info("discovery: account %s found %d new match(es), high-water %d",
             account_id, len(new_ids), max_seen)
    return len(new_ids)


def discover_all(conn, client, *, now=utcnow) -> dict[int, int]:
    """Run discovery for every tracked account. Returns {account_id: newly queued
    count}. The per-account counts let the runner gate rank ingestion on the
    accounts that actually got new matches this cycle."""
    accounts = [r["account_id"] for r in
                conn.execute("SELECT account_id FROM tracked_accounts").fetchall()]
    return {account_id: discover_account(conn, client, account_id, now=now)
            for account_id in accounts}


def run_discovery(conn, client, *, now=utcnow) -> int:
    """Run discovery for every tracked account. Returns total newly queued."""
    return sum(discover_all(conn, client, now=now).values())
