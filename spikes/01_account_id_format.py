"""Spike 01: which account_id format does deadlock-api expect?

Tries match history with the 32-bit account ID and the SteamID64 and
reports what each returns. The successful response is archived so spike 02
can pick the most recent match ID from it.
"""
import json

from _api import get, save_raw

ACCOUNT_ID_32 = 891231519
STEAMID64 = 76561198851497247
STEAM_ID_OFFSET = 76561197960265728  # steamid64 - this = 32-bit account id

results = {}
for label, ident in [("account_id32", ACCOUNT_ID_32), ("steamid64", STEAMID64)]:
    url = f"https://api.deadlock-api.com/v1/players/{ident}/match-history"
    status, body = get(url)
    save_raw(f"01_match_history_{label}.json", body)
    print(f"  {label}: HTTP {status}, {len(body)} bytes")
    try:
        results[label] = json.loads(body)
    except json.JSONDecodeError:
        print(f"  non-JSON body, first 300 chars: {body[:300]}")
        results[label] = None

print()
print(f"sanity check: {STEAMID64} - {STEAM_ID_OFFSET} = {STEAMID64 - STEAM_ID_OFFSET}")

for label, data in results.items():
    if isinstance(data, list):
        print(f"\n{label}: list of {len(data)} entries")
        if data:
            print("most recent entry:")
            print(json.dumps(data[0], indent=2))
    elif data is not None:
        print(f"\n{label}: {type(data).__name__}: {json.dumps(data)[:500]}")

a, b = results.get("account_id32"), results.get("steamid64")
if isinstance(a, list) and isinstance(b, list):
    print(f"\nboth succeeded; identical content: {a == b}")
