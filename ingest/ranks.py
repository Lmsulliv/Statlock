"""Rank ingestion: per-player rank-over-time for tracked accounts.

The Overview "Rank over time" chart reads account_rank_history, but nothing
used to populate it. This module fills it from
GET /v1/players/{account_id}/mmr-history -- the per-match rank series for a
tracked account.

Shape (docs/api-findings.md "Player rank / MMR history", spike 12): a JSON array,
one row per RANKED match, each carrying its own start_time (unix seconds),
match_id, and rank (= division*10 + division_tier, i.e. our tier*10+subtier
badge, 0..116). We store rank as the badge and start_time as recorded_at, so the
read layer can order the series by its own timestamp without joining matches.

Cadence (gated by the runner): we only fetch an account's rank when discovery
just queued new matches for it -- mmr-history only changes when you play, so a
fetch every cycle would be wasted requests. The fetch itself is idempotent:
re-running upserts the same rows, never duplicates (PK is (account_id, match_id)).
"""
import json
import logging

from ingest.client import BASE_URL, archive_response
from ingest.util import unix_to_iso, utcnow

log = logging.getLogger(__name__)

_UPSERT = (
    "INSERT INTO account_rank_history(account_id, match_id, badge, recorded_at)"
    " VALUES (?, ?, ?, ?)"
    " ON CONFLICT(account_id, match_id) DO UPDATE SET"
    "   badge       = excluded.badge,"
    "   recorded_at = excluded.recorded_at"
)


def _mmr_history_url(account_id: int) -> str:
    return f"{BASE_URL}/v1/players/{account_id}/mmr-history"


def fetch_account_rank(conn, client, account_id: int, *, now=utcnow) -> int:
    """Fetch + upsert the rank history for one account. Returns rows upserted.

    Idempotent: the upsert keys on (account_id, match_id), so re-fetching the
    same series writes the same rows in place rather than duplicating them."""
    url = _mmr_history_url(account_id)
    fetched_at = now().isoformat()
    status, _headers, body = client.get(url)
    # Hard rule 2: archive raw before parsing.
    archive_response(conn, url, status, body, fetched_at)
    if status != 200:
        log.warning("ranks: account %s mmr-history HTTP %s", account_id, status)
        return 0
    if not body or not body.strip():
        log.warning("ranks: account %s empty mmr-history body", account_id)
        return 0

    rows = json.loads(body)
    upserts = [
        (account_id, r["match_id"], r["rank"], unix_to_iso(r["start_time"]))
        for r in rows
        if r.get("match_id") is not None and r.get("rank") is not None
    ]
    conn.executemany(_UPSERT, upserts)
    conn.commit()
    log.info("ranks: account %s upserted %d rank row(s)", account_id, len(upserts))
    return len(upserts)


def run_rank_sync(conn, client, account_ids, *, now=utcnow) -> int:
    """Fetch rank history for the given accounts (those discovery just found new
    matches for). Returns total rows upserted across them."""
    return sum(fetch_account_rank(conn, client, account_id, now=now)
               for account_id in account_ids)
