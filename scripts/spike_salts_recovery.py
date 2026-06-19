"""THROWAWAY SPIKE #2: are the 400-"salts" matches actually recoverable?

Spike #1 (scripts/spike_not_parsed.py) concluded the existing 'unavailable'
matches were "genuinely gone". That conclusion was unsafe: I hammered 12
/metadata probes in ~1 minute, but fetching salts for an uncached match routes
through Steam, which deadlock-api rate-limits to **10 req / 30 min per IP**
(GET /v1/matches/{id}/salts, OpenAPI). So most of those 400s were probably the
rate limit, not missing data.

This spike tests recoverability the RIGHT way, within the documented limit:
  - GET /v1/matches/{id}/salts  (Steam fallback ON by default) for a FEW old
    400-salts matches, well spaced -- do salts actually come back?
  - if salts come back, GET /v1/matches/{id}/metadata again -- does it now 200?
  - GET /v1/matches/recently-fetched for context (is the fetch pool live?).

Hard rule 3 still applies: paced through the same TokenBucket the worker uses.
We make only N_SALTS (<=5) Steam-backed calls so we stay well under 10/30min.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.config import db_path
from ingest.client import BASE_URL, Client, NetworkError
from ingest.ratelimit import DEFAULT_STAMP, TokenBucket
from tracker.db import connect

N_SALTS = 4            # Steam-backed calls; MUST stay well under 10 / 30 min

_client = Client(TokenBucket(stamp_path=DEFAULT_STAMP))


def probe(url: str) -> tuple[int, dict, str]:
    print(f"  GET {url}")
    try:
        status, headers, body = _client.get(url)
    except NetworkError as e:
        print(f"    network error: {e}")
        return -1, {}, str(e)
    print(f"    -> {status}  {body[:120].strip()!r}")
    rate_hdrs = {k: v for k, v in headers.items()
                 if any(t in k.lower() for t in ("rate", "retry", "limit"))}
    if rate_hdrs:
        print(f"    rate headers: {rate_hdrs}")
    return status, headers, body


def main() -> None:
    conn = connect(db_path())
    old_ids = [r["match_id"] for r in conn.execute(
        "SELECT match_id FROM fetch_queue WHERE status = 'unavailable'"
        " ORDER BY match_id DESC LIMIT ?", (N_SALTS,))]
    conn.close()

    print(f"testing salts recovery for {len(old_ids)} old matches: {old_ids}")
    print("(Steam salts limit is 10 req / 30 min; we make at most "
          f"{N_SALTS} salts calls.)\n")

    print("=== context: recently-fetched (DB-backed, cheap) ===")
    probe(f"{BASE_URL}/v1/matches/recently-fetched")

    print("\n=== salts fetch (Steam fallback ON) ===")
    results = []
    for mid in old_ids:
        s_status, _h, s_body = probe(f"{BASE_URL}/v1/matches/{mid}/salts")
        rec = {"match_id": mid, "salts_status": s_status, "salts_body": s_body[:200]}
        # If salts came back, see whether metadata now succeeds.
        if s_status == 200:
            m_status, _h2, _m_body = probe(f"{BASE_URL}/v1/matches/{mid}/metadata")
            rec["metadata_after"] = m_status
        results.append(rec)

    print("\n========== SUMMARY ==========")
    for r in results:
        print("   ", r)
    got_salts = [r["match_id"] for r in results if r["salts_status"] == 200]
    print("\nmatches that returned salts:", got_salts or "none")
    print("any explicit 429 / rate-limit response:",
          any(r["salts_status"] == 429 for r in results))


if __name__ == "__main__":
    main()
