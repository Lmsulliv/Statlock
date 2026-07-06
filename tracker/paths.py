"""Where the worker keeps its cross-restart state files.

Two token-bucket stamp files (deadlock-api + Steam) and the daemon heartbeat
must survive process restarts, otherwise rate-limit politeness resets every time
the worker is bounced. In a container the image filesystem is ephemeral, so these
files have to live on the mounted volume instead of next to the code.

DEADLOCK_STATE_DIR selects that directory; production points it at the volume
(/data). Unset, it falls back to the repo's data/ dir -- the historical location,
so dev and the test suite are unchanged. Read fresh on every call (like
api.config.db_path) so a test can monkeypatch the environment per-test.
"""
import os
from pathlib import Path

# Historical default: the repo-level data/ dir, sibling to tracker/.
_DEFAULT_STATE_DIR = Path(__file__).parent.parent / "data"


def state_dir() -> Path:
    """Directory holding the worker's persistent state files.

    DEADLOCK_STATE_DIR if set, else the repo's data/ dir (unchanged dev behavior).
    """
    env = os.environ.get("DEADLOCK_STATE_DIR")
    return Path(env) if env else _DEFAULT_STATE_DIR


def deadlock_stamp_path() -> Path:
    """Stamp file recording the last deadlock-api request (1 req / 5 s budget)."""
    return state_dir() / ".last_deadlock_request"


def steam_stamp_path() -> Path:
    """Stamp file for the SEPARATE Steam persona token bucket."""
    return state_dir() / ".last_steam_request"


def worker_heartbeat_path() -> Path:
    """Liveness stamp the daemon touches each loop; the healthcheck reads its mtime."""
    return state_dir() / ".worker_heartbeat"
