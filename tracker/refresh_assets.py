"""CLI: fetch heroes and items from the assets API, archive raw, load into DB.

Usage:
    python -m tracker.refresh_assets <db_path>

Two requests, spaced at least 5 seconds apart (hard rule: 1 req / 5 s to
deadlock-api). Each response is archived in raw_api_responses before parsing
(hard rule 2: archive raw before any parsing).

Requests go through the same ingest.client.Client + TokenBucket the worker uses,
so this CLI and the worker share one HTTP path: one combined rate ceiling (the
deadlock_stamp_path stamp file), one jitter policy, and the same optional
DEADLOCK_API_KEY / DEADLOCK_REQUESTS_PER_SECOND deployment config.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ingest.client import Client
from ingest.ratelimit import TokenBucket
from tracker.config import deadlock_api_key, deadlock_requests_per_second
from tracker.db import connect
from tracker.migrate import migrate
from tracker.paths import deadlock_stamp_path
from tracker.reference import load_abilities, load_heroes, load_items

BASE = "https://api.deadlock-api.com"


def _build_client() -> Client:
    """The same rate-limited, optionally-keyed client the worker builds. An
    invalid DEADLOCK_REQUESTS_PER_SECOND raises here, failing loudly at startup."""
    bucket = TokenBucket(rate=deadlock_requests_per_second(), stamp_path=deadlock_stamp_path())
    return Client(bucket, api_key=deadlock_api_key())


def _get(client: Client, url: str) -> tuple[int, str]:
    print(f"  GET {url}")
    status, _headers, body = client.get(url)
    return status, body


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

    client = _build_client()
    fetched_at = datetime.now(timezone.utc).isoformat()

    heroes_url = f"{BASE}/v1/assets/heroes"
    status, body = _get(client, heroes_url)
    _archive(conn, heroes_url, status, body, fetched_at)
    if status == 200:
        heroes_json = json.loads(body)
        load_heroes(conn, heroes_json, fetched_at)
        print(f"  loaded {len(heroes_json)} heroes")
    else:
        print(f"  heroes request failed: HTTP {status}", file=sys.stderr)
        sys.exit(1)

    items_url = f"{BASE}/v1/assets/items"
    status, body = _get(client, items_url)
    _archive(conn, items_url, status, body, fetched_at)
    if status == 200:
        items_json = json.loads(body)
        upgrades = [i for i in items_json if i.get("type") == "upgrade"]
        abilities = [i for i in items_json if i.get("type") == "ability"]
        load_items(conn, items_json, fetched_at)
        load_abilities(conn, items_json, fetched_at)  # same response, no extra request
        print(f"  loaded {len(upgrades)} shop items and {len(abilities)} abilities"
              f" (out of {len(items_json)} total)")
    else:
        print(f"  items request failed: HTTP {status}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
