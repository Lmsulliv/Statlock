"""Deployment config for talking to deadlock-api.

These live in `tracker` (not `api.config`) so the ingest worker and the
refresh-assets CLI can read them without importing the api package. Like
api.config's accessors, each reads the environment FRESH on every call so a
test can monkeypatch per-test and a deploy needs no code change: a granted API
key or a maintainer-blessed rate is a `.env` edit, nothing more.
"""
import os

# The historical default: 1 request every 5 seconds (hard rule 3). Do NOT raise
# this default -- the ceiling moves only when the deadlock-api maintainers bless
# a higher rate, and then only via DEADLOCK_REQUESTS_PER_SECOND, never here.
DEFAULT_REQUESTS_PER_SECOND = 0.2


def deadlock_api_key() -> str | None:
    """The deadlock-api key from DEADLOCK_API_KEY, or None if unset/blank.

    Optional by design: when None the worker calls the API unauthenticated,
    exactly as it does today. When set, the key rides every deadlock-api request.
    """
    return os.environ.get("DEADLOCK_API_KEY", "").strip() or None


def deadlock_requests_per_second() -> float:
    """Requests-per-second ceiling for deadlock-api, from
    DEADLOCK_REQUESTS_PER_SECOND, defaulting to DEFAULT_REQUESTS_PER_SECOND.

    Blank/unset falls back to the default (Compose's ${VAR:-} injects an empty
    string when the var is absent). A present-but-invalid value -- non-numeric
    or non-positive -- raises ValueError so a typo fails LOUDLY at startup rather
    than silently running at the default.
    """
    raw = os.environ.get("DEADLOCK_REQUESTS_PER_SECOND", "").strip()
    if not raw:
        return DEFAULT_REQUESTS_PER_SECOND
    try:
        rate = float(raw)
    except ValueError:
        raise ValueError(
            f"DEADLOCK_REQUESTS_PER_SECOND must be a positive number, got {raw!r}"
        ) from None
    if rate <= 0:
        raise ValueError(
            f"DEADLOCK_REQUESTS_PER_SECOND must be a positive number, got {raw!r}"
        )
    return rate
