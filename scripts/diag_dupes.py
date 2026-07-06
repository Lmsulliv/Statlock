# Read-only: finds stored match payloads with duplicate account_ids.
# Reads matches.raw_json (THE archive now) via tracker.rawstore, since the old
# raw_api_responses copies of successful metadata are pruned.
import json, collections
from tracker.db import connect
from tracker import rawstore
from api.config import db_path

conn = connect(db_path())
rows = conn.execute("SELECT match_id, raw_json FROM matches").fetchall()

hits = 0
for r in rows:
    body = rawstore.load(r["raw_json"])
    if not body:
        continue
    try:
        players = (json.loads(body).get("match_info") or {}).get("players") or []
    except Exception:
        continue
    if not players:
        continue
    ids = [p.get("account_id") for p in players]
    dupes = {k: v for k, v in collections.Counter(ids).items() if v > 1}
    if dupes:
        hits += 1
        print(f"{r['match_id']}: {len(players)} players, dup account_ids: {dupes}")

print(f"\n{hits} stored match payloads contain duplicate account_ids.")
