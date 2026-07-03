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


def _parse_history(body: str, account_id: int) -> list:
    """Parse a match-history 200 body into a list of rows, tolerating a body that
    carries no usable JSON array (empty/blank/unparseable/non-list). Same defensive
    shape as maintenance._parse_baseline_rows: warn + return [] so one bad response
    can't crash discovery for every tracked account."""
    if not body or not body.strip():
        log.warning("discovery: account %s empty history body", account_id)
        return []
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        log.warning("discovery: account %s malformed history body (%s)", account_id, exc)
        return []
    if not isinstance(parsed, list):
        log.warning("discovery: account %s history body was %s, not a list",
                    account_id, type(parsed).__name__)
        return []
    return parsed


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

    # HTTP 200 doesn't guarantee a JSON array: the endpoint sometimes answers 200
    # with an empty, truncated, or error-shaped body, and json.loads("") raises.
    # An unhandled raise here would crash the discovery loop, so treat any
    # non-list body as "no history" (warn + skip) rather than letting it propagate.
    history = _parse_history(body, account_id)

    # Read match_id with .get() and skip rows that lack it: a sparse row must not
    # crash the loop or poison the high-water mark with a None.
    match_ids = [mid for row in history
                 if (mid := row.get("match_id")) is not None]
    new_ids = [mid for mid in match_ids if mid > high_water]

    for match_id in new_ids:
        conn.execute(
            "INSERT OR IGNORE INTO fetch_queue(match_id, discovered_at, status)"
            " VALUES (?, ?, 'pending')",
            (match_id, fetched_at),
        )

    max_seen = max(match_ids, default=high_water)
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
