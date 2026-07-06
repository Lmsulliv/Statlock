"""Activity-aware discovery cadence (schema v20).

Discovery used to cost one API call per tracked account every 30 min forever;
these tests pin the new behaviour: each account is rescheduled by how recently it
was played (active/idle/dormant), the daemon's discovery pass visits only DUE
accounts so per-cycle calls track active accounts, and the web process can flag
an idle account for a fresh check via the discovery_requests mailbox (never by
calling the API itself).
"""
import json
from datetime import timedelta

import pytest

from api import service
from ingest.accounts import add_account
from ingest.discovery import (
    INTERVAL_ACTIVE_S,
    INTERVAL_IDLE_S,
    INTERVAL_DORMANT_S,
    _interval_for_last_match,
    discover_account,
    discover_due,
)
from tests.fakes import FakeClient, ManualNow, ok

ACCT = 42


def _entry(match_id: int, start_unix: int, hero_id: int = 7) -> dict:
    """A match-history entry with a controllable start_time (unix seconds)."""
    return {
        "match_id": match_id, "start_time": start_unix, "hero_id": hero_id,
        "game_mode": 1, "player_team": 0, "match_result": 0,
        "player_kills": 1, "player_deaths": 1, "player_assists": 1,
        "net_worth": 1000, "last_hits": 10, "denies": 1, "match_duration_s": 1800,
    }


def _history(client: FakeClient, entries: list[dict]) -> None:
    client.add("match-history", ok(json.dumps(entries)))


def _next_at(conn, account_id: int) -> str | None:
    return conn.execute(
        "SELECT next_discovery_at FROM sync_state WHERE account_id = ?", (account_id,)
    ).fetchone()["next_discovery_at"]


@pytest.fixture
def now():
    return ManualNow()


def _days_ago_unix(now: ManualNow, days: float) -> int:
    return int((now() - timedelta(days=days)).timestamp())


# ── Cadence bucketing (pure helper) ──────────────────────────────────────────

def test_interval_buckets_by_last_match_age(now):
    iso = lambda d: (now() - timedelta(days=d)).isoformat()
    assert _interval_for_last_match(None, now()) == INTERVAL_DORMANT_S   # never played
    assert _interval_for_last_match(iso(1), now()) == INTERVAL_ACTIVE_S  # within 48h
    assert _interval_for_last_match(iso(5), now()) == INTERVAL_IDLE_S    # 2-14 days
    assert _interval_for_last_match(iso(20), now()) == INTERVAL_DORMANT_S  # > 14 days


# ── discover_account schedules the next visit ────────────────────────────────

def test_new_matches_schedule_active_cadence(db, now):
    add_account(db, ACCT, is_self=True, now=now)
    client = FakeClient()
    _history(client, [_entry(100, _days_ago_unix(now, 1))])

    assert discover_account(db, client, ACCT, now=now) == 1
    assert _next_at(db, ACCT) == (now() + timedelta(seconds=INTERVAL_ACTIVE_S)).isoformat()


def test_no_new_but_recent_match_stays_active(db, now):
    add_account(db, ACCT, is_self=True, now=now)
    client = FakeClient()
    # Same single match served twice: the second pass finds nothing new, but the
    # match is only 1 day old -> still active.
    _history(client, [_entry(100, _days_ago_unix(now, 1))])
    _history(client, [_entry(100, _days_ago_unix(now, 1))])
    discover_account(db, client, ACCT, now=now)          # first: syncs high-water

    assert discover_account(db, client, ACCT, now=now) == 0
    assert _next_at(db, ACCT) == (now() + timedelta(seconds=INTERVAL_ACTIVE_S)).isoformat()


def test_idle_account_scheduled_every_6h(db, now):
    add_account(db, ACCT, is_self=True, now=now)
    client = FakeClient()
    _history(client, [_entry(100, _days_ago_unix(now, 5))])
    _history(client, [_entry(100, _days_ago_unix(now, 5))])
    discover_account(db, client, ACCT, now=now)

    assert discover_account(db, client, ACCT, now=now) == 0   # nothing new; 5 days idle
    assert _next_at(db, ACCT) == (now() + timedelta(seconds=INTERVAL_IDLE_S)).isoformat()


def test_dormant_account_scheduled_every_24h(db, now):
    add_account(db, ACCT, is_self=True, now=now)
    client = FakeClient()
    _history(client, [_entry(100, _days_ago_unix(now, 20))])
    _history(client, [_entry(100, _days_ago_unix(now, 20))])
    discover_account(db, client, ACCT, now=now)

    assert discover_account(db, client, ACCT, now=now) == 0   # nothing new; 20 days idle
    assert _next_at(db, ACCT) == (now() + timedelta(seconds=INTERVAL_DORMANT_S)).isoformat()


