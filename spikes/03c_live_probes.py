"""Spike 03c: live analytics probes (6 throttled calls).

Verifies that date-range and badge filters actually narrow the data, that
bucket=avg_badge yields per-badge-level rows, and grabs the rank ladder and
big-patch-days lists for the era work.
"""
import json
import time

from _api import get, save_raw

BASE = "https://api.deadlock-api.com/v1"
now = int(time.time())
DAY = 86400


def get_json(name, url):
    status, body = get(url)
    save_raw(name, body)
    data = json.loads(body) if status == 200 else None
    print(f"  HTTP {status}, entries: {len(data) if isinstance(data, list) else 'n/a'}")
    return data


print("--- A: hero-counter-stats, defaults (30-day window) ---")
a = get_json("03c_counter_default.json", f"{BASE}/analytics/hero-counter-stats")

print("--- B: same, last 7 days only ---")
b = get_json("03c_counter_7d.json",
             f"{BASE}/analytics/hero-counter-stats?min_unix_timestamp={now - 7 * DAY}")

print("--- C: same 7 days, badge 110-116 (Eternus only) ---")
c = get_json("03c_counter_7d_eternus.json",
             f"{BASE}/analytics/hero-counter-stats?min_unix_timestamp={now - 7 * DAY}"
             f"&min_average_badge=110&max_average_badge=116")

for label, data in (("A default 30d", a), ("B last 7d", b), ("C 7d Eternus", c)):
    if data:
        total = sum(r["matches_played"] for r in data)
        print(f"{label}: rows={len(data)}, sum(matches_played)={total}")
if a:
    print("sample row:", json.dumps(a[0], indent=1))

print("\n--- D: item-stats, hero 7, bucket=avg_badge, last 7 days ---")
d = get_json("03c_item_stats_badge_bucket.json",
             f"{BASE}/analytics/item-stats?bucket=avg_badge&hero_id=7"
             f"&min_unix_timestamp={now - 7 * DAY}")
if d:
    buckets = sorted({r["bucket"] for r in d})
    print(f"distinct buckets ({len(buckets)}): {buckets}")
    print("sample row:", json.dumps(d[0], indent=1))

print("\n--- E: patches/big-days ---")
e = get_json("03c_big_days.json", f"{BASE}/patches/big-days")
if e:
    print("big days:", e)

print("\n--- F: assets/ranks (badge encoding) ---")
f = get_json("03c_ranks.json", f"{BASE}/assets/ranks")
if f:
    for r in f:
        print(f"  tier {r['tier']}: {r['name']}")
