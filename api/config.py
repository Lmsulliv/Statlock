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
