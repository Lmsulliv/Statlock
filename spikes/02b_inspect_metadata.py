"""Spike 02b: offline inspection of the archived metadata (no API calls).

Answers what spike 02's summary left open: per-player badge/party, the
per-player stats time series content, and whether any game build/version
field exists anywhere in the response.
"""
import json
import re

from _api import OUT

raw = next(OUT.glob("02_match_metadata_*.json")).read_text(encoding="utf-8")
d = json.loads(raw)
mi = d["match_info"]
p = mi["players"][0]

print("player keys:", sorted(p.keys()))
print("\nstats[0] (time-series sample):")
print(json.dumps(p["stats"][0], indent=1)[:2000])

print("\nkey-name search across entire response:")
for needle in ("badge", "party", "build", "version", "patch", "rank",
               "damage", "healing"):
    keys = sorted(set(re.findall(r'"(\w*%s\w*)":' % needle, raw)))
    print(f"  {needle} -> {keys}")