def test_non_200_reschedules_active_not_due_next_iteration(db, now):
    add_account(db, ACCT, is_self=True, now=now)
    # Mark it synced so discover_due considers it at all.
    db.execute("UPDATE sync_state SET last_synced_at = ? WHERE account_id = ?",
               (now().isoformat(), ACCT))
    db.commit()
    client = FakeClient()
    client.add("match-history", (500, {}, ""))

    assert discover_account(db, client, ACCT, now=now) == 0
    # Rescheduled 30 min out (not left "due now"), so the next pass skips it.
    assert _next_at(db, ACCT) == (now() + timedelta(seconds=INTERVAL_ACTIVE_S)).isoformat()
    assert discover_due(db, client, now=now) == {}   # not due -> no second API call


# ── discover_due visits only due accounts ────────────────────────────────────

def _synced_account(db, account_id, now, *, next_at, played_days_ago=1):
    """A tracked, synced account with a controllable schedule and one summary row
    (so the reschedule can bucket it)."""
    add_account(db, account_id, is_self=(account_id == ACCT), now=now)
    db.execute(
        "UPDATE sync_state SET last_synced_at = ?, last_match_id = 1,"
        " next_discovery_at = ? WHERE account_id = ?",
        (now().isoformat(), next_at, account_id))
    db.execute(
        "INSERT INTO account_match_summaries(account_id, match_id, start_time, fetched_at)"
        " VALUES (?, 1, ?, ?)",
        (account_id, (now() - timedelta(days=played_days_ago)).isoformat(), now().isoformat()))
    db.commit()


def test_discover_due_visits_only_due_accounts(db, now):
    past = (now() - timedelta(minutes=1)).isoformat()
    future = (now() + timedelta(hours=1)).isoformat()
    _synced_account(db, 1, now, next_at=past)      # due (lapsed)
    _synced_account(db, 2, now, next_at=future)    # not due
    _synced_account(db, 3, now, next_at=None)      # due (NULL = due now)
    # A never-synced account: must stay off discover_due (immediate path owns it).
    add_account(db, 4, is_self=False, now=now)

    client = FakeClient()
    # Only the due accounts (1 and 3) should be fetched; serve their (unchanged)
    # history so the pass finds nothing new.
    _history(client, [_entry(1, _days_ago_unix(now, 1))])

    visited = discover_due(db, client, now=now)

    assert set(visited) == {1, 3}                          # only due accounts
    fetched = {int(u.split("/players/")[1].split("/")[0]) for u in client.calls}
    assert fetched == {1, 3}                               # per-cycle calls == active count
    assert 2 not in fetched and 4 not in fetched


def test_idle_account_visited_once_per_schedule(db, now):
    # An idle (20-day) account that is due right now: one visit reschedules it a
    # full day out, so an immediate second pass does not re-fetch it.
    _synced_account(db, ACCT, now, next_at=(now() - timedelta(minutes=1)).isoformat(),
                    played_days_ago=20)
    client = FakeClient()
    _history(client, [_entry(1, _days_ago_unix(now, 20))])
    _history(client, [_entry(1, _days_ago_unix(now, 20))])

    assert discover_due(db, client, now=now) == {ACCT: 0}   # visited once
    assert _next_at(db, ACCT) == (now() + timedelta(seconds=INTERVAL_DORMANT_S)).isoformat()
    assert discover_due(db, client, now=now) == {}          # not due again same day


# ── Web mailbox: discovery_requests ──────────────────────────────────────────

def test_request_makes_account_due_and_is_deleted(db, now):
    future = (now() + timedelta(hours=6)).isoformat()
    _synced_account(db, ACCT, now, next_at=future)          # not due by schedule
    db.execute("INSERT INTO discovery_requests(account_id, requested_at) VALUES (?, ?)",
               (ACCT, now().isoformat()))
    db.commit()
    client = FakeClient()
    _history(client, [_entry(1, _days_ago_unix(now, 1))])

    visited = discover_due(db, client, now=now)

    assert ACCT in visited                                  # request forced it due
    # The request row is consumed so it isn't re-run every iteration.
    assert db.execute("SELECT COUNT(*) FROM discovery_requests").fetchone()[0] == 0


def test_overview_of_stale_account_files_a_request(db, now):
    _synced_account(db, ACCT, now, next_at=None)
    # Age the sync past the staleness floor.
    db.execute("UPDATE sync_state SET last_synced_at = ? WHERE account_id = ?",
               ((now() - timedelta(hours=2)).isoformat(), ACCT))
    db.commit()

    service.account_progress(db, ACCT, now=now)

    reqs = [r["account_id"] for r in db.execute("SELECT account_id FROM discovery_requests")]
    assert reqs == [ACCT]


def test_freshly_synced_account_files_no_request(db, now):
    _synced_account(db, ACCT, now, next_at=None)
    # last_synced_at is "now" (fresh) -> within the floor -> no request.
    service.account_progress(db, ACCT, now=now)
    assert db.execute("SELECT COUNT(*) FROM discovery_requests").fetchone()[0] == 0


def test_never_synced_account_files_no_request(db, now):
    add_account(db, ACCT, is_self=True, now=now)   # sync_state row, last_synced_at NULL
    service.account_progress(db, ACCT, now=now)
    assert db.execute("SELECT COUNT(*) FROM discovery_requests").fetchone()[0] == 0
