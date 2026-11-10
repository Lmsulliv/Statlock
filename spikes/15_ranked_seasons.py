"""Spike 15: ranked seasons and rank systems.

The Jul 30 2026 Matchmaking Update introduced a new rank ladder that reuses the
badge encoding (tier*10 + subtier) with different names: badge 55 was Ritualist 5
before and is Mystic 5 now. Before building a season-aware rank chart we need to
know, from the API and our own archive:

  (1) the exact Jul 30 cutover, from archived Steam news + rank rows either side;
  (2) whether pre-cutover /v1/assets/ranks metadata is archived, and whether the
      ranks table has already been overwritten;
  (3) where the badge art lives now and whether the legacy art is still reachable;
  (4) what mmr-history returns after Jul 30 (Ranked only? calibration? Eternus?);
  (5) the self account's rows around the Beta Season 2 start (Nov 5 2026);
  (6) the latest mmr-history row vs. the in-game rank (Sentinel V = badge 45).

Part A reads data/tracker.db READ-ONLY (mode=ro) -- no network. Part B (--live)
makes six requests through the same Client + TokenBucket construction the worker
uses (shared deadlock_stamp_path), so the 1 req / 5 s budget holds across
processes. Raw bodies are saved to spikes/out/15_*; nothing is written to the app
DB. Findings are transcribed into docs/api-findings.md (hard rule 6).

    py -3.14 spikes/15_ranked_seasons.py          # Part A only
    py -3.14 spikes/15_ranked_seasons.py --live   # Part A + Part B
"""
import hashlib
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from ingest.client import BASE_URL, USER_AGENT, Client  # noqa: E402
from ingest.ratelimit import TokenBucket  # noqa: E402
from tracker import rawstore  # noqa: E402
from tracker.config import deadlock_api_key, deadlock_requests_per_second  # noqa: E402
from tracker.paths import deadlock_stamp_path  # noqa: E402

DB_PATH = REPO / "data" / "tracker.db"
OUT = Path(__file__).resolve().parent / "out"
ME = 891231519
ART = "https://assets-bucket.deadlock-api.com/assets-api-res/images/ranks"

# Candidate boundaries under test (unix seconds).
MATCHMAKING_POST = 1785438877   # 2026-07-30T19:14:37Z, "Matchmaking Update"
S2_POST = 1793908694            # 2026-11-05T19:58:14Z, "Ranked - Beta Season 2"
S2_PDT = 1793912400             # 2026-11-05T21:00:00Z, "2pm PDT" read literally
S2_PST = 1793916000             # 2026-11-05T22:00:00Z, 2pm Pacific (PST in effect)


def utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def ro_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def archived_body(conn, response_id: int):
    return json.loads(conn.execute(
        "SELECT body FROM raw_api_responses WHERE id = ?", (response_id,)).fetchone()[0])


# ── Part A: the archive (read-only) ──────────────────────────────────────────

def part_a() -> None:
    conn = ro_connect()

    print("[A1] archived /v1/assets/ranks responses (names of tiers 3-7, tier-5 art)")
    for r in conn.execute(
            "SELECT id, fetched_at FROM raw_api_responses"
            " WHERE url LIKE '%/v1/assets/ranks' AND status_code = 200 ORDER BY fetched_at"):
        body = archived_body(conn, r["id"])
        names = [t["name"] for t in body if 3 <= t["tier"] <= 7]
        print(f"  {r['id']:>5} {r['fetched_at'][:19]} {names} {body[5]['images']['large']}")

    print("\n[A2] live ranks table")
    for r in conn.execute("SELECT tier, name, color, fetched_at FROM ranks ORDER BY tier"):
        print(f"  {r['tier']:>2} {r['name']:<10} {r['color']} {r['fetched_at'][:19]}")

    print("\n[A3] rank rows either side of the Matchmaking Update post")
    cut = utc(MATCHMAKING_POST)
    print("  last legacy row:   ", tuple(conn.execute(
        "SELECT MAX(recorded_at) FROM account_rank_history WHERE recorded_at < ?", (cut,)).fetchone()))
    print("  first seasonal row:", tuple(conn.execute(
        "SELECT MIN(recorded_at) FROM account_rank_history WHERE recorded_at >= ?", (cut,)).fetchone()))

    print("\n[A4] self mmr-history archive sizes (legacy rows dropped after the cutover?)")
    for r in conn.execute(
            "SELECT id, fetched_at, body FROM raw_api_responses"
            " WHERE url LIKE ? AND status_code = 200 ORDER BY fetched_at",
            (f"%/{ME}/mmr-history",)):
        rows = json.loads(r["body"])
        span = (utc(min(x["start_time"] for x in rows))[:10],
                utc(max(x["start_time"] for x in rows))[:10]) if rows else ()
        print(f"  {r['id']:>5} {r['fetched_at'][:19]} rows={len(rows):>4} span={span}")

    print("\n[A5] self matches since the cutover: match_mode vs having a rank row")
    tally = Counter()
    for r in conn.execute(
            "SELECT v.match_id, m.raw_json,"
            " EXISTS (SELECT 1 FROM account_rank_history h"
            "         WHERE h.account_id = v.account_id AND h.match_id = v.match_id) AS ranked"
            " FROM v_account_matches v LEFT JOIN matches m ON m.match_id = v.match_id"
            " WHERE v.account_id = ? AND v.start_time >= ?", (ME, cut)):
        mode = None
        if r["raw_json"]:
            meta = json.loads(rawstore.load(r["raw_json"]))
            mode = meta.get("match_info", meta).get("match_mode")
        tally[(mode, bool(r["ranked"]))] += 1
    print("  (match_mode, has_rank_row) ->", dict(tally))

    print("\n[A6] self match-history ranked_* fields on Ranked (match_mode 4) games")
    _print_ranked_games(json.loads(conn.execute(
        "SELECT body FROM raw_api_responses WHERE url LIKE ? AND status_code = 200"
        " ORDER BY fetched_at DESC LIMIT 1", (f"%/{ME}/match-history",)).fetchone()[0]), conn)


