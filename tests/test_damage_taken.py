"""Damage-taken materialization: derivation from damage_matrix, the net total
from stats[], same-transaction insert, and archive backfill.

Two raw_json sources (docs/api-findings.md, "Damage taken"):
- match_info.damage_matrix attributes GROSS, pre-mitigation damage per dealer slot
  to each victim slot, as a cumulative time series. derive_damage_taken_sources
  keeps each source's final value, summed per (victim, source) -> damage_taken_sources.
- the LAST stats[] entry carries net player_damage_taken -> match_players column.

Hard rule 3 (no live API) holds trivially: every function here is a pure parse or a
local-SQLite read, and the autouse _no_network fixture also blocks urlopen. The
backfill never re-fetches -- it reads bodies already archived in raw_api_responses.
"""
import json

from ingest.parse import (
    derive_damage_taken_sources,
    finals_from_stats,
    insert_match,
    parse_metadata,
)
from ingest.reprocess import reprocess_archive

MATCH_ID = 777
H_ME, H_ALLY, H_ENEMY1, H_ENEMY2 = 7, 10, 15, 20
JUNE = "2026-06-15T12:00:00+00:00"


def _stats(damage_taken):
    """A two-snapshot stats[] series whose LAST entry holds the net total."""
    return [
        {"time_stamp_s": 180, "player_damage_taken": damage_taken // 4},
        {"time_stamp_s": 1800, "player_damage_taken": damage_taken},
    ]


def _meta():
    """Four players (slots 1-4). slot 1 = me (team 0). The damage_matrix gives:
    enemy1 (slot 3) deals me 55 across two sources (cumulative finals 40 + 15),
    enemy2 (slot 4) deals me 100, the environment (dealer slot 0, no roster player)
    deals me 9, and I (slot 1) deal enemy1 50 -- so the rows cover an enemy, an
    environment NULL source, and a victim who is not me."""
    return {
        "match_info": {
            "match_id": MATCH_ID,
            "start_time": 0,
            "duration_s": 1800,
            "game_mode": 1,
            "winning_team": 0,
            "average_badge_team0": 50,
            "average_badge_team1": 52,
            "players": [
                {"player_slot": 1, "account_id": 100, "hero_id": H_ME, "team": 0,
                 "stats": _stats(48000)},
                {"player_slot": 2, "account_id": 200, "hero_id": H_ALLY, "team": 0,
                 "stats": _stats(50000)},
                {"player_slot": 3, "account_id": 0, "hero_id": H_ENEMY1, "team": 1,
                 "stats": _stats(30000)},
                {"player_slot": 4, "account_id": 400, "hero_id": H_ENEMY2, "team": 1,
                 "stats": _stats(20000)},
            ],
            "damage_matrix": {
                "sample_time_s": [180, 1800],
                "damage_dealers": [
                    {"dealer_player_slot": 3, "damage_sources": [
                        {"source_details_index": 0, "damage_to_players": [
                            {"target_player_slot": 1, "damage": [10, 40]}]},
                        {"source_details_index": 1, "damage_to_players": [
                            {"target_player_slot": 1, "damage": [5, 15]}]},
                    ]},
                    {"dealer_player_slot": 4, "damage_sources": [
                        {"source_details_index": 0, "damage_to_players": [
                            {"target_player_slot": 1, "damage": [100]}]},
                    ]},
                    {"dealer_player_slot": 0, "damage_sources": [   # environment
                        {"source_details_index": 2, "damage_to_players": [
                            {"target_player_slot": 1, "damage": [7, 9]}]},
                    ]},
                    {"dealer_player_slot": 1, "damage_sources": [   # me -> enemy1
                        {"source_details_index": 0, "damage_to_players": [
                            {"target_player_slot": 3, "damage": [50]}]},
                    ]},
                ],
            },
        }
    }


def _seed_heroes(conn, *hero_ids):
    for hid in hero_ids:
        conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, 't')",
                     (hid, f"Hero{hid}"))
    conn.commit()


def _archive(conn, match_id, body):
    conn.execute(
        "INSERT INTO raw_api_responses(url, status_code, body, fetched_at)"
        " VALUES (?, 200, ?, ?)",
        (f"https://api.deadlock-api.com/v1/matches/{match_id}/metadata", body, JUNE),
    )
    conn.commit()


