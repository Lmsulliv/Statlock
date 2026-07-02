"""Spike 12: per-player rank ingestion design probe.

The Overview "Rank over time" chart is empty because nothing populates
account_rank_history. Before building rank ingestion we need to know the live
response shapes, since they decide the storage/query design:

- Does each mmr-history row carry its OWN timestamp, or only a match_id?
- How is rank encoded (rank vs division/division_tier; range 0..116)?
- Is there a clean CURRENT-rank endpoint (more reliable than the series tail)?
- How sparse / reliable is the series?

The OpenAPI dump (out/03_openapi.json) already shows the candidate endpoints and
their schemas; this script confirms them against live data for a tracked account.

Hard rule: <= 1 request / 5 s (enforced on disk by spikes/_api.py). Run:
    python spikes/12_rank_history.py [account_id]
Default account is the tracked self account.
"""
import json
import sqlite3
import sys
import urllib.parse
from pathlib import Path

import _api

BASE = "https://api.deadlock-api.com"
DB = Path(__file__).parent.parent / "data" / "tracker.db"


def _self_account() -> int:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT account_id FROM tracked_accounts ORDER BY is_self DESC, account_id LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        raise SystemExit("no tracked accounts in data/tracker.db")
    return row["account_id"]


def _probe(name: str, url: str) -> object | None:
    status, body = _api.get(url)
    _api.save_raw(name, body)
    print(f"  HTTP {status}  ({len(body)} bytes)")
    if status != 200:
        print(f"  !! non-200; body head: {body[:200]!r}")
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        print(f"  !! not JSON: {e}")
        return None


def main() -> None:
    account_id = int(sys.argv[1]) if len(sys.argv) > 1 else _self_account()
    print(f"probing rank endpoints for account {account_id}\n")

    # 1. The per-match rank series.
    print(f"[1] GET /v1/players/{account_id}/mmr-history")
    hist = _probe("12_mmr_history.json", f"{BASE}/v1/players/{account_id}/mmr-history")
    if isinstance(hist, list) and hist:
        print(f"  rows: {len(hist)}")
        print(f"  first row: {hist[0]}")
        print(f"  last  row: {hist[-1]}")
        keys = sorted(hist[0].keys())
        print(f"  keys: {keys}")
        # Are start_time and match_id always present / monotonic?
        have_ts = sum(1 for r in hist if r.get("start_time"))
        have_mid = sum(1 for r in hist if r.get("match_id"))
        ranks = [r.get("rank") for r in hist if r.get("rank") is not None]
        print(f"  rows with start_time: {have_ts}/{len(hist)}; with match_id: {have_mid}/{len(hist)}")
        if ranks:
            print(f"  rank range: {min(ranks)}..{max(ranks)}")
        # Cross-check rank vs division*10+division_tier on a few rows.
        for r in hist[:3]:
            d, dt, rk = r.get("division"), r.get("division_tier"), r.get("rank")
            print(f"    div={d} div_tier={dt} rank={rk}  -> div*10+div_tier={None if d is None else d*10+dt}")

    # 2. Current-rank candidate A: PlayerCard (ranked_badge_level/rank/subrank).
    print(f"\n[2] GET /v1/players/{account_id}/card")
    card = _probe("12_card.json", f"{BASE}/v1/players/{account_id}/card")
    if isinstance(card, dict):
        print(f"  ranked_badge_level={card.get('ranked_badge_level')} "
              f"ranked_rank={card.get('ranked_rank')} ranked_subrank={card.get('ranked_subrank')}")

    # 3. Current-rank candidate B: Batch MMR (latest row per account).
    print(f"\n[3] GET /v1/players/mmr?account_ids={account_id}")
    q = urllib.parse.urlencode({"account_ids": account_id})
    batch = _probe("12_batch_mmr.json", f"{BASE}/v1/players/mmr?{q}")
    if isinstance(batch, list):
        print(f"  rows: {len(batch)}")
        if batch:
            print(f"  row: {batch[0]}")


if __name__ == "__main__":
    main()
