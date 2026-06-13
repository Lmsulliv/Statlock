"""One-off: trim recorded responses in spikes/out into tests/fixtures.

Keeps real recorded values; only cuts size (fewer rows, shorter series,
fewer keys). The synthetic exception: a match-history file for the second
tracked account (890069947), built from that player's real per-match data
in the recorded metadata, since no history recording exists for them.
"""
import json
import re
from pathlib import Path

OUT = Path(__file__).parent / "out"
FIX = Path(__file__).parent.parent / "tests" / "fixtures"

TRACKED = {891231519, 890069947}
MATCH_ID = 86714494

PLAYER_KEYS = [
    "account_id", "player_slot", "team", "hero_id", "assigned_lane",
    "kills", "deaths", "assists", "net_worth", "last_hits", "denies", "level",
]
STATS_KEYS = [
    "time_stamp_s", "player_damage", "player_healing", "boss_damage",
    "self_healing", "player_damage_taken", "net_worth",
]
ITEM_KEYS = ["game_time_s", "item_id", "upgrade_id", "sold_time_s",
             "flags", "imbued_ability_id", "upgrade_info"]
MATCH_KEYS = [
    "match_id", "start_time", "duration_s", "game_mode", "match_mode",
    "winning_team", "match_outcome", "average_badge_team0", "average_badge_team1",
]


def trim_metadata() -> dict:
    meta = json.load(open(OUT / f"02_match_metadata_{MATCH_ID}.json", encoding="utf-8"))
    mi = meta["match_info"]
    players = []
    for p in mi["players"]:
        tp = {k: p[k] for k in PLAYER_KEYS}
        series = p["stats"]
        tp["stats"] = [
            {k: e[k] for k in STATS_KEYS if k in e}
            for e in ([series[0], series[-1]] if len(series) > 1 else series)
        ]
        n_items = 8 if p["account_id"] in TRACKED else 2
        tp["items"] = [{k: it[k] for k in ITEM_KEYS} for it in p["items"][:n_items]]
        players.append(tp)
    builds = meta["hero_build_ids"]
    if isinstance(builds, dict):
        builds = dict(list(builds.items())[:2])
    else:
        builds = builds[:2]
    return {
        "match_info": {**{k: mi[k] for k in MATCH_KEYS}, "players": players},
        "hero_build_ids": builds,
        "banned_hero_ids": meta["banned_hero_ids"],
    }


def main() -> None:
    # 1. Match history for the primary account: last 3 real rows.
    hist = json.load(open(OUT / "01_match_history_account_id32.json", encoding="utf-8"))
    hist.sort(key=lambda r: r["match_id"])
    recent = hist[-3:]
    json.dump(recent, open(FIX / "match_history_891231519.json", "w", encoding="utf-8"), indent=1)
    print("history 891231519:", [r["match_id"] for r in recent])

    # 2. Trimmed metadata.
    meta = trim_metadata()
    json.dump(meta, open(FIX / f"match_metadata_{MATCH_ID}.json", "w", encoding="utf-8"), indent=1)
    players = meta["match_info"]["players"]
    print("metadata players:", [(p["account_id"], p["hero_id"], p["team"]) for p in players])

    # 3. History for second tracked account: the shared match, real values
    # taken from their player entry in the metadata.
    me = next(r for r in recent if r["match_id"] == MATCH_ID)
    other = next(p for p in players if p["account_id"] == 890069947)
    row = dict(me)
    row.update(
        account_id=other["account_id"], hero_id=other["hero_id"],
        player_team=other["team"], player_kills=other["kills"],
        player_deaths=other["deaths"], player_assists=other["assists"],
        denies=other["denies"], net_worth=other["net_worth"],
        last_hits=other["last_hits"],
    )
    json.dump([row], open(FIX / "match_history_890069947.json", "w", encoding="utf-8"), indent=1)

    # 4. Assets subsets covering exactly what the metadata fixture references.
    heroes = json.load(open(OUT / "06_assets_heroes.json", encoding="utf-8"))
    need_heroes = {p["hero_id"] for p in players}
    hero_subset = [
        {"id": h["id"], "name": h["name"], "class_name": h["class_name"],
         "images": {"icon_hero_card": h.get("images", {}).get("icon_hero_card")},
         "disabled": h.get("disabled"), "player_selectable": h.get("player_selectable")}
        for h in heroes if h["id"] in need_heroes
    ]
    json.dump(hero_subset, open(FIX / "assets_heroes_match.json", "w", encoding="utf-8"), indent=1)

    items = json.load(open(OUT / "06_assets_items.json", encoding="utf-8"))
    purchased = {it["item_id"] for p in players for it in p["items"]}
    subset = [
        {"id": i["id"], "name": i["name"], "class_name": i.get("class_name"),
         "type": i["type"], "item_tier": i.get("item_tier"),
         "item_slot_type": i.get("item_slot_type"), "cost": i.get("cost"),
         "shop_image": i.get("shop_image"), "image": i.get("image"),
         "shopable": i.get("shopable"), "disabled": i.get("disabled")}
        for i in items if i["id"] in purchased and i.get("type") == "upgrade"
    ]
    json.dump(subset, open(FIX / "assets_items_match.json", "w", encoding="utf-8"), indent=1)
    upgrade_ids = {i["id"] for i in subset}
    me_items = next(p for p in players if p["account_id"] == 891231519)["items"]
    print("upgrade items in fixture:", len(subset))
    print("primary player purchases: total", len(me_items),
          "shop", sum(1 for it in me_items if it["item_id"] in upgrade_ids))

    # 5. Steam News: one major, one minor, one hero release, one non-Valve post.
    news = json.load(open(OUT / "04_steam_news.json", encoding="utf-8"))
    posts = news["appnews"]["newsitems"]
    picked = []
    for want in ("Gameplay Update - 05-22-2026", "Minor Update - 06-04-2026", "Apollo - A Cut Above"):
        picked.append(next(p for p in posts if p["title"].startswith(want)))
    non_valve = next((p for p in posts if p["feedname"] != "steam_community_announcements"), None)
    if non_valve:
        picked.append(non_valve)
    for p in picked:
        if len(p["contents"]) > 20000:
            p = dict(p)
        n = len(re.findall(r"\[p\]\s*-\s", p["contents"]))
        print(f"news: {p['title'][:45]!r} feed={p['feedname']} change_lines={n}")
    json.dump({"appnews": {"appid": news["appnews"]["appid"], "newsitems": picked}},
              open(FIX / "steam_news.json", "w", encoding="utf-8"), indent=1)

    # 6. Counter-stats sample: first 8 rows.
    counter = json.load(open(OUT / "03c_counter_default.json", encoding="utf-8"))
    json.dump(counter[:8], open(FIX / "counter_stats.json", "w", encoding="utf-8"), indent=1)
    print("counter rows kept:", 8, "keys:", sorted(counter[0].keys()))


if __name__ == "__main__":
    main()