def _print_ranked_games(history: list[dict], conn=None, since: int = MATCHMAKING_POST) -> None:
    stored = {}
    if conn is not None:
        stored = {r["match_id"]: r["badge"] for r in conn.execute(
            "SELECT match_id, badge FROM account_rank_history WHERE account_id = ?", (ME,))}
    for x in sorted(history, key=lambda x: x["start_time"]):
        if x["start_time"] >= since and x.get("match_mode") == 4:
            print(f"  {x['match_id']} {utc(x['start_time'])[:19]}"
                  f" calib={x.get('ranked_calibration_match')}"
                  f" display_badge={x.get('ranked_display_badge')}"
                  f" delta={x.get('ranked_delta')} stored={stored.get(x['match_id'])}")


# ── Part B: live probes ──────────────────────────────────────────────────────

def _bucket() -> TokenBucket:
    # Rebuilt before every request: a TokenBucket reads the shared stamp only at
    # construction, so a fresh one honors any request another process (the
    # worker) made since -- the 5 s spacing holds across processes, not just here.
    return TokenBucket(rate=deadlock_requests_per_second(), stamp_path=deadlock_stamp_path())


def get_json(name: str, url: str):
    status, _headers, body = Client(_bucket(), api_key=deadlock_api_key()).get(url)
    (OUT / name).write_text(body, encoding="utf-8")
    print(f"  GET {url} -> HTTP {status} ({len(body)} bytes) saved out/{name}")
    return json.loads(body) if status == 200 else None


def get_bytes(name: str, url: str) -> None:
    # Client.get decodes UTF-8, which a PNG isn't, so images take the same
    # bucket but read raw bytes.
    _bucket().acquire()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            status, ctype, data = resp.status, resp.headers.get("Content-Type"), resp.read()
    except urllib.error.HTTPError as e:
        status, ctype, data = e.code, e.headers.get("Content-Type"), e.read()
    (OUT / name).write_bytes(data)
    print(f"  GET {url} -> HTTP {status} {ctype} {len(data)} bytes"
          f" sha256={hashlib.sha256(data).hexdigest()[:16]} saved out/{name}")


def part_b() -> None:
    # Run 2026-11-10: /v1/openapi.json, mmr-history and mmr/distribution all
    # returned 404 -- the mmr endpoints were renamed to /v1/players/{id}/rank and
    # /v1/players/rank/distribution, which return the LATEST rank only. The spec
    # lives at /openapi.json (spike 03's fallback). B2/B5 are kept as the record
    # of that 404; B6 was added after reading the new spec.
    print("\n[B1] OpenAPI: params on /v1/assets/ranks and mmr-history now")
    spec = get_json("15_openapi.json", f"{BASE_URL}/openapi.json")
    if spec:
        for path, ops in spec["paths"].items():
            if "rank" in path or "mmr" in path:
                params = [p["name"] for p in ops.get("get", {}).get("parameters", [])]
                print(f"    {path} {params}")

    print("\n[B2] self mmr-history")
    rows = get_json("15_mmr_history.json", f"{BASE_URL}/v1/players/{ME}/mmr-history") or []
    if rows:
        print(f"    rows={len(rows)} span={utc(min(r['start_time'] for r in rows))[:19]}"
              f" .. {utc(max(r['start_time'] for r in rows))[:19]}")
        print(f"    encoding mismatches: "
              f"{sum(r['rank'] != r['division'] * 10 + r['division_tier'] for r in rows)}")
        for r in sorted(rows, key=lambda r: r["start_time"]):
            if r["start_time"] >= S2_POST - 7 * 86400:
                print(f"    {r['match_id']} {utc(r['start_time'])[:19]} rank={r['rank']}")
        print(f"    latest: {max(rows, key=lambda r: r['start_time'])}  (in-game: 45)")

    print("\n[B3] self match-history: Ranked games around the S2 start")
    history = get_json("15_match_history.json", f"{BASE_URL}/v1/players/{ME}/match-history") or []
    _print_ranked_games(history, since=S2_POST - 7 * 86400)
    in_window = [x for x in history if S2_POST <= x["start_time"] < S2_PST and x.get("match_mode") == 4]
    print(f"    Ranked games between the S2 post and 22:00 UTC: {[x['match_id'] for x in in_window]}")

    print("\n[B4] badge art: legacy path vs seasonal path (tier 5)")
    get_bytes("15_rank5_badge_lg.png", f"{ART}/rank5/badge_lg.png")
    get_bytes("15_rank05_lg.png", f"{ART}/rank05_lg.png")

    print("\n[B5] seasonal rank distribution (Eternus encoding)")
    dist = get_json("15_mmr_distribution.json",
                    f"{BASE_URL}/v1/players/mmr/distribution?min_unix_timestamp={MATCHMAKING_POST}")
    if dist:
        ranks = sorted(d["rank"] for d in dist)
        print(f"    {len(ranks)} distinct ranks: {ranks}")

    print("\n[B6] ranked-seasons asset: the API's own season windows")
    for season in get_json("15_ranked_seasons.json", f"{BASE_URL}/v1/assets/ranked-seasons") or []:
        for interval in season["intervals"]:
            print(f"    {season['name']}: {utc(interval['start_timestamp'])}"
                  f" -> {utc(interval['end_timestamp'])}"
                  f" (calibration_matches={season['calibration_matches']})")


if __name__ == "__main__":
    part_a()
    if "--live" in sys.argv:
        part_b()
