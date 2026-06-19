"""Per-kill attribution: derivation, same-transaction insert, and archive backfill.

kill_events materializes each death from the payload's death_details[] so both the
per-match view and future aggregate kill-trade features can read it via SQL joins
instead of re-walking raw_json. derive_kill_events is the storage-facing sibling of
api.match_detail.parse_deaths (kept separate because ingest must not import api).

Hard rule 3 (no live API) holds trivially -- every function here is a pure parse or
a local-SQLite read, and the autouse _no_network fixture also blocks urlopen. The
backfill never re-fetches: it reads bodies already archived in raw_api_responses.
"""
import json

from ingest.parse import derive_kill_events, insert_match, parse_metadata
from ingest.reprocess import reprocess_archive

MATCH_ID = 999
H_ME, H_ALLY, H_ENEMY1, H_ENEMY2 = 7, 10, 15, 20
JUNE = "2026-06-15T12:00:00+00:00"


def _meta(winning_team: int = 0) -> dict:
    """Four players (slots 1-4, deliberately not 0-based) with a handful of
    death_details -- including one whose killer slot (99) maps to no player,
    standing in for a tower/creep kill."""
    return {
        "match_info": {
            "match_id": MATCH_ID,
            "start_time": 0,
            "duration_s": 1800,
            "game_mode": 1,
            "winning_team": winning_team,
            "average_badge_team0": 50,
            "average_badge_team1": 52,
            "players": [
                {"player_slot": 1, "account_id": 100, "hero_id": H_ME, "team": 0,
                 "death_details": [
                     {"game_time_s": 420, "killer_player_slot": 3},
                     {"game_time_s": 1200, "killer_player_slot": 99},
                 ]},
                {"player_slot": 2, "account_id": 200, "hero_id": H_ALLY, "team": 0,
                 "death_details": [{"game_time_s": 300, "killer_player_slot": 4}]},
                {"player_slot": 3, "account_id": 300, "hero_id": H_ENEMY1, "team": 1,
                 "death_details": [{"game_time_s": 900, "killer_player_slot": 1}]},
                {"player_slot": 4, "account_id": 400, "hero_id": H_ENEMY2, "team": 1,
                 "death_details": []},
            ],
        }
    }


def _seed_heroes(conn, *hero_ids) -> None:
    for hid in hero_ids:
        conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, 't')",
                     (hid, f"Hero{hid}"))
    conn.commit()


def _archive(conn, match_id, body) -> None:
    conn.execute(
        "INSERT INTO raw_api_responses(url, status_code, body, fetched_at)"
        " VALUES (?, 200, ?, ?)",
        (f"https://api.deadlock-api.com/v1/matches/{match_id}/metadata", body, JUNE),
    )
    conn.commit()


# ── Pure derivation ──────────────────────────────────────────────────────────

def test_derive_one_row_per_death_ordered_by_game_time():
    events = derive_kill_events(_meta())
    # One row per death_details entry across all players (4 deaths total).
    assert len(events) == 4
    # Each row is (match_id, game_time_s, victim_slot, killer_slot), ordered by time.
    assert [e[1] for e in events] == [300, 420, 900, 1200]
    assert all(e[0] == MATCH_ID for e in events)

    first = events[0]                        # ally (slot 2) dies to enemy2 (slot 4)
    assert first[2] == 2 and first[3] == 4


def test_non_player_killer_is_null_event_kept():
    events = derive_kill_events(_meta())
    env = next(e for e in events if e[1] == 1200)   # victim slot 1, killer slot 99
    assert env[2] == 1
    assert env[3] is None                            # 99 maps to no roster slot -> NULL


def test_empty_payload_yields_no_events():
    assert derive_kill_events({}) == []
    assert derive_kill_events(json.loads("{}")) == []


# ── Same-transaction insert via insert_match ─────────────────────────────────

