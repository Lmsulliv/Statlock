"""Loop 1: discovery. Find new match IDs for tracked accounts.

Cheap (one match-history call per account) and idempotent: matches above
each account's high-water mark are queued with INSERT OR IGNORE, so a match
two tracked players share is queued exactly once. The high-water mark
(sync_state.last_match_id) makes restarts cheap — we never re-walk a full
history, just everything newer than the mark.

Queued rows carry a priority and an owner (schema v18): a first import's
newest PRIORITY_IMPORT_COUNT matches are priority-1 'pending', the older
remainder is 'backfill'; once an account is synced, its few new matches per
cycle are all priority-1. The drain loop uses the owner for per-account
round-robin fairness so one bulk import can't starve everyone else.
"""
import json
import logging
from datetime import timedelta

from ingest.client import BASE_URL, archive_response
from ingest.util import unix_to_iso, utcnow

log = logging.getLogger(__name__)

# Activity-aware discovery cadence (schema v20). An account is scheduled after
# each visit by how recently it was actually played, not by a flat timer:
#   - active  (new matches this pass, or last known match within ACTIVE_WINDOW)
#             -> every 30 min, the old flat rate (steady-state responsiveness).
#   - idle    (last match within IDLE_WINDOW)        -> every 6 h.
#   - dormant (idle beyond IDLE_WINDOW, or never any) -> every 24 h.
# This keeps total discovery calls per cycle proportional to ACTIVE accounts, so
# the registry can grow without discovery swallowing the whole request budget
# (see db/migrations/020_discovery_cadence.sql for the budget math).
ACTIVE_WINDOW_S = 48 * 3600
IDLE_WINDOW_S = 14 * 24 * 3600
INTERVAL_ACTIVE_S = 30 * 60
INTERVAL_IDLE_S = 6 * 3600
INTERVAL_DORMANT_S = 24 * 3600

# First-import split (schema v18): the newest matches of a fresh import queue as
# priority-1 'pending' (drained first, newest first), everything older queues as
# 'backfill' (drained only when nothing fresh is eligible). 50 is roughly one
# rate-limited drain-hour of the matches a new user actually looks at; the rest
# trickles in behind every account's fresh work instead of ahead of it.
PRIORITY_IMPORT_COUNT = 50
FRESH_PRIORITY = 1

# Materialize the match-history payload so a freshly imported account has useful
# data within seconds, long before the rate-limited drain loop fetches full match
# metadata. Idempotent: the upsert keys on (account_id, match_id), so re-running
# discovery rewrites the same rows in place rather than duplicating them (mirrors
# the mmr-history upsert in ingest/ranks.py). See migration 017 for the rationale.
_SUMMARY_UPSERT = (
    "INSERT INTO account_match_summaries("
    "  account_id, match_id, hero_id, start_time, game_mode, won,"
    "  kills, deaths, assists, net_worth, last_hits, denies, duration_s, fetched_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT(account_id, match_id) DO UPDATE SET"
    "   hero_id     = excluded.hero_id,"
    "   start_time  = excluded.start_time,"
    "   game_mode   = excluded.game_mode,"
    "   won         = excluded.won,"
    "   kills       = excluded.kills,"
    "   deaths      = excluded.deaths,"
    "   assists     = excluded.assists,"
    "   net_worth   = excluded.net_worth,"
    "   last_hits   = excluded.last_hits,"
    "   denies      = excluded.denies,"
    "   duration_s  = excluded.duration_s,"
    "   fetched_at  = excluded.fetched_at"
)


def _history_url(account_id: int) -> str:
    return f"{BASE_URL}/v1/players/{account_id}/match-history"


def _interval_for_last_match(last_iso: str | None, now_dt) -> int:
    """The cadence bucket for an account with no NEW matches this pass, keyed by
    how recently it was last PLAYED (last known match). ISO timestamps are UTC
    (`+00:00`) throughout, so lexicographic compare == chronological compare --
    the same trick maintenance_due uses. No known match at all -> dormant."""
    if last_iso is None:
        return INTERVAL_DORMANT_S
    if last_iso >= (now_dt - timedelta(seconds=ACTIVE_WINDOW_S)).isoformat():
        return INTERVAL_ACTIVE_S
    if last_iso >= (now_dt - timedelta(seconds=IDLE_WINDOW_S)).isoformat():
        return INTERVAL_IDLE_S
    return INTERVAL_DORMANT_S


