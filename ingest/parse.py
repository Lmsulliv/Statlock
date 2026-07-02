"""Parsing match metadata into database rows. Pure functions, no HTTP.

Parsing rules (docs/ingestion-spec.md):
- damage/healing totals come from the LAST entry of each player's stats[]
  time series; a missing or empty series yields NULLs, never zeros (a zero
  is a claim, a NULL is an admission);
- the full payload stays untouched in matches.raw_json so future features
  can backfill without re-fetching;
- items[] mixes shop purchases with ability/level-up entries; only IDs
  known to the items table (assets type=="upgrade") are purchases.
"""
import sqlite3
from dataclasses import dataclass

from ingest.util import unix_to_iso
from stats.laning import laning_mark


@dataclass
class ParsedMatch:
    match_row: dict
    players: list[dict]
    purchases: list[tuple]  # (match_id, player_slot, account_id, item_id, purchase_time_s, sold_time_s)
    kill_events: list[tuple]  # (match_id, game_time_s, victim_slot, killer_slot)
    laning_stats: list[tuple]  # (match_id, player_slot, net_worth, last_hits, denies, sampled_at_s)
    damage_taken_sources: list[tuple]  # (match_id, victim_slot, source_slot, damage_taken)


def finals_from_stats(
    stats: list[dict] | None,
) -> tuple[int | None, int | None, int | None, int | None]:
    """(player_damage, obj_damage, healing, player_damage_taken) from the last
    stats entry, or NULLs. player_damage_taken is the net, post-mitigation total
    you took (api-findings, "Damage taken"); a missing series stays NULL, never 0."""
    if not stats:
        return (None, None, None, None)
    last = stats[-1]
    return (last.get("player_damage"), last.get("boss_damage"),
            last.get("player_healing"), last.get("player_damage_taken"))


def derive_kill_events(meta: dict) -> list[tuple]:
    """Per-kill rows from each player's death_details[]. Pure, no DB/HTTP.

    This is the storage-facing sibling of api.match_detail.parse_deaths and walks
    death_details the same way (kept separate because ingest must not import api):
    each player owns the array of their own deaths, so the victim is that player
    and the killer is `killer_player_slot`. Two deliberate differences for storage:

    - a killer slot that maps to no roster player (a tower/creep kill) is stored as
      killer_slot = NULL -- there's nothing to join to -- rather than dropping the
      event, so the death is never lost;
    - rows are ordered by game_time_s (NULLs last) for a stable, readable feed.

    Every field is read with .get() so a sparse or empty ('{}') payload yields an
    empty list instead of raising. Each row: (match_id, game_time_s, victim_slot,
    killer_slot), matching the kill_events columns.
    """
    info = meta.get("match_info") or {}
    match_id = info.get("match_id")
    players = info.get("players") or []
    roster_slots = {p.get("player_slot") for p in players}
    events: list[tuple] = []
    for p in players:
        victim_slot = p.get("player_slot")
        for d in p.get("death_details") or []:
            killer_slot = d.get("killer_player_slot")
            if killer_slot not in roster_slots:
                killer_slot = None  # tower / creep: no roster player to attribute to
            events.append((match_id, d.get("game_time_s"), victim_slot, killer_slot))
    events.sort(key=lambda e: (e[1] is None, e[1]))
    return events


def derive_laning_stats(meta: dict) -> list[tuple]:
    """Each player's end-of-laning snapshot from their stats[] series. Pure, no
    DB/HTTP -- the storage-facing reader of the laning finding (api-findings).

    For every player, stats.laning.laning_mark picks the latest snapshot at or
    before LANE_END_S; we read net_worth, creep_kills (the per-snapshot last-hit
    proxy -- the snapshot's own last_hits field is null), and denies from it, and
    record which snapshot via sampled_at_s. A player with no lane-end snapshot
    (empty/short series) is SKIPPED rather than stored as zeros, so a missing mark
    stays "we don't know" and can't poison a baseline average.

    Every field is read with .get() so a sparse or empty ('{}') payload yields an
    empty list instead of raising. Each row: (match_id, player_slot, net_worth,
    last_hits, denies, sampled_at_s), matching the laning_stats columns.
    """
    info = meta.get("match_info") or {}
    match_id = info.get("match_id")
    rows: list[tuple] = []
    for p in info.get("players") or []:
        mark = laning_mark(p.get("stats"))
        if mark is None:
            continue
        rows.append((match_id, p.get("player_slot"), mark.get("net_worth"),
                     mark.get("creep_kills"), mark.get("denies"),
                     mark.get("time_stamp_s")))
    return rows


