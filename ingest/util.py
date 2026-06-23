"""Small time helpers shared across the worker.

Every loop takes `now` as an injectable callable so tests can control time;
these are the production defaults.
"""
from datetime import datetime, timezone

# The default/local user id (the first user, seeded by migration 011). Until real
# auth exists (Phase 2), every read and write resolves to this single user, so the
# app behaves exactly as it did when "self" was global. Phase 2 introduces a
# session-backed dependency that overrides this default per request.
DEFAULT_USER_ID = 1


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def unix_to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def iso_to_unix(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())
