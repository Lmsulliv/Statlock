"""Spike 04: Steam News API output for Deadlock, to calibrate era-candidate scoring.

One call to GetNewsForApp (not deadlock-api, so not under its rate limit).
Prints title/date/line-count for each post so a known major and minor patch
can be picked out and recorded in the findings.
"""
import json
import time

from _api import get, save_raw

APP_ID = 1422450  # Deadlock
URL = (f"https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
       f"?appid={APP_ID}&count=30&maxlength=0")

status, body = get(URL, throttle=False)
print(f"HTTP {status}, {len(body)} bytes")
save_raw("04_steam_news.json", body)

data = json.loads(body)
appnews = data.get("appnews", {})
items = appnews.get("newsitems", [])
print(f"appid in response: {appnews.get('appid')}, items: {len(items)}\n")

for i, item in enumerate(items):
    contents = item.get("contents", "")
    # rough change-line proxy: non-empty lines in the post body
    lines = [ln for ln in contents.splitlines() if ln.strip()]
    date = time.strftime("%Y-%m-%d", time.gmtime(item.get("date", 0)))
    print(f"[{i:2}] {date}  lines={len(lines):4}  chars={len(contents):6}  "
          f"feed={item.get('feedlabel', '?')}  title={item.get('title', '')[:70]}")

if items:
    print("\n=== field names on a news item ===")
    print(", ".join(items[0].keys()))
    print("\n=== first 800 chars of most recent post body ===")
    print(items[0].get("contents", "")[:800])
