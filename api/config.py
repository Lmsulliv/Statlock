"""Where the read layer finds the database.

The worker writes to data/tracker.db (ingest/__main__.py DEFAULT_DB); the API
reads the same file. The path is overridable via the DEADLOCK_DB environment
variable so deployments (and tests) can point elsewhere without code changes.
"""
import os
from pathlib import Path

DEFAULT_DB = Path("data") / "tracker.db"


def db_path() -> Path:
    """Resolved database path: DEADLOCK_DB env var if set, else the default.

    Read fresh on every call (not cached at import) so tests can monkeypatch
    the environment per-test and the app picks it up per-request.
    """
    env = os.environ.get("DEADLOCK_DB")
    return Path(env) if env else DEFAULT_DB


# Interim owner gate. There is no real authentication yet; the era-management
# writes (confirm/dismiss candidates) are the app's only write surface, so until
# a login exists we keep them private behind a single deploy-time flag. This is
# authorization-by-config, NOT authentication: there's no identity and no
# session. Replace with a real login before exposing the app publicly.
_OWNER_TRUTHY = {"1", "true", "yes"}


def owner_enabled() -> bool:
    """True when the owner flag (DEADLOCK_OWNER) is set to a truthy value.

    Read fresh on every call (like db_path) so tests can monkeypatch the
    environment per-test and each request re-checks the current value.
    """
    return os.environ.get("DEADLOCK_OWNER", "").strip().lower() in _OWNER_TRUTHY
