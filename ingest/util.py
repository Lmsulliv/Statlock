"""Small time helpers shared across the worker.

Every loop takes `now` as an injectable callable so tests can control time;
these are the production defaults.
"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def unix_to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def iso_to_unix(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())
