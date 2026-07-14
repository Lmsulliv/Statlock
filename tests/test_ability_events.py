"""Ability level-ups: derivation, same-transaction insert, and archive backfill.

ability_events materializes each ability point from the payload's players[].items[]
(the entries whose item_id is a known ability, assets type=="ability") so the Match
Detail strip and the Heroes-tab skill-order aggregation can read it via SQL joins
instead of re-walking raw_json. See docs/api-findings.md, "Ability level-up
extraction rule".

Hard rule 3 (no live API) holds trivially -- every function here is a pure parse or
a local-SQLite read, and the autouse _no_network fixture also blocks urlopen. The
backfill never re-fetches: it reads bodies already stored in matches.raw_json (or
archived in raw_api_responses).
"""
import json

from ingest.parse import derive_ability_events, insert_match, parse_metadata
from ingest.reprocess import reprocess_archive
from tracker import rawstore

MATCH_ID = 999
H_ME, H_ALLY, H_ENEMY1, H_ENEMY2 = 7, 10, 15, 20
JUNE = "2026-06-15T12:00:00+00:00"

# Ability ids (assets type=="ability"); shop ids 100/200; 999 is an unknown id.
A1, A2, A3, A4 = 5001, 5002, 5003, 5004
ABILITY_IDS = {A1, A2, A3, A4}


def _meta(winning_team: int = 0) -> dict:
    """Four players (slots 1-4, deliberately not 0-based) whose items[] mix shop
    buys, ability points, and one unknown id -- exercising the type filter, the
    game_time_s ordering (incl. a same-second tie and a NULL time), and the
    all-players rule."""
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
                # slot 1: shop dropped; 4 ability points incl. a t=20 tie.
                {"player_slot": 1, "account_id": 100, "hero_id": H_ME, "team": 0,
                 "items": [
                     {"item_id": 100, "game_time_s": 5},    # shop -> dropped
                     {"item_id": A1, "game_time_s": 10},
                     {"item_id": A2, "game_time_s": 20},
                     {"item_id": A1, "game_time_s": 20},    # banked tie with A2
                     {"item_id": A3, "game_time_s": 40},
                 ]},
                # slot 2: 3 ability points, one with NULL game_time_s (sorts last).
                {"player_slot": 2, "account_id": 200, "hero_id": H_ALLY, "team": 0,
                 "items": [
                     {"item_id": A4, "game_time_s": 15},
                     {"item_id": 200, "game_time_s": 8},    # shop -> dropped
                     {"item_id": A4, "game_time_s": 30},
                     {"item_id": A4},                        # no game_time_s -> last
                 ]},
                # slot 3: no items at all -> no ability events.
                {"player_slot": 3, "account_id": 300, "hero_id": H_ENEMY1, "team": 1,
                 "items": []},
                # slot 4: only an unknown id (neither shop nor ability) -> dropped.
                {"player_slot": 4, "account_id": 400, "hero_id": H_ENEMY2, "team": 1,
                 "items": [{"item_id": 999, "game_time_s": 5}]},
            ],
        }
    }


def _seed_heroes(conn, *hero_ids) -> None:
    for hid in hero_ids:
        conn.execute("INSERT INTO heroes(hero_id, name, fetched_at) VALUES (?, ?, 't')",
                     (hid, f"Hero{hid}"))
    conn.commit()


def _seed_abilities(conn, *ability_ids) -> None:
    """reprocess-archive builds its ability id set from the abilities table, so the
    backfill tests must seed it (the parse-time tests pass the set explicitly)."""
    for aid in ability_ids:
        conn.execute(
            "INSERT INTO abilities(ability_id, name, ability_type, fetched_at)"
            " VALUES (?, ?, 'signature', 't')", (aid, f"Ability{aid}"))
    conn.commit()


def _archive(conn, match_id, body) -> None:
    conn.execute(
        "INSERT INTO raw_api_responses(url, status_code, body, fetched_at)"
        " VALUES (?, 200, ?, ?)",
        (f"https://api.deadlock-api.com/v1/matches/{match_id}/metadata", body, JUNE),
    )
    conn.commit()


# ── Pure derivation ──────────────────────────────────────────────────────────

def test_derive_keeps_abilities_drops_shop_and_unknown():
    rows = derive_ability_events(_meta(), ABILITY_IDS)
    # slot1: 4 points, slot2: 3 points, slots 3 & 4: none -> 7 rows total.
    assert len(rows) == 7
    assert all(r[0] == MATCH_ID for r in rows)
    kept_ids = {r[4] for r in rows}
    assert kept_ids <= ABILITY_IDS            # nothing but abilities survived
    assert 100 not in kept_ids and 200 not in kept_ids and 999 not in kept_ids


