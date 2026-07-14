"""tracker.config accessors read the environment fresh per call, so a granted
API key or a maintainer-blessed rate is a deployment (.env) change, not a code
change. Mirrors the style of tests/test_config.py."""
import pytest

from tracker.config import deadlock_api_key, deadlock_requests_per_second


def test_api_key_none_when_unset(monkeypatch):
    monkeypatch.delenv("DEADLOCK_API_KEY", raising=False)
    assert deadlock_api_key() is None


def test_api_key_none_when_blank(monkeypatch):
    # Compose's ${VAR:-} injects an empty string when the var is unset.
    monkeypatch.setenv("DEADLOCK_API_KEY", "   ")
    assert deadlock_api_key() is None


def test_api_key_stripped_when_set(monkeypatch):
    monkeypatch.setenv("DEADLOCK_API_KEY", "  secret-key ")
    assert deadlock_api_key() == "secret-key"


def test_api_key_read_fresh_per_call(monkeypatch):
    monkeypatch.setenv("DEADLOCK_API_KEY", "first")
    first = deadlock_api_key()
    monkeypatch.setenv("DEADLOCK_API_KEY", "second")
    assert deadlock_api_key() != first


def test_rate_default_when_unset(monkeypatch):
    monkeypatch.delenv("DEADLOCK_REQUESTS_PER_SECOND", raising=False)
    assert deadlock_requests_per_second() == 0.2


def test_rate_default_when_blank(monkeypatch):
    # Compose's ${VAR:-} injects "" when the var is unset -> treat as default.
    monkeypatch.setenv("DEADLOCK_REQUESTS_PER_SECOND", "  ")
    assert deadlock_requests_per_second() == 0.2


def test_rate_parses_configured_value(monkeypatch):
    monkeypatch.setenv("DEADLOCK_REQUESTS_PER_SECOND", "1.0")
    assert deadlock_requests_per_second() == 1.0


def test_rate_read_fresh_per_call(monkeypatch):
    monkeypatch.setenv("DEADLOCK_REQUESTS_PER_SECOND", "1.0")
    first = deadlock_requests_per_second()
    monkeypatch.setenv("DEADLOCK_REQUESTS_PER_SECOND", "2.0")
    assert deadlock_requests_per_second() != first


@pytest.mark.parametrize("bad", ["0", "-0.5", "abc"])
def test_rate_rejects_non_positive_or_garbage_loudly(monkeypatch, bad):
    # No silent fallback: a typo'd rate must kill the process at startup with a
    # message that names the variable, not quietly run at the default.
    monkeypatch.setenv("DEADLOCK_REQUESTS_PER_SECOND", bad)
    with pytest.raises(ValueError) as excinfo:
        deadlock_requests_per_second()
    assert "DEADLOCK_REQUESTS_PER_SECOND" in str(excinfo.value)
