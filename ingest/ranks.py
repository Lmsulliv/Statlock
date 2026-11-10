"""Rank ingestion: per-match rank-over-time for tracked accounts.

The Overview "Rank over time" chart reads account_rank_history. Since Nov 2026
the rows come from the match-history response discovery already fetches: every
Ranked game (match_mode 4) carries ranked_display_badge, our tier*10+subtier
badge (docs/api-findings.md "Ranked seasons and rank systems", spike 15). It
matched the old mmr-history rank on 16/16 shared matches, and mmr-history itself
now 404s -- so this costs no extra request.

Which rows count:
- calibration games carry badge 0 (no rank yet) -> skipped;
- the placement game is the first real badge -> kept (mmr-history had it too);
- Standard games carry a null badge -> skipped.

Rows written before the switch (the legacy ladder, from mmr-history) can't be
re-derived: their match-history rows have no badge. The upsert only ever touches
the match_ids it was given, so those rows are never overwritten or deleted.
"""
import logging

from ingest.util import unix_to_iso

log = logging.getLogger(__name__)

RANKED_MATCH_MODE = 4

_UPSERT = (
    "INSERT INTO account_rank_history(account_id, match_id, badge, recorded_at)"
    " VALUES (?, ?, ?, ?)"
    " ON CONFLICT(account_id, match_id) DO UPDATE SET"
    "   badge       = excluded.badge,"
    "   recorded_at = excluded.recorded_at"
)


def _rank_row(account_id: int, row: dict):
    """One history entry -> an _UPSERT params tuple, or None if it carries no rank.

    `type(...) is int` rather than isinstance: a JSON true would pass isinstance
    (bool is an int subclass) and must not become badge 1."""
    if row.get("match_mode") != RANKED_MATCH_MODE:
        return None
    badge = row.get("ranked_display_badge")
    if type(badge) is not int or badge <= 0:
        return None
    match_id = row.get("match_id")
    if match_id is None:
        return None
    try:
        recorded_at = unix_to_iso(row.get("start_time"))
    except (TypeError, ValueError, OverflowError, OSError):
        log.warning("ranks: account %s match %s bad start_time; skipping rank row",
                    account_id, match_id)
        return None
    return (account_id, match_id, badge, recorded_at)


def upsert_ranks_from_history(conn, account_id: int, history: list[dict]) -> int:
    """Upsert a rank row for each Ranked game in a parsed match-history.

    Does NOT commit: discovery calls this inside its own transaction, so the
    summaries, the queue and the rank rows from one response land together.
    Idempotent -- the upsert keys on (account_id, match_id)."""
    rows = [params for row in history
            if (params := _rank_row(account_id, row)) is not None]
    conn.executemany(_UPSERT, rows)
    return len(rows)