def derive_damage_taken_sources(meta: dict) -> list[tuple]:
    """Per (victim, source) gross damage rows from match_info.damage_matrix. Pure,
    no DB/HTTP -- the storage-facing reader of the damage-taken finding (api-findings).

    The damage_matrix attributes damage from each dealer to each victim as a
    CUMULATIVE time series (one value per sample_time_s), so a source's running
    total is its damage[] array's LAST value; we sum those finals over a dealer's
    many sources to get the gross damage that dealer dealt to that victim. A dealer
    slot that maps to no roster player (slot 0 / environment: creeps, towers, boss)
    is recorded as source_slot = NULL, exactly like derive_kill_events' non-roster
    killer -- nothing is dropped, and slots collapse to one row per (victim, source).

    This is GROSS, pre-mitigation damage: it does NOT reconcile with the net
    player_damage_taken total (api-findings), so it only backs a RELATIVE per-enemy
    ranking, never an absolute total. Every field is read with .get() so a body
    without a damage_matrix (or an empty '{}' payload) yields an empty list instead
    of raising. Each row: (match_id, victim_slot, source_slot, damage_taken)."""
    info = meta.get("match_info") or {}
    match_id = info.get("match_id")
    roster_slots = {p.get("player_slot") for p in info.get("players") or []}
    matrix = info.get("damage_matrix") or {}
    totals: dict[tuple, int] = {}
    for dealer in matrix.get("damage_dealers") or []:
        source_slot = dealer.get("dealer_player_slot")
        if source_slot not in roster_slots:
            source_slot = None  # environment: no roster player to attribute to
        for src in dealer.get("damage_sources") or []:
            for tgt in src.get("damage_to_players") or []:
                series = tgt.get("damage") or []
                if not series:
                    continue
                key = (tgt.get("target_player_slot"), source_slot)
                totals[key] = totals.get(key, 0) + series[-1]
    rows = [(match_id, victim, source, dmg)
            for (victim, source), dmg in totals.items()]
    # Stable, readable order: by victim, then source (NULL/environment last).
    rows.sort(key=lambda r: (r[1] is None, r[1], r[2] is None, r[2] or 0))
    return rows


def parse_metadata(meta: dict, raw_body: str, shop_item_ids: set[int],
                   era_id: int | None, ingested_at: str) -> ParsedMatch:
    info = meta["match_info"]
    match_id = info["match_id"]
    winning_team = info["winning_team"]

    match_row = {
        "match_id": match_id,
        "start_time": unix_to_iso(info["start_time"]),
        "duration_s": info["duration_s"],
        "game_mode": str(info.get("game_mode")) if info.get("game_mode") is not None else None,
        "winning_team": winning_team,
        "era_id": era_id,
        "average_badge_team0": info.get("average_badge_team0"),
        "average_badge_team1": info.get("average_badge_team1"),
        "raw_json": raw_body,
        "ingested_at": ingested_at,
    }

    players = []
    purchases = []
    for p in info["players"]:
        damage, obj_damage, healing, damage_taken = finals_from_stats(p.get("stats"))
        players.append({
            "match_id": match_id,
            "player_slot": p["player_slot"],
            "account_id": p["account_id"],
            "hero_id": p["hero_id"],
            "team": p["team"],
            "lane": p.get("assigned_lane"),
            "kills": p.get("kills"),
            "deaths": p.get("deaths"),
            "assists": p.get("assists"),
            "net_worth": p.get("net_worth"),
            "last_hits": p.get("last_hits"),
            "denies": p.get("denies"),
            "player_damage": damage,
            "obj_damage": obj_damage,
            "healing": healing,
            "player_damage_taken": damage_taken,
            "won": int(p["team"] == winning_team),
        })
        # PK is (match_id, player_slot, item_id): if an item was sold and
        # re-bought we keep the first purchase, a known simplification.
        seen: set[int] = set()
        for entry in sorted(p.get("items", []), key=lambda e: e.get("game_time_s", 0)):
            item_id = entry.get("item_id")
            if item_id in shop_item_ids and item_id not in seen:
                seen.add(item_id)
                purchases.append((match_id, p["player_slot"], p["account_id"], item_id,
                                  entry.get("game_time_s"), entry.get("sold_time_s", 0)))

    return ParsedMatch(match_row, players, purchases, derive_kill_events(meta),
                       derive_laning_stats(meta), derive_damage_taken_sources(meta))