# ── Pure derivation ──────────────────────────────────────────────────────────

def test_finals_from_stats_reads_net_damage_taken():
    # 4-tuple now: (player_damage, obj_damage, healing, player_damage_taken).
    assert finals_from_stats(_stats(48000))[3] == 48000
    assert finals_from_stats(None) == (None, None, None, None)
    assert finals_from_stats([]) == (None, None, None, None)


def test_derive_sums_cumulative_finals_per_source():
    rows = derive_damage_taken_sources(_meta())
    by_pair = {(victim, source): dmg for (_mid, victim, source, dmg) in rows}
    # enemy1's two sources collapse to one row: final 40 + final 15 = 55.
    assert by_pair[(1, 3)] == 55
    assert by_pair[(1, 4)] == 100
    assert all(r[0] == MATCH_ID for r in rows)


def test_derive_maps_environment_dealer_to_null_source():
    rows = derive_damage_taken_sources(_meta())
    env = next(r for r in rows if r[1] == 1 and r[3] == 9)
    assert env[2] is None        # dealer slot 0 maps to no roster player


def test_derive_keeps_damage_to_other_victims():
    rows = derive_damage_taken_sources(_meta())
    mine = next(r for r in rows if r[1] == 3)   # me (slot 1) dealing to enemy1 (slot 3)
    assert mine[2] == 1 and mine[3] == 50


def test_empty_payload_yields_no_rows():
    assert derive_damage_taken_sources({}) == []
    assert derive_damage_taken_sources({"match_info": {"players": []}}) == []


# ── Same-transaction insert via insert_match ─────────────────────────────────

def test_insert_match_writes_damage_and_the_column(db):
    _seed_heroes(db, H_ME, H_ALLY, H_ENEMY1, H_ENEMY2)
    meta = _meta()
    parsed = parse_metadata(meta, json.dumps(meta), set(), None, JUNE)
    with db:
        insert_match(db, parsed)

    rows = db.execute(
        "SELECT victim_slot, source_slot, damage_taken FROM damage_taken_sources"
        " WHERE match_id = ? ORDER BY victim_slot, source_slot IS NULL, source_slot",
        (MATCH_ID,)).fetchall()
    assert len(rows) == 4
    enemy1 = next(r for r in rows if r["victim_slot"] == 1 and r["source_slot"] == 3)
    assert enemy1["damage_taken"] == 55
    # The net total landed on the match_players column for the owner's slot.
    taken = db.execute(
        "SELECT player_damage_taken FROM match_players WHERE match_id = ? AND player_slot = 1",
        (MATCH_ID,)).fetchone()["player_damage_taken"]
    assert taken == 48000


# ── Archive backfill (reprocess-archive) ─────────────────────────────────────

def test_reprocess_backfills_damage_and_column_idempotently(db):
    _seed_heroes(db, H_ME, H_ALLY, H_ENEMY1, H_ENEMY2)
    meta = _meta()
    body = json.dumps(meta)
    parsed = parse_metadata(meta, body, set(), None, JUNE)
    with db:
        insert_match(db, parsed)
    # Simulate a pre-migration row: blank the column so the backfill has work to do.
    db.execute("UPDATE match_players SET player_damage_taken = NULL WHERE match_id = ?",
               (MATCH_ID,))
    db.execute("DELETE FROM damage_taken_sources WHERE match_id = ?", (MATCH_ID,))
    db.commit()
    _archive(db, MATCH_ID, body)

    def counts():
        n = db.execute("SELECT COUNT(*) FROM damage_taken_sources WHERE match_id = ?",
                       (MATCH_ID,)).fetchone()[0]
        taken = db.execute("SELECT player_damage_taken FROM match_players"
                           " WHERE match_id = ? AND player_slot = 1",
                           (MATCH_ID,)).fetchone()[0]
        return n, taken

    result = reprocess_archive(db)
    assert result["damage_source_rows_rebuilt"] == 4
    assert counts() == (4, 48000)              # column backfilled, rows rebuilt
    reprocess_archive(db)
    assert counts() == (4, 48000)              # delete-then-insert holds steady
