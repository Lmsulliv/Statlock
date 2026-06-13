"""Shared pytest fixtures."""
import os
import sqlite3
import pytest
from hypothesis import settings

from tracker.db import connect
from tracker.migrate import migrate


# Hypothesis profiles.
# "default" is used locally and in most CI runs.
# Set HYPOTHESIS_PROFILE=ci for a longer exhaustive run (e.g., nightly).
# deadline=None prevents slow CI machines from failing timing checks.
settings.register_profile("default", max_examples=200, deadline=None)
settings.register_profile("ci", max_examples=500, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    """Fresh migrated SQLite database for each test."""
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    return conn


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Hard rule 3: no test may hit the live API. Any urlopen call fails loudly."""
    def _blocked(*args, **kwargs):
        raise AssertionError("Network access attempted in a test (urlopen blocked)")
    monkeypatch.setattr("urllib.request.urlopen", _blocked)