def _next_discovery_at(conn, account_id: int, new_count: int, now_dt) -> str:
    """When this account is next due for discovery, as an ISO timestamp. New
    matches this pass mean it's active -> the 30-min cadence; otherwise the bucket
    comes from the last known match's age. The last-match read runs AFTER the
    summary upsert, so it reflects the history just fetched (and an occasional
    empty/glitchy response leaves the prior schedule's inputs intact)."""
    if new_count > 0:
        interval = INTERVAL_ACTIVE_S
    else:
        row = conn.execute(
            "SELECT MAX(start_time) AS t FROM account_match_summaries WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        interval = _interval_for_last_match(row["t"] if row else None, now_dt)
    return (now_dt + timedelta(seconds=interval)).isoformat()


def _summary_row(account_id: int, row: dict, fetched_at: str):
    """Turn one history entry into an _SUMMARY_UPSERT params tuple, or None to skip.

    Every field is read with .get(): a missing stat stays NULL, never 0 -- a NULL
    admits "we don't know", a 0 claims "measured as zero", and averages must not be
    polluted by fabricated zeros. A row is skipped (returns None) only when it can't
    key or order the table:
      - no match_id: it can't be the primary key (same rule the queue loop uses).
      - no / non-numeric start_time: the column is NOT NULL and it's the index's
        ordering key; a fabricated timestamp would corrupt (account_id, start_time).
    Observed histories always carry both, so these guards are pure malformed-row
    defense -- one bad entry must not crash discovery for the whole account.
    """
    match_id = row.get("match_id")
    if match_id is None:
        return None

    start_time = row.get("start_time")
    try:
        start_iso = unix_to_iso(start_time)
    except (TypeError, ValueError, OverflowError, OSError):
        log.warning("discovery: account %s match %s bad start_time %r; skipping summary",
                    account_id, match_id, start_time)
        return None

    game_mode = row.get("game_mode")
    game_mode = str(game_mode) if game_mode is not None else None

    # won = (player_team == match_result) per api-findings: match_result is the
    # WINNING team's number, not a won-flag. Only decidable when both are present.
    player_team = row.get("player_team")
    match_result = row.get("match_result")
    won = int(player_team == match_result) if (
        player_team is not None and match_result is not None) else None

    return (
        account_id, match_id, row.get("hero_id"), start_iso, game_mode, won,
        row.get("player_kills"), row.get("player_deaths"), row.get("player_assists"),
        row.get("net_worth"), row.get("last_hits"), row.get("denies"),
        row.get("match_duration_s"), fetched_at,
    )


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
        "SELECT last_match_id, last_synced_at FROM sync_state WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    high_water = (state["last_match_id"] if state and state["last_match_id"] is not None else 0)
    # A first import (never synced) gets the priority split below; once synced,
    # the few new matches per cycle all queue as fresh priority work.
    first_import = state is None or state["last_synced_at"] is None

    url = _history_url(account_id)
    now_dt = now()
    fetched_at = now_dt.isoformat()
    status, _headers, body = client.get(url)
    # Hard rule 2: archive raw before parsing.
    archive_response(conn, url, status, body, fetched_at)
    if status != 200:
        log.warning("discovery: account %s history HTTP %s", account_id, status)
        # Reschedule on the active cadence even on failure: otherwise a
        # persistently-erroring account stays "due now" and gets retried every
        # daemon iteration instead of every 30 min (its budget share is bounded).
        conn.execute(
            "UPDATE sync_state SET next_discovery_at = ? WHERE account_id = ?",
            ((now_dt + timedelta(seconds=INTERVAL_ACTIVE_S)).isoformat(), account_id),
        )
        conn.commit()
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

    # Newest first: on a first import only the newest PRIORITY_IMPORT_COUNT
    # matches are fresh priority work; the older remainder is 'backfill'. The
    # insert stays INSERT OR IGNORE (idempotency), which also makes the FIRST
    # discoverer win: a match another account already queued keeps its original
    # owner and priority.
    for rank, match_id in enumerate(sorted(new_ids, reverse=True)):
        if first_import and rank >= PRIORITY_IMPORT_COUNT:
            status, priority = "backfill", 0
        else:
            status, priority = "pending", FRESH_PRIORITY
        conn.execute(
            "INSERT OR IGNORE INTO fetch_queue"
            " (match_id, discovered_at, status, priority, discovered_for_account)"
            " VALUES (?, ?, ?, ?, ?)",
            (match_id, fetched_at, status, priority, account_id),
        )

    # Materialize a summary for EVERY history entry, not just new_ids: the payload
    # is idempotent and only a few hundred rows, and re-upserting old rows refreshes
    # any field the API corrected. Rows that can't key/order the table are skipped.
    summary_rows = [params for row in history
                    if (params := _summary_row(account_id, row, fetched_at)) is not None]
    conn.executemany(_SUMMARY_UPSERT, summary_rows)

    max_seen = max(match_ids, default=high_water)
    # Schedule the next visit by activity (reads the summaries just upserted).
    next_at = _next_discovery_at(conn, account_id, len(new_ids), now_dt)
    conn.execute(
        "UPDATE sync_state SET last_match_id = ?, last_synced_at = ?,"
        " next_discovery_at = ? WHERE account_id = ?",
        (max_seen, fetched_at, next_at, account_id),
    )
    conn.commit()
    log.info("discovery: account %s found %d new match(es), high-water %d, next %s",
             account_id, len(new_ids), max_seen, next_at)
    return len(new_ids)


def discover_all(conn, client, *, now=utcnow) -> dict[int, int]:
    """Run discovery for every tracked account. Returns {account_id: newly queued
    count}. The per-account counts let the runner gate rank ingestion on the
    accounts that actually got new matches this cycle.

    Visits everyone regardless of schedule -- this is the manual run-once "catch
    up when you play" shape. It still stamps next_discovery_at as a side effect,
    so a manual run and the daemon share one schedule."""
    accounts = [r["account_id"] for r in
                conn.execute("SELECT account_id FROM tracked_accounts").fetchall()]
    return {account_id: discover_account(conn, client, account_id, now=now)
            for account_id in accounts}


def _due_accounts(conn, now_iso: str) -> list[int]:
    """Synced accounts whose discovery is due: schedule lapsed, never scheduled
    (NULL -> "due now", so legacy/first-scheduled accounts are picked up once), or
    explicitly requested by the web process. `last_synced_at IS NOT NULL` keeps
    never-synced accounts off this path -- they belong to the runner's immediate
    first-import pass, unchanged."""
    rows = conn.execute(
        "SELECT account_id FROM sync_state"
        " WHERE last_synced_at IS NOT NULL"
        "   AND (next_discovery_at IS NULL OR next_discovery_at <= ?"
        "        OR account_id IN (SELECT account_id FROM discovery_requests))",
        (now_iso,),
    ).fetchall()
    return [r["account_id"] for r in rows]


def discover_due(conn, client, *, now=utcnow) -> dict[int, int]:
    """Run discovery only for accounts due by their next_discovery_at (or flagged
    in discovery_requests). The daemon's per-iteration discovery pass -- cheap
    (one indexed SELECT over the tiny sync_state/discovery_requests tables) and
    self-pacing: each visited account re-stamps its own next_discovery_at, so the
    total calls per cycle track ACTIVE accounts, not the whole registry.

    A request row is deleted after the visit regardless of outcome: a failed
    (non-200) visit is already rescheduled by discover_account, so leaving the
    request would just re-run it every iteration."""
    due = _due_accounts(conn, now().isoformat())
    result: dict[int, int] = {}
    for account_id in due:
        result[account_id] = discover_account(conn, client, account_id, now=now)
        conn.execute("DELETE FROM discovery_requests WHERE account_id = ?", (account_id,))
        conn.commit()
    return result


def run_discovery(conn, client, *, now=utcnow) -> int:
    """Run discovery for every tracked account. Returns total newly queued."""
    return sum(discover_all(conn, client, now=now).values())
