"""Spike 03, stage 1: discover the analytics endpoints from the OpenAPI spec.

Fetches and caches the OpenAPI document, then lists every analytics-related
path with its query parameters, so stage-2 live probes (added after reading
this output) use real parameter names instead of guesses.
"""
import json

from _api import OUT, get, save_raw

SPEC_FILE = OUT / "03_openapi.json"

if SPEC_FILE.exists():
    spec = json.loads(SPEC_FILE.read_text(encoding="utf-8"))
    print("using cached OpenAPI spec")
else:
    for url in ("https://api.deadlock-api.com/v1/openapi.json",
                "https://api.deadlock-api.com/openapi.json"):
        status, body = get(url)
        print(f"HTTP {status} for {url}")
        if status == 200:
            save_raw("03_openapi.json", body)
            spec = json.loads(body)
            break
    else:
        raise SystemExit("could not fetch OpenAPI spec from either path")

print(f"\nAPI title: {spec.get('info', {}).get('title')}, version: {spec.get('info', {}).get('version')}")

paths = spec.get("paths", {})
print(f"total paths: {len(paths)}")

print("\n=== analytics paths and their query params ===")
for path, ops in sorted(paths.items()):
    if "analytics" not in path:
        continue
    for method, op in ops.items():
        if method != "get":
            continue
        params = op.get("parameters", [])
        names = []
        for p in params:
            schema = p.get("schema", {})
            typ = schema.get("type") or schema.get("$ref", "?")
            names.append(f"{p.get('name')}:{typ}")
        print(f"\nGET {path}")
        print(f"  summary: {op.get('summary', '')}")
        print(f"  params: {', '.join(names) if names else '(none)'}")

print("\n=== all other GET paths (for the findings doc) ===")
for path, ops in sorted(paths.items()):
    if "analytics" in path or "get" not in ops:
        continue
    print(f"  {path}")