def test_point_number_is_ordinal_by_game_time_per_player():
    rows = derive_ability_events(_meta(), ABILITY_IDS)
    # Row shape: (match_id, player_slot, account_id, hero_id, ability_id, point_number, game_time_s)
    slot1 = [r for r in rows if r[1] == 1]
    assert [r[5] for r in slot1] == [1, 2, 3, 4]           # 1-based, contiguous
    # Ordered by game_time_s; the t=20 tie keeps original list order (A2 before A1).
    assert [(r[4], r[6]) for r in slot1] == [
        (A1, 10), (A2, 20), (A1, 20), (A3, 40)]
    # account_id and the PLAYER's hero come straight off the roster row.
    assert all(r[2] == 100 and r[3] == H_ME for r in slot1)


def test_null_game_time_sorts_last_and_is_stored_null():
    rows = derive_ability_events(_meta(), ABILITY_IDS)
    slot2 = [r for r in rows if r[1] == 2]
    assert [r[5] for r in slot2] == [1, 2, 3]
    assert [r[6] for r in slot2] == [15, 30, None]         # NULL time sorts last, kept NULL


def test_level_is_entry_count_per_ability():
    rows = derive_ability_events(_meta(), ABILITY_IDS)
    slot2 = [r for r in rows if r[1] == 2]
    assert len(slot2) == 3 and all(r[4] == A4 for r in slot2)   # A4 reached level 3


def test_empty_payload_yields_no_events():
    assert derive_ability_events({}, ABILITY_IDS) == []
    assert derive_ability_events(json.loads("{}"), ABILITY_IDS) == []


def test_empty_ability_id_set_yields_no_events():
    # No ability reference loaded yet -> nothing is classified as an ability.
    assert derive_ability_events(_meta(), set()) == []


# ── Same-transaction insert via insert_match ─────────────────────────────────

def test_insert_match_writes_ability_events(db):
    _seed_heroes(db, H_ME, H_ALLY, H_ENEMY1, H_ENEMY2)
    meta = _meta()
    # shop_item_ids empty (so no purchases / no items FK needed); abilities explicit.
    parsed = parse_metadata(meta, json.dumps(meta), set(), None, JUNE, ABILITY_IDS)
    with db:
        insert_match(db, parsed)

    rows = db.execute(
        "SELECT player_slot, account_id, hero_id, ability_id, point_number, game_time_s"
        " FROM ability_events WHERE match_id = ? ORDER BY player_slot, point_number",
        (MATCH_ID,)).fetchall()
    assert len(rows) == 7
    slot1 = [r for r in rows if r["player_slot"] == 1]
    assert [r["point_number"] for r in slot1] == [1, 2, 3, 4]
    assert slot1[0]["hero_id"] == H_ME and slot1[0]["account_id"] == 100


# ── Archive backfill (reprocess-archive) ─────────────────────────────────────

def test_reprocess_archive_is_idempotent(db):
    _seed_heroes(db, H_ME, H_ALLY, H_ENEMY1, H_ENEMY2)
    _seed_abilities(db, A1, A2, A3, A4)
    meta = _meta()
    body = json.dumps(meta)
    parsed = parse_metadata(meta, body, set(), None, JUNE, ABILITY_IDS)
    with db:
        insert_match(db, parsed)
    _archive(db, MATCH_ID, body)

    def count():
        return db.execute("SELECT COUNT(*) FROM ability_events WHERE match_id = ?",
                          (MATCH_ID,)).fetchone()[0]

    result1 = reprocess_archive(db)
    after_first = count()
    reprocess_archive(db)
    after_second = count()

    assert after_first == 7 and after_second == 7          # delete-then-insert holds steady
    assert result1["ability_events_rebuilt"] == 7
    assert db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1


def test_reprocess_rebuilds_from_compressed_raw_json_without_archive(db):
    """The archive is matches.raw_json now. Reprocess must rebuild ability_events
    from a COMPRESSED raw_json body with no raw_api_responses row present, using the
    ability id set read from the abilities reference table."""
    _seed_heroes(db, H_ME, H_ALLY, H_ENEMY1, H_ENEMY2)
    _seed_abilities(db, A1, A2, A3, A4)
    meta = _meta()
    body = json.dumps(meta)
    parsed = parse_metadata(meta, body, set(), None, JUNE, ABILITY_IDS)
    with db:
        insert_match(db, parsed)
    stored = db.execute("SELECT raw_json FROM matches WHERE match_id = ?",
                        (MATCH_ID,)).fetchone()["raw_json"]
    assert isinstance(stored, bytes)                      # compressed on disk
    assert rawstore.load(stored) == body
    assert db.execute("SELECT COUNT(*) FROM raw_api_responses").fetchone()[0] == 0

    db.execute("DELETE FROM ability_events WHERE match_id = ?", (MATCH_ID,))
    db.commit()

    result = reprocess_archive(db)

    assert result["matches_recovered"] == 0              # already stored
    assert db.execute("SELECT COUNT(*) FROM ability_events WHERE match_id = ?",
                      (MATCH_ID,)).fetchone()[0] == 7
