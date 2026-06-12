"""Spike 02: which fields does the match metadata endpoint actually populate?

Reads the most recent match ID from spike 01's archived match history (no
hardcoded match ID), fetches its metadata, archives it raw, then reports on
every field the data model cares about.
"""
import json

from _api import OUT, get, save_raw

history = json.loads((OUT / "01_match_history_account_id32.json").read_text(encoding="utf-8"))
if not isinstance(history, list) or not history:
    raise SystemExit(f"unexpected history shape: {type(history).__name__}")

most_recent = max(history, key=lambda m: m.get("start_time", 0))
match_id = most_recent["match_id"]
print(f"most recent match: {match_id} (start_time {most_recent.get('start_time')})")

status, body = get(f"https://api.deadlock-api.com/v1/matches/{match_id}/metadata")
save_raw(f"02_match_metadata_{match_id}.json", body)
print(f"HTTP {status}, {len(body)} bytes")
data = json.loads(body)


def tree(obj, prefix="", depth=0, max_depth=4):
    """Print the key structure with types; lists show their first element."""
    pad = "  " * depth
    if depth > max_depth:
        print(f"{pad}{prefix}: ...")
        return
    if isinstance(obj, dict):
        print(f"{pad}{prefix}: dict({len(obj)})")
        for k, v in obj.items():
            tree(v, k, depth + 1, max_depth)
    elif isinstance(obj, list):
        print(f"{pad}{prefix}: list[{len(obj)}]")
        if obj:
            tree(obj[0], "[0]", depth + 1, max_depth)
    else:
        val = repr(obj)
        if len(val) > 60:
            val = val[:60] + "..."
        print(f"{pad}{prefix}: {type(obj).__name__} = {val}")


print("\n=== response structure ===")
tree(data, "root")

# Drill into the first player record in full, since per-player nullable
# columns (lane, denies, item purchase timestamps) are the main question.
match_info = data.get("match_info", data)
players = match_info.get("players") or []
print(f"\n=== players: {len(players)} ===")
if players:
    print("first player, full dump:")
    print(json.dumps(players[0], indent=2, default=str)[:6000])

print("\n=== match-level fields of interest ===")
for key in ("match_id", "start_time", "duration_s", "game_mode", "match_mode",
            "winning_team", "game_build", "game_version", "average_badge",
            "average_badge_team0", "average_badge_team1"):
    print(f"  {key}: {match_info.get(key, '<absent>')}")
