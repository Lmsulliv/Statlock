"""Spike 04b: offline look at one major and one minor Valve patch post.

The contents field is HTML on one line, so newline counts are useless.
Check what markup separates change lines (<br>? <li>?) and compute counts
for one Gameplay Update and one Minor Update as scoring calibration anchors.
"""
import json
import re

from _api import OUT

data = json.loads((OUT / "04_steam_news.json").read_text(encoding="utf-8"))
items = data["appnews"]["newsitems"]

valve = [i for i in items if i["feedname"] == "steam_community_announcements"
         or i["feedlabel"] == "Community Announcements"]
major = next(i for i in valve if i["title"].startswith("Gameplay Update"))
minor = next(i for i in valve if i["title"].startswith("Minor Update"))

for label, item in (("MAJOR", major), ("MINOR", minor)):
    c = item["contents"]
    print(f"=== {label}: {item['title']} ({item['date']}) ===")
    print(f"url: {item['url']}")
    print(f"chars: {len(c)}")
    for tag in ("<br>", "<br/>", "<br />", "<li>", "[*]", "\n"):
        print(f"  count {tag!r}: {c.count(tag)}")
    # candidate heuristic: split on <br> variants, keep lines starting with "- "
    parts = re.split(r"<br\s*/?>", c)
    dash_lines = [p for p in parts if p.strip().startswith("- ")]
    print(f"  segments after <br> split: {len(parts)}, of which '- ' change lines: {len(dash_lines)}")
    print(f"  first 600 chars:\n{c[:600]}\n")
