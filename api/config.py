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


# Authentication is opt-in via DEADLOCK_BASE_URL. It must be the app's public
# origin (e.g. https://stats.example.com) because Steam OpenID redirects back to
# <base>/api/auth/callback and scopes the login to the <base>/ realm. When unset
# (local/dev) auth is OFF: the app runs as the single default user and the write
# endpoints are open -- the same single-user workflow as before. When set, login
# is required and the cookie-based writes are CSRF-protected.
def base_url() -> str | None:
    """The app's public origin (DEADLOCK_BASE_URL), trailing slash trimmed, or None.

    Read fresh on every call (like db_path) so tests can monkeypatch it per-test.
    """
    raw = os.environ.get("DEADLOCK_BASE_URL", "").strip().rstrip("/")
    return raw or None


def auth_enabled() -> bool:
    """True when a public base URL is configured, which turns Steam login on."""
    return base_url() is not None


def steam_api_key() -> str | None:
    """Steam Web API key from STEAM_API_KEY, or None if unset/blank.

    Optional by design: when None, persona fetching is a clean no-op and the
    rest of the app falls back to bare account ids (graceful degradation). Read
    fresh on every call (like db_path) so tests can monkeypatch it per-test.
    """
    return os.environ.get("STEAM_API_KEY", "").strip() or None
