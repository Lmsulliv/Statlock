"""Spike 03b: offline dig into the cached OpenAPI spec (no API calls).

Pulls the details the live probes need: the `bucket` enum, badge-param
descriptions, response schemas for hero-counter-stats and item-stats, and
the account_id path-param description (does it document SteamID64 support?).
"""
import json

from _api import OUT

spec = json.loads((OUT / "03_openapi.json").read_text(encoding="utf-8"))
paths = spec["paths"]


def params_of(path):
    return paths[path]["get"].get("parameters", [])


print("=== bucket param on /v1/analytics/hero-stats ===")
for p in params_of("/v1/analytics/hero-stats"):
    if p["name"] == "bucket":
        print(json.dumps(p, indent=2))

print("\n=== badge params on /v1/analytics/hero-counter-stats ===")
for p in params_of("/v1/analytics/hero-counter-stats"):
    if "badge" in p["name"] or "timestamp" in p["name"]:
        print(json.dumps(p, indent=2))

print("\n=== account_id param on match-history ===")
for p in params_of("/v1/players/{account_id}/match-history"):
    print(json.dumps(p, indent=2))

print("\n=== response schema names referenced by key analytics endpoints ===")
for path in ("/v1/analytics/hero-counter-stats", "/v1/analytics/item-stats",
             "/v1/patches", "/v1/patches/big-days", "/v2/patches",
             "/v1/players/{account_id}/mmr-history", "/v1/assets/ranks"):
    if path not in paths:
        print(f"  {path}: NOT IN SPEC")
        continue
    resp = paths[path]["get"].get("responses", {}).get("200", {})
    content = resp.get("content", {})
    for ctype, c in content.items():
        print(f"  {path} -> {ctype}: {json.dumps(c.get('schema', {}))[:300]}")

print("\n=== schemas that look relevant ===")
schemas = spec.get("components", {}).get("schemas", {})
for name in schemas:
    if any(s in name.lower() for s in ("counter", "itemstat", "patch", "rank", "mmr")):
        print(f"\n--- {name} ---")
        print(json.dumps(schemas[name], indent=1)[:1500])
