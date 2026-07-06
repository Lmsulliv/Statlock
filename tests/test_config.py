"""api.config accessors read the environment fresh per call. Here we cover
demo_account_id's parsing rules; db_path/base_url/etc. are exercised indirectly
by the app tests."""
from api.config import demo_account_id


def test_demo_account_id_none_when_unset(monkeypatch):
    monkeypatch.delenv("DEMO_ACCOUNT_ID", raising=False)
    assert demo_account_id() is None


def test_demo_account_id_parses_integer(monkeypatch):
    monkeypatch.setenv("DEMO_ACCOUNT_ID", "  424242 ")
    assert demo_account_id() == 424242


def test_demo_account_id_none_when_blank(monkeypatch):
    monkeypatch.setenv("DEMO_ACCOUNT_ID", "   ")
    assert demo_account_id() is None


def test_demo_account_id_none_when_non_integer(monkeypatch):
    # A misconfigured value degrades to "no demo" rather than crashing /me.
    monkeypatch.setenv("DEMO_ACCOUNT_ID", "not-a-number")
    assert demo_account_id() is None


def test_demo_account_id_read_fresh_per_call(monkeypatch):
    monkeypatch.setenv("DEMO_ACCOUNT_ID", "1")
    first = demo_account_id()
    monkeypatch.setenv("DEMO_ACCOUNT_ID", "2")
    assert demo_account_id() != first
