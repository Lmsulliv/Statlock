# Read-only: finds archived match payloads with duplicate account_ids.
import json, collections
from tracker.db import connect
from api.config import db_path

conn = connect(db_path())
rows = conn.execute(
    "SELECT url, body FROM raw_api_responses WHERE status_code = 200"
).fetchall()

hits = 0
for r in rows:
    try:
        players = (json.loads(r["body"]).get("match_info") or {}).get("players") or []
    except Exception:
        continue
    if not players:
        continue
    ids = [p.get("account_id") for p in players]
    dupes = {k: v for k, v in collections.Counter(ids).items() if v > 1}
    if dupes:
        hits += 1
        print(f"{r['url']}: {len(players)} players, dup account_ids: {dupes}")

print(f"\n{hits} archived match payloads contain duplicate account_ids.")