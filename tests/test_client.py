"""ingest.client.Client sends DEADLOCK_API_KEY (when set) on every request, and
the two deadlock-api construction sites honor DEADLOCK_REQUESTS_PER_SECOND.

All HTTP is mocked -- no test may hit the live API (hard rule 3)."""
import urllib.request

import pytest

import ingest.__main__ as ingest_main
import tracker.refresh_assets as refresh_assets
from ingest.client import Client
from ingest.ratelimit import TokenBucket


class _FakeResponse:
    """Minimal stand-in for the urlopen context manager."""

    def __init__(self, status=200, body="{}"):
        self.status = status
        self.headers = {}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body.encode("utf-8")


def _capture_urlopen(monkeypatch, captured):
    """Patch urlopen to record the Request it was handed and return a canned 200."""

    def fake_urlopen(request, timeout=None):
        captured.append(request)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _idle_bucket(stamp_path):
    """A bucket starting with a full token (no stamp file) and zero jitter, so
    the first get() neither sleeps for a token nor for jitter."""
    return TokenBucket(rate=1.0, capacity=1.0, jitter=lambda: 0.0, stamp_path=stamp_path)


def test_api_key_sent_on_every_request(monkeypatch, tmp_path):
    captured = []
    _capture_urlopen(monkeypatch, captured)
    client = Client(_idle_bucket(tmp_path / "stamp"), api_key="secret-key")

    client.get("https://api.deadlock-api.com/v1/thing")

    # urllib normalizes header names to title-case; fetch case-insensitively.
    assert captured[0].get_header("X-api-key") == "secret-key"


def test_no_auth_header_when_key_unset(monkeypatch, tmp_path):
    captured = []
    _capture_urlopen(monkeypatch, captured)
    client = Client(_idle_bucket(tmp_path / "stamp"))

    client.get("https://api.deadlock-api.com/v1/thing")

    assert captured[0].get_header("X-api-key") is None
    # The User-Agent is still present -- only auth is conditional.
    assert captured[0].get_header("User-agent") is not None


def _clear_state_env(monkeypatch, tmp_path):
    # A fresh state dir means no stamp file, so the first token is available and
    # _build_client's get() would not block; also isolates from the repo's data/.
    monkeypatch.setenv("DEADLOCK_STATE_DIR", str(tmp_path))


def test_main_build_client_honors_configured_rate_and_key(monkeypatch, tmp_path):
    _clear_state_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DEADLOCK_REQUESTS_PER_SECOND", "1.0")
    monkeypatch.setenv("DEADLOCK_API_KEY", "k")
    captured = []
    _capture_urlopen(monkeypatch, captured)

    client = ingest_main._build_client()
    assert client._bucket.rate == 1.0

    client.get("https://api.deadlock-api.com/v1/thing")
    assert captured[0].get_header("X-api-key") == "k"


def test_main_build_client_fails_loudly_on_bad_rate(monkeypatch, tmp_path):
    _clear_state_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DEADLOCK_REQUESTS_PER_SECOND", "0")
    with pytest.raises(ValueError):
        ingest_main._build_client()


def test_refresh_assets_build_client_honors_configured_rate_and_key(monkeypatch, tmp_path):
    _clear_state_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DEADLOCK_REQUESTS_PER_SECOND", "1.0")
    monkeypatch.setenv("DEADLOCK_API_KEY", "k")
    captured = []
    _capture_urlopen(monkeypatch, captured)

    client = refresh_assets._build_client()
    assert client._bucket.rate == 1.0

    client.get("https://api.deadlock-api.com/v1/assets/heroes")
    assert captured[0].get_header("X-api-key") == "k"


def test_refresh_assets_build_client_fails_loudly_on_bad_rate(monkeypatch, tmp_path):
    _clear_state_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DEADLOCK_REQUESTS_PER_SECOND", "-1")
    with pytest.raises(ValueError):
        refresh_assets._build_client()
