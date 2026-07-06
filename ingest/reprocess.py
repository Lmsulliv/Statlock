"""reprocess-archive: rebuild derived tables from the raw_json archive.

No HTTP -- the archived match bodies are the source of truth here, so
backfilling costs zero requests and can't trip the rate limit.

The archive is now matches.raw_json itself (a successful 200 metadata ingest is
stored there and NOT duplicated into raw_api_responses; see CLAUDE.md hard rule
2). So this rebuild has two sources, in priority order:

  1. PRIMARY -- matches.raw_json. For every stored match we (re)materialize its
     derived tables (kill_events + laning_stats + damage_taken_sources) from the
     archived body, and backfill the match_players.player_damage_taken column a
     later schema added. raw_json is read through tracker.rawstore, so both
     compressed (new) and legacy uncompressed rows work.

  2. FALLBACK -- raw_api_responses, for match_ids that are NOT in the matches
     table (e.g. a pre-player_slot 6x0 anonymized match, or any 200 whose parse
     failed, that crashed ingest before the schema could accept it). Those bodies
     still live in the response archive; we parse and insert match + players +
     purchases + kill_events + laning_stats now, and flip the fetch_queue row to
     'fetched'. A match already stored (source 1) is never re-recovered.

One match = one transaction (hard rule 4). Re-running is a no-op on row counts:
recovered matches move to the primary source on the next pass, and the replace_*
helpers delete-then-insert, so the derived tables stay stable. This is also how
new derived tables backfill historical matches with zero API calls: add the
table, add its replace_* call here, run reprocess-archive (laning_stats did).
"""
import json
import logging

from ingest.parse import (
    derive_damage_taken_sources,
    derive_kill_events,
    derive_laning_stats,
    era_id_for,
    finals_from_stats,
    insert_match,
    parse_metadata,
    replace_damage_taken_sources,
    replace_kill_events,
    replace_laning_stats,
)
from ingest.util import unix_to_iso, utcnow
from tracker import rawstore

log = logging.getLogger(__name__)


def _archive_only_bodies(conn, stored_ids: set[int]) -> dict[int, str]:
    """match_id -> its most recently archived status-200 metadata body, for
    matches NOT already stored (those are handled from matches.raw_json). A match
    re-fetched over time has several archived copies; the highest id (newest) wins.
    Bodies that don't parse or lack match_info are skipped -- this is where the
    empty-200 responses land, and they carry nothing to reprocess."""
    bodies: dict[int, str] = {}
    rows = conn.execute(
        "SELECT id, body FROM raw_api_responses"
        " WHERE status_code = 200 AND url LIKE '%/metadata'"
        " ORDER BY id"
    ).fetchall()
    for row in rows:
        try:
            meta = json.loads(row["body"])
        except (json.JSONDecodeError, TypeError):
            continue
        info = meta.get("match_info") if isinstance(meta, dict) else None
        if not info or info.get("match_id") is None:
            continue
        match_id = info["match_id"]
        if match_id in stored_ids:
            continue  # already stored: rebuilt from matches.raw_json instead
        bodies[match_id] = row["body"]
    return bodies


def _backfill_damage_taken_column(conn, meta: dict) -> None:
    """Set match_players.player_damage_taken from raw_json for an already-stored
    match. The derived tables get a replace_* call, but match_players is INSERTed
    once (not replaced), so when a new column lands its historical rows stay NULL
    until an explicit UPDATE fills them. Reads the same stats[] final as
    parse_metadata, so a backfilled value equals a freshly-ingested one."""
    info = meta.get("match_info") or {}
    match_id = info.get("match_id")
    for p in info.get("players") or []:
        _, _, _, damage_taken = finals_from_stats(p.get("stats"))
        conn.execute(
            "UPDATE match_players SET player_damage_taken = ?"
            " WHERE match_id = ? AND player_slot = ?",
            (damage_taken, match_id, p.get("player_slot")),
        )


def _derived_counts(conn, match_id: int) -> tuple[int, int, int]:
    """(kill_events, laning_stats, damage_taken_sources) row counts for a match."""
    kills = conn.execute(
        "SELECT COUNT(*) FROM kill_events WHERE match_id = ?", (match_id,)
    ).fetchone()[0]
    laning = conn.execute(
        "SELECT COUNT(*) FROM laning_stats WHERE match_id = ?", (match_id,)
    ).fetchone()[0]
    damage = conn.execute(
        "SELECT COUNT(*) FROM damage_taken_sources WHERE match_id = ?", (match_id,)
    ).fetchone()[0]
    return kills, laning, damage


def reprocess_archive(conn, *, now=utcnow) -> dict:
    """Backfill the derived tables + the player_damage_taken column (and recover
    unstorable matches) from the archive. Returns counts per rebuilt artifact."""
    shop_item_ids = {r["item_id"] for r in
                     conn.execute("SELECT item_id FROM items").fetchall()}
    recovered = 0
    rebuilt = 0
    laning_rebuilt = 0
    damage_rebuilt = 0

    # ── Source 1: rebuild every stored match from matches.raw_json ──────────
    # Fetch ids first (cheap), then read one body at a time -- the bodies are
    # 1-2 MB each, so we never hold them all in memory at once.
    stored_ids = {r["match_id"] for r in
                  conn.execute("SELECT match_id FROM matches").fetchall()}
    for match_id in stored_ids:
        row = conn.execute(
            "SELECT raw_json FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone()
        body = rawstore.load(row["raw_json"])
        if not body:
            continue
        try:
            meta = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            continue

        # One match = one transaction (hard rule 4).
        with conn:
            replace_kill_events(conn, match_id, derive_kill_events(meta))
            replace_laning_stats(conn, match_id, derive_laning_stats(meta))
            replace_damage_taken_sources(conn, match_id,
                                         derive_damage_taken_sources(meta))
            _backfill_damage_taken_column(conn, meta)

        kills, laning, damage = _derived_counts(conn, match_id)
        rebuilt += kills
        laning_rebuilt += laning
        damage_rebuilt += damage

    # ── Source 2: recover matches that never made it into the matches table ──
    for match_id, body in _archive_only_bodies(conn, stored_ids).items():
        meta = json.loads(body)
        start_iso = unix_to_iso(meta["match_info"]["start_time"])
        era_id = era_id_for(conn, start_iso)
        parsed = parse_metadata(meta, body, shop_item_ids, era_id, now().isoformat())

        # One match = one transaction: the inserts and the queue flip commit together.
        with conn:
            insert_match(conn, parsed)  # includes its kill_events
            # Flip the queue row if one exists (no-op otherwise): the body was
            # fetched-200 long ago but never stored.
            conn.execute(
                "UPDATE fetch_queue SET status = 'fetched', next_retry_at = NULL,"
                " last_error = NULL WHERE match_id = ?",
                (match_id,),
            )
        recovered += 1

        kills, laning, damage = _derived_counts(conn, match_id)
        rebuilt += kills
        laning_rebuilt += laning
        damage_rebuilt += damage

    log.info("reprocess-archive: %d match(es) recovered, %d kill event(s) rebuilt,"
             " %d laning row(s) rebuilt, %d damage-source row(s) rebuilt",
             recovered, rebuilt, laning_rebuilt, damage_rebuilt)
    return {"matches_recovered": recovered, "kill_events_rebuilt": rebuilt,
            "laning_rows_rebuilt": laning_rebuilt,
            "damage_source_rows_rebuilt": damage_rebuilt}