def test_insert_match_writes_kill_events(db):
    _seed_heroes(db, H_ME, H_ALLY, H_ENEMY1, H_ENEMY2)
    meta = _meta()
    parsed = parse_metadata(meta, json.dumps(meta), set(), None, JUNE)
    with db:
        insert_match(db, parsed)

    rows = db.execute(
        "SELECT game_time_s, victim_slot, killer_slot FROM kill_events"
        " WHERE match_id = ? ORDER BY game_time_s", (MATCH_ID,)
    ).fetchall()
    assert len(rows) == 4
    assert [r["game_time_s"] for r in rows] == [300, 420, 900, 1200]
    env = next(r for r in rows if r["game_time_s"] == 1200)
    assert env["killer_slot"] is None


# ── Archive backfill (reprocess-archive) ─────────────────────────────────────

def test_reprocess_archive_is_idempotent(db):
    _seed_heroes(db, H_ME, H_ALLY, H_ENEMY1, H_ENEMY2)
    meta = _meta()
    body = json.dumps(meta)
    # Store the match the normal way, then archive its body.
    parsed = parse_metadata(meta, body, set(), None, JUNE)
    with db:
        insert_match(db, parsed)
    _archive(db, MATCH_ID, body)

    def kill_count():
        return db.execute("SELECT COUNT(*) FROM kill_events WHERE match_id = ?",
                          (MATCH_ID,)).fetchone()[0]

    reprocess_archive(db)
    after_first = kill_count()
    reprocess_archive(db)
    after_second = kill_count()

    assert after_first == 4 and after_second == 4          # delete-then-insert holds steady
    assert db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1


def _six_zero_meta() -> dict:
    """A 12-player match with six anonymized (account_id 0) players -- the shape
    that crashed ingest before player_slot became the per-match key. A few deaths
    give it kill_events to rebuild, including one environment kill (slot 99)."""
    players = []
    for slot in range(1, 13):
        account_id = 0 if slot <= 6 else 1000 + slot
        deaths = []
        if slot == 1:
            deaths = [{"game_time_s": 500, "killer_player_slot": 7}]
        elif slot == 7:
            deaths = [{"game_time_s": 800, "killer_player_slot": 1}]
        elif slot == 2:
            deaths = [{"game_time_s": 300, "killer_player_slot": 99}]   # tower/creep
        players.append({
            "player_slot": slot, "account_id": account_id, "hero_id": H_ME,
            "team": 0 if slot <= 6 else 1, "death_details": deaths,
        })
    return {"match_info": {
        "match_id": 555, "start_time": 0, "duration_s": 1800, "game_mode": 1,
        "winning_team": 0, "average_badge_team0": 50, "average_badge_team1": 50,
        "players": players,
    }}


def test_reprocess_archive_recovers_unstored_six_zero_match(db):
    _seed_heroes(db, H_ME)
    meta = _six_zero_meta()
    mid = meta["match_info"]["match_id"]
    body = json.dumps(meta)
    _archive(db, mid, body)
    # It was queued and fetched-200 but never stored (pre-player_slot crash).
    db.execute("INSERT INTO fetch_queue(match_id, discovered_at, status)"
               " VALUES (?, ?, 'failed')", (mid, JUNE))
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM matches WHERE match_id = ?",
                      (mid,)).fetchone()[0] == 0

    result = reprocess_archive(db)

    assert result["matches_recovered"] == 1
    assert db.execute("SELECT COUNT(*) FROM matches WHERE match_id = ?",
                      (mid,)).fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM match_players WHERE match_id = ?",
                      (mid,)).fetchone()[0] == 12
    assert db.execute("SELECT COUNT(*) FROM match_players WHERE match_id = ?"
                      " AND account_id = 0", (mid,)).fetchone()[0] == 6
    # Three deaths materialized; the tower/creep kill stored killer_slot NULL.
    assert db.execute("SELECT COUNT(*) FROM kill_events WHERE match_id = ?",
                      (mid,)).fetchone()[0] == 3
    assert db.execute("SELECT COUNT(*) FROM kill_events WHERE match_id = ?"
                      " AND killer_slot IS NULL", (mid,)).fetchone()[0] == 1
    # The queue row was flipped to fetched.
    assert db.execute("SELECT status FROM fetch_queue WHERE match_id = ?",
                      (mid,)).fetchone()["status"] == "fetched"