def era_id_for(conn: sqlite3.Connection, start_time_iso: str) -> int | None:
    """The era a match belongs to: latest era started at or before the match."""
    row = conn.execute(
        "SELECT era_id FROM patch_eras WHERE started_at <= ? ORDER BY started_at DESC LIMIT 1",
        (start_time_iso,),
    ).fetchone()
    return row["era_id"] if row else None


def insert_match(conn: sqlite3.Connection, parsed: ParsedMatch) -> None:
    """Insert all of a match's rows. The caller owns the transaction:
    one match = one transaction (hard rule 4), and the fetch_queue status
    update belongs in the same transaction so a crash can't leave the two
    out of step."""
    m = parsed.match_row
    conn.execute(
        "INSERT INTO matches(match_id, start_time, duration_s, game_mode, winning_team,"
        " era_id, average_badge_team0, average_badge_team1, raw_json, ingested_at)"
        " VALUES (:match_id, :start_time, :duration_s, :game_mode, :winning_team,"
        " :era_id, :average_badge_team0, :average_badge_team1, :raw_json, :ingested_at)",
        m,
    )
    conn.executemany(
        "INSERT INTO match_players(match_id, player_slot, account_id, hero_id, team, lane,"
        " kills, deaths, assists, net_worth, last_hits, denies, player_damage, obj_damage,"
        " healing, player_damage_taken, won)"
        " VALUES (:match_id, :player_slot, :account_id, :hero_id, :team, :lane, :kills,"
        " :deaths, :assists, :net_worth, :last_hits, :denies, :player_damage, :obj_damage,"
        " :healing, :player_damage_taken, :won)",
        parsed.players,
    )
    conn.executemany(
        "INSERT INTO match_item_purchases(match_id, player_slot, account_id, item_id,"
        " purchase_time_s, sold_time_s)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        parsed.purchases,
    )
    replace_kill_events(conn, m["match_id"], parsed.kill_events)
    replace_laning_stats(conn, m["match_id"], parsed.laning_stats)
    replace_damage_taken_sources(conn, m["match_id"], parsed.damage_taken_sources)


def replace_kill_events(conn: sqlite3.Connection, match_id: int,
                        events: list[tuple]) -> None:
    """Idempotently write a match's kill_events: clear this match's rows, then
    insert the derived ones. Delete-then-insert (rather than INSERT OR IGNORE)
    because event_id is an autoincrement surrogate with no natural key to dedupe
    on -- so the backfill can re-run without piling up duplicates. On first ingest
    the DELETE simply matches nothing. Caller owns the transaction (hard rule 4)."""
    conn.execute("DELETE FROM kill_events WHERE match_id = ?", (match_id,))
    conn.executemany(
        "INSERT INTO kill_events(match_id, game_time_s, victim_slot, killer_slot)"
        " VALUES (?, ?, ?, ?)",
        events,
    )


def replace_laning_stats(conn: sqlite3.Connection, match_id: int,
                         rows: list[tuple]) -> None:
    """Idempotently write a match's laning_stats: clear this match's rows, then
    insert the derived ones. Delete-then-insert (rather than INSERT OR REPLACE) so
    a player who no longer has a lane-end snapshot on a re-parse is dropped, not
    left stale. On first ingest the DELETE matches nothing. Caller owns the
    transaction (hard rule 4), same shape as replace_kill_events."""
    conn.execute("DELETE FROM laning_stats WHERE match_id = ?", (match_id,))
    conn.executemany(
        "INSERT INTO laning_stats(match_id, player_slot, net_worth, last_hits,"
        " denies, sampled_at_s) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def replace_damage_taken_sources(conn: sqlite3.Connection, match_id: int,
                                 rows: list[tuple]) -> None:
    """Idempotently write a match's damage_taken_sources: clear this match's rows,
    then insert the derived ones. Delete-then-insert, same shape as
    replace_kill_events, so the archive backfill can re-run without piling up rows.
    On first ingest the DELETE matches nothing. Caller owns the transaction (hard
    rule 4)."""
    conn.execute("DELETE FROM damage_taken_sources WHERE match_id = ?", (match_id,))
    conn.executemany(
        "INSERT INTO damage_taken_sources(match_id, victim_slot, source_slot,"
        " damage_taken) VALUES (?, ?, ?, ?)",
        rows,
    )


# Re-exported for convenience: tests and callers treat parse as the module
# that knows how metadata timestamps become ISO strings.
__all__ = ["ParsedMatch", "finals_from_stats", "derive_kill_events",
           "derive_laning_stats", "derive_damage_taken_sources", "parse_metadata",
           "era_id_for", "insert_match", "replace_kill_events", "replace_laning_stats",
           "replace_damage_taken_sources", "unix_to_iso"]
