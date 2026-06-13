"""Unit tests for ingest.accounts: ID normalization and account registration."""
import pytest

from ingest.accounts import add_account, to_account_id

from tests.fakes import ManualNow


def test_account_id_passes_through():
    assert to_account_id("891231519") == 891231519
    assert to_account_id(891231519) == 891231519


def test_steamid64_is_normalized():
    assert to_account_id("76561198851497247") == 891231519


def test_profile_url_is_parsed():
    url = "https://steamcommunity.com/profiles/76561198851497247"
    assert to_account_id(url) == 891231519
    assert to_account_id(url + "/") == 891231519


def test_vanity_url_rejected_with_helpful_message():
    with pytest.raises(ValueError, match="(?i)vanity"):
        to_account_id("https://steamcommunity.com/id/somename")


def test_garbage_rejected():
    with pytest.raises(ValueError):
        to_account_id("not an id")
    with pytest.raises(ValueError):
        to_account_id("-5")


def test_add_account_creates_tracked_and_sync_rows(db):
    now = ManualNow()
    account_id = add_account(db, "76561198851497247", display_name="me",
                             is_self=True, now=now)
    assert account_id == 891231519
    tracked = db.execute(
        "SELECT * FROM tracked_accounts WHERE account_id=?", (account_id,)
    ).fetchone()
    assert tracked["display_name"] == "me"
    assert tracked["is_self"] == 1
    sync = db.execute(
        "SELECT * FROM sync_state WHERE account_id=?", (account_id,)
    ).fetchone()
    assert sync is not None
    assert sync["last_match_id"] is None


def test_add_account_is_idempotent(db):
    now = ManualNow()
    add_account(db, 891231519, display_name="me", now=now)
    add_account(db, 891231519, display_name="me", now=now)
    assert db.execute("SELECT COUNT(*) FROM tracked_accounts").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0] == 1
