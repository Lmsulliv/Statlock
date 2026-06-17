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


@dataclass
class ParsedMatch:
    match_row: dict
    players: list[dict]
    purchases: list[tuple]  # (match_id, player_slot, account_id, item_id, purchase_time_s, sold_time_s)


def finals_from_stats(stats: list[dict] | None) -> tuple[int | None, int | None, int | None]:
    """(player_damage, obj_damage, healing) from the last stats entry, or NULLs."""
    if not stats:
        return (None, None, None)
    last = stats[-1]
    return (last.get("player_damage"), last.get("boss_damage"), last.get("player_healing"))


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
        damage, obj_damage, healing = finals_from_stats(p.get("stats"))
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

    return ParsedMatch(match_row, players, purchases)


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
        " healing, won)"
        " VALUES (:match_id, :player_slot, :account_id, :hero_id, :team, :lane, :kills,"
        " :deaths, :assists, :net_worth, :last_hits, :denies, :player_damage, :obj_damage,"
        " :healing, :won)",
        parsed.players,
    )
    conn.executemany(
        "INSERT INTO match_item_purchases(match_id, player_slot, account_id, item_id,"
        " purchase_time_s, sold_time_s)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        parsed.purchases,
    )


# Re-exported for convenience: tests and callers treat parse as the module
# that knows how metadata timestamps become ISO strings.
__all__ = ["ParsedMatch", "finals_from_stats", "parse_metadata", "era_id_for",
           "insert_match", "unix_to_iso"]
