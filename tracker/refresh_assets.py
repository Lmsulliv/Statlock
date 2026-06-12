"""CLI: fetch heroes and items from the assets API, archive raw, load into DB.

Usage:
    python -m tracker.refresh_assets <db_path>

Two requests, spaced at least 5 seconds apart (hard rule: 1 req / 5 s to
deadlock-api). Each response is archived in raw_api_responses before parsing
(hard rule 2: archive raw before any parsing).
"""
import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from tracker.db import connect
from tracker.migrate import migrate
from tracker.reference import load_heroes, load_items

BASE = "https://api.deadlock-api.com"
_UA  = "deadlock-stat-tracker/0.1 (personal project)"
_MIN_INTERVAL_S = 5.0
_STAMP = Path(__file__).parent.parent / "data" / ".last_deadlock_request"


def _wait_for_slot() -> None:
    """Block until 5 s have elapsed since the last request (cross-run via disk stamp)."""
    try:
        last = float(_STAMP.read_text())
    except (FileNotFoundError, ValueError):
        last = 0.0
    wait = _MIN_INTERVAL_S - (time.time() - last)
    if wait > 0:
        print(f"  rate-limit: sleeping {wait:.1f}s …")
        time.sleep(wait)
    # Jitter: 0–2 extra seconds to avoid perfectly periodic traffic.
    jitter = random.uniform(0, 2)
    time.sleep(jitter)
    _STAMP.write_text(str(time.time()))


def _get(url: str) -> tuple[int, str]:
    _wait_for_slot()
    print(f"  GET {url}")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def _archive(conn, url: str, status: int, body: str, fetched_at: str) -> None:
    conn.execute(
        "INSERT INTO raw_api_responses(url,status_code,body,fetched_at) VALUES(?,?,?,?)",
        (url, status, body, fetched_at),
    )
    conn.commit()


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m tracker.refresh_assets <db_path>", file=sys.stderr)
        sys.exit(1)

    db_path = Path(sys.argv[1])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    migrate(conn)

    fetched_at = datetime.now(timezone.utc).isoformat()
    _STAMP.parent.mkdir(parents=True, exist_ok=True)

    heroes_url = f"{BASE}/v1/assets/heroes"
    status, body = _get(heroes_url)
    _archive(conn, heroes_url, status, body, fetched_at)
    if status == 200:
        heroes_json = json.loads(body)
        load_heroes(conn, heroes_json, fetched_at)
        print(f"  loaded {len(heroes_json)} heroes")
    else:
        print(f"  heroes request failed: HTTP {status}", file=sys.stderr)
        sys.exit(1)

    items_url = f"{BASE}/v1/assets/items"
    status, body = _get(items_url)
    _archive(conn, items_url, status, body, fetched_at)
    if status == 200:
        items_json = json.loads(body)
        upgrades = [i for i in items_json if i.get("type") == "upgrade"]
        load_items(conn, items_json, fetched_at)
        print(f"  loaded {len(upgrades)} shop items (out of {len(items_json)} total)")
    else:
        print(f"  items request failed: HTTP {status}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
