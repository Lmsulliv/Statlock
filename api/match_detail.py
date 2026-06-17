"""Parsing one match's stored metadata into a display-ready detail view.

Pure functions, no HTTP and no DB -- the same discipline as ingest/parse.py.
api.service calls these on matches.raw_json (json.loads'd) and then enriches the
result with hero/item names from the lookup tables.

Why parse the roster and the timeline out of raw_json rather than the relational
tables: death_details references players by `player_slot` (1-12), and that slot
is only present in the raw payload -- match_players never stored it. So the slot
-> hero map, and therefore the kill/death feed, can only be rebuilt from the
payload. Purchases are the exception: they come from match_item_purchases, which
was already filtered to real shop items at ingest.

Every field is read with .get() so a sparse or empty ('{}') payload yields empty
lists instead of raising -- a missing match is the service's 404, not a crash.
"""


def _players(meta: dict) -> list[dict]:
    info = meta.get("match_info") or {}
    return info.get("players") or []


def parse_players(meta: dict) -> list[dict]:
    """The 12-player roster. `won` is derived against the match's winning_team
    (there is no per-player won flag in the payload); `lane` is the raw
    assigned_lane integer -- the API exposes no lane names, so we don't invent
    any (hard rule 6)."""
    info = meta.get("match_info") or {}
    winning_team = info.get("winning_team")
    out = []
    for p in _players(meta):
        team = p.get("team")
        out.append({
            "player_slot": p.get("player_slot"),
            "account_id": p.get("account_id"),
            "hero_id": p.get("hero_id"),
            "team": team,
            "lane": p.get("assigned_lane"),
            "kills": p.get("kills"),
            "deaths": p.get("deaths"),
            "assists": p.get("assists"),
            "net_worth": p.get("net_worth"),
            "last_hits": p.get("last_hits"),
            "denies": p.get("denies"),
            "won": team == winning_team if team is not None else None,
        })
    return out


def _slot_map(meta: dict) -> dict[int, dict]:
    """player_slot -> {hero_id, account_id, team}, for resolving death killers."""
    return {
        p.get("player_slot"): {
            "hero_id": p.get("hero_id"),
            "account_id": p.get("account_id"),
            "team": p.get("team"),
        }
        for p in _players(meta)
    }


def parse_deaths(meta: dict) -> list[dict]:
    """The whole-match kill/death feed, sorted by game_time_s.

    Each player's death_details[] lists that player's deaths, so the victim is
    the owner of the array and the killer is `killer_player_slot`. A killer slot
    that maps to no player (e.g. a tower or creep kill) leaves the killer fields
    None rather than dropping the event."""
    slots = _slot_map(meta)
    events = []
    for p in _players(meta):
        victim = {
            "slot": p.get("player_slot"),
            "hero_id": p.get("hero_id"),
            "team": p.get("team"),
        }
        for d in p.get("death_details") or []:
            killer_slot = d.get("killer_player_slot")
            killer = slots.get(killer_slot, {})
            events.append({
                "game_time_s": d.get("game_time_s"),
                "victim_slot": victim["slot"],
                "victim_hero_id": victim["hero_id"],
                "victim_team": victim["team"],
                "killer_slot": killer_slot,
                "killer_hero_id": killer.get("hero_id"),
                "killer_team": killer.get("team"),
            })
    events.sort(key=lambda e: (e["game_time_s"] is None, e["game_time_s"]))
    return events


def parse_detail(meta: dict) -> dict:
    """Roster + kill/death feed. Empty lists when the payload has no match_info."""
    return {"players": parse_players(meta), "deaths": parse_deaths(meta)}
