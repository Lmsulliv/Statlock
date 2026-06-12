"""Spike 05: what does match-history `match_result` mean? (1 throttled call)

Two candidate readings:
  A: match_result = number of the winning team
  B: match_result = 1 if this player won, else 0
For a row with player_team=0 the readings predict different values whatever
the actual winner, so one metadata fetch settles it.
"""
import json

from _api import OUT, get, save_raw

history = json.loads((OUT / "01_match_history_account_id32.json").read_text(encoding="utf-8"))
row = max((m for m in history if m["player_team"] == 0), key=lambda m: m["start_time"])
print(f"history row: match {row['match_id']}, player_team=0, match_result={row['match_result']}")

status, body = get(f"https://api.deadlock-api.com/v1/matches/{row['match_id']}/metadata")
save_raw(f"05_match_metadata_{row['match_id']}.json", body)
winning_team = json.loads(body)["match_info"]["winning_team"]
print(f"metadata winning_team={winning_team}")

pred_a = winning_team
pred_b = 1 if row["player_team"] == winning_team else 0
print(f"reading A (winning team) predicts {pred_a}; reading B (won flag) predicts {pred_b}")
actual = row["match_result"]
if pred_a != pred_b:
    verdict = "A: match_result = winning team" if actual == pred_a else "B: match_result = won flag"
    print(f"actual {actual} -> {verdict}")
else:
    print("readings coincide here; try another row")
