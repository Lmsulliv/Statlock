"""Spike 03d: follow-up probes after 03c's item-stats 400 (3 throttled calls).

item-stats has no avg_badge bucket, so badge scoping there means one call
per bracket via min/max_average_badge. hero-stats DOES have bucket=avg_badge;
confirm it returns one row per badge level.
"""
import json
import time

from _api import get, save_raw

BASE = "https://api.deadlock-api.com/v1"
now = int(time.time())
DAY = 86400
since = now - 7 * DAY


def get_json(name, url):
    status, body = get(url)
    save_raw(name, body)
    data = json.loads(body) if status == 200 else None
    print(f"  HTTP {status}, entries: {len(data) if isinstance(data, list) else body[:200]}")
    return data


print("--- D1: item-stats, hero 7, last 7d, no bucket ---")
d1 = get_json("03d_item_stats_hero7.json",
              f"{BASE}/analytics/item-stats?hero_id=7&min_unix_timestamp={since}")
if d1:
    total = sum(r["matches"] for r in d1)
    print(f"rows={len(d1)}, sum(matches)={total}")
    print("sample row:", json.dumps(d1[0], indent=1))

print("\n--- D2: same, badge 80-89 only (Oracle bracket) ---")
d2 = get_json("03d_item_stats_hero7_oracle.json",
              f"{BASE}/analytics/item-stats?hero_id=7&min_unix_timestamp={since}"
              f"&min_average_badge=80&max_average_badge=89")
if d2:
    total = sum(r["matches"] for r in d2)
    print(f"rows={len(d2)}, sum(matches)={total}")

print("\n--- E2: hero-stats, bucket=avg_badge, last 7d ---")
e2 = get_json("03d_hero_stats_badge_bucket.json",
              f"{BASE}/analytics/hero-stats?bucket=avg_badge&min_unix_timestamp={since}")
if e2:
    buckets = sorted({r.get("bucket") for r in e2})
    print(f"rows={len(e2)}, distinct buckets ({len(buckets)}): {buckets}")
    print("sample row:", json.dumps(e2[0], indent=1)[:900])
