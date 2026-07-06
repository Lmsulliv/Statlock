"""Worker state files (rate-limit stamps + the daemon heartbeat) must resolve
under DEADLOCK_STATE_DIR so they land on the persistent volume in production. In
a container the image filesystem is ephemeral, so a stamp written there is lost
on every restart and cross-restart rate-limit politeness resets. When the env
var is unset the paths fall back to the repo's data/ dir, so dev is unchanged.
"""
from pathlib import Path

import tracker.paths as paths
from tracker.paths import (
    deadlock_stamp_path,
    state_dir,
    steam_stamp_path,
    worker_heartbeat_path,
)


def test_state_dir_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DEADLOCK_STATE_DIR", str(tmp_path))
    assert state_dir() == tmp_path


def test_state_dir_defaults_to_repo_data(monkeypatch):
    monkeypatch.delenv("DEADLOCK_STATE_DIR", raising=False)
    assert state_dir() == Path(paths.__file__).parent.parent / "data"


def test_state_dir_read_fresh_per_call(monkeypatch, tmp_path):
    """Resolved on every call (like api.config.db_path), not cached at import, so a
    test or deployment can repoint it without reloading the module."""
    monkeypatch.setenv("DEADLOCK_STATE_DIR", str(tmp_path / "a"))
    first = state_dir()
    monkeypatch.setenv("DEADLOCK_STATE_DIR", str(tmp_path / "b"))
    assert state_dir() != first


def test_stamp_helpers_land_under_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DEADLOCK_STATE_DIR", str(tmp_path))
    assert deadlock_stamp_path() == tmp_path / ".last_deadlock_request"
    assert steam_stamp_path() == tmp_path / ".last_steam_request"
    assert worker_heartbeat_path() == tmp_path / ".worker_heartbeat"
