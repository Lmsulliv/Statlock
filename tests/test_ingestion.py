"""The six acceptance scenarios from docs/ingestion-spec.md (as amended).

1. Discovery twice in a row: queue contains no duplicates.
2. Crash mid-drain, restart: no match is lost or double-written.
3. 429: worker slows down and the match's attempts is unchanged.
4. Five 404s: match lands in 'unavailable'; nightly job revives it.
5. Two tracked accounts in one match: one queue row, one fetch, both in stats.
6. Five consecutive 500s: attempts unchanged everywhere, drain pauses 15 min,
   a subsequent 200 resets the streak.

All HTTP is mocked from trimmed recordings of real API responses.
"""
import json

import pytest

import ingest.drain as drain_module
from ingest.accounts import add_account
from ingest.discovery import run_discovery
from ingest.drain import DrainWorker, STRIKE_PAUSE_S, TRANSIENT_RETRY_S
from ingest.maintenance import revive_unavailable
from ingest.parse import insert_match
from tracker.reference import load_heroes, load_items

from tests.fakes import FakeClient, FakeSleep, ManualNow, load_fixture, fixture_text, ok

ME = 891231519
BROTHER = 890069947
SHARED_MATCH = 86714494


@pytest.fixture
def now():
    return ManualNow()


@pytest.fixture
def populated_db(db, now):
    """DB with reference data, one epoch era, and the primary tracked account."""
    load_heroes(db, load_fixture("assets_heroes_match.json"), "2026-06-11T00:00:00+00:00")
    load_items(db, load_fixture("assets_items_match.json"), "2026-06-11T00:00:00+00:00")
    db.execute(
        "INSERT INTO patch_eras(label, started_at) VALUES('all', '1970-01-01T00:00:00+00:00')"
    )
    db.commit()
    add_account(db, ME, display_name="me", is_self=True, now=now)
    return db


def make_worker(db, client, now, sleep=None):
    return DrainWorker(
        db, client, now=now,
        sleep=sleep or FakeSleep(now),
        rng=lambda lo, hi: lo,  # deterministic: no jitter in tests
    )


def metadata_body_for(match_id: int) -> str:
    """The recorded metadata, re-keyed to another queued match id when needed."""
    meta = load_fixture(f"match_metadata_{SHARED_MATCH}.json")
    if match_id != SHARED_MATCH:
        meta["match_info"]["match_id"] = match_id
    return json.dumps(meta)


def queue_rows(db):
    return db.execute(
        "SELECT * FROM fetch_queue ORDER BY match_id"
    ).fetchall()


# ── Scenario 1: discovery is idempotent ──────────────────────────────────────

def test_discovery_twice_no_duplicates(populated_db, now):
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history", ok(fixture_text(f"match_history_{ME}.json")))

    first = run_discovery(populated_db, client, now=now)
    second = run_discovery(populated_db, client, now=now)

    rows = queue_rows(populated_db)
    assert first == 3 and second == 0
    assert len(rows) == 3
    assert len({r["match_id"] for r in rows}) == 3
    # High-water mark advanced to the newest match seen.
    state = populated_db.execute(
        "SELECT last_match_id, last_synced_at FROM sync_state WHERE account_id=?", (ME,)
    ).fetchone()
    assert state["last_match_id"] == SHARED_MATCH
    assert state["last_synced_at"] is not None


def test_discovery_archives_raw_before_parsing(populated_db, now):
    client = FakeClient()
    client.add("match-history", ok(fixture_text(f"match_history_{ME}.json")))
    run_discovery(populated_db, client, now=now)
    archived = populated_db.execute(
        "SELECT * FROM raw_api_responses WHERE url LIKE '%match-history%'"
    ).fetchall()
    assert len(archived) == 1
    assert json.loads(archived[0]["body"])  # full body archived verbatim


# ── Scenario 2: crash mid-drain, restart ─────────────────────────────────────

def test_crash_mid_drain_no_partial_write_then_clean_restart(populated_db, now, monkeypatch):
    populated_db.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at) VALUES (?, ?)",
        (SHARED_MATCH, now().isoformat()),
    )
    populated_db.commit()
    client = FakeClient()
    client.add("/metadata", ok(metadata_body_for(SHARED_MATCH)))
    worker = make_worker(populated_db, client, now)

    # Simulate the process dying mid-transaction: the real inserts run, then
    # the "crash" hits before the transaction can commit.
    real_insert = insert_match

    def crashing_insert(conn, parsed):
        real_insert(conn, parsed)
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(drain_module, "insert_match", crashing_insert)
    with pytest.raises(RuntimeError):
        worker.step()

    # Nothing half-written: the transaction rolled back entirely.
    assert populated_db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
    assert populated_db.execute("SELECT COUNT(*) FROM match_players").fetchone()[0] == 0
    row = queue_rows(populated_db)[0]
    assert row["status"] == "pending"

    # Restart (fresh worker, fault gone): the match ingests exactly once.
    monkeypatch.setattr(drain_module, "insert_match", real_insert)
    worker2 = make_worker(populated_db, client, now)
    assert worker2.step() == "fetched"
    assert populated_db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
    assert populated_db.execute("SELECT COUNT(*) FROM match_players").fetchone()[0] == 12
    assert queue_rows(populated_db)[0]["status"] == "fetched"
    # And the queue is now empty: nothing to double-write.
    assert worker2.step() is None
    assert populated_db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1


# ── Scenario 3: 429 leaves the row untouched and slows the worker ────────────

def test_429_row_untouched_and_worker_sleeps(populated_db, now):
    populated_db.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at) VALUES (?, ?)",
        (SHARED_MATCH, now().isoformat()),
    )
    populated_db.commit()
    before = dict(queue_rows(populated_db)[0])

    client = FakeClient()
    client.add("/metadata", (429, {"Retry-After": "120"}, ""))
    sleep = FakeSleep(now)
    worker = make_worker(populated_db, client, now, sleep=sleep)

    assert worker.step() == "rate_limited"

    after = dict(queue_rows(populated_db)[0])
    assert after == before          # completely untouched, attempts included
    assert 120 in sleep.calls       # honored the Retry-After header


def test_429_without_retry_after_sleeps_five_minutes(populated_db, now):
    populated_db.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at) VALUES (?, ?)",
        (SHARED_MATCH, now().isoformat()),
    )
    populated_db.commit()
    client = FakeClient()
    client.add("/metadata", (429, {}, ""))
    sleep = FakeSleep(now)
    worker = make_worker(populated_db, client, now, sleep=sleep)
    worker.step()
    assert 300 in sleep.calls


# ── Scenario 4: five 404s -> unavailable; nightly job revives ────────────────

def test_five_404s_then_unavailable_then_revived(populated_db, now):
    populated_db.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at) VALUES (?, ?)",
        (SHARED_MATCH, now().isoformat()),
    )
    populated_db.commit()
    client = FakeClient()
    client.add("/metadata", (404, {}, "not found"))
    worker = make_worker(populated_db, client, now)

    for attempt in range(1, 6):
        outcome = worker.step()
        row = queue_rows(populated_db)[0]
        assert row["attempts"] == attempt
        if attempt < 5:
            assert outcome == "failed"
            assert row["status"] == "failed"
            assert row["next_retry_at"] > now().isoformat()
            now.advance(2 * 86400)  # jump past any backoff
        else:
            assert outcome == "unavailable"
            assert row["status"] == "unavailable"

    # Once unavailable, the drain loop no longer touches it.
    assert worker.step() is None

    # Nightly maintenance revives it after 24h...
    now.advance(25 * 3600)
    assert revive_unavailable(populated_db, now=now) == 1
    row = queue_rows(populated_db)[0]
    assert row["status"] == "pending"
    assert row["attempts"] == 0

    # ...and a successful fetch finally ingests it.
    client2 = FakeClient()
    client2.add("/metadata", ok(metadata_body_for(SHARED_MATCH)))
    worker2 = make_worker(populated_db, client2, now)
    assert worker2.step() == "fetched"


# ── Scenario 5: two tracked accounts in the same match ───────────────────────

def test_two_tracked_accounts_one_queue_row_one_fetch(populated_db, now):
    add_account(populated_db, BROTHER, display_name="brother", now=now)
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history", ok(fixture_text(f"match_history_{ME}.json")))
    client.add(f"/v1/players/{BROTHER}/match-history", ok(fixture_text(f"match_history_{BROTHER}.json")))
    for match_id in (86704689, 86707774, SHARED_MATCH):
        client.add(f"/v1/matches/{match_id}/metadata", ok(metadata_body_for(match_id)))

    run_discovery(populated_db, client, now=now)

    # The shared match was discovered through both accounts but queued once.
    shared = populated_db.execute(
        "SELECT COUNT(*) FROM fetch_queue WHERE match_id=?", (SHARED_MATCH,)
    ).fetchone()[0]
    assert shared == 1

    worker = make_worker(populated_db, client, now)
    worker.drain()

    # One fetch for the shared match, not one per account.
    assert len(client.calls_matching(f"/v1/matches/{SHARED_MATCH}/metadata")) == 1
    # Both tracked players landed in stats.
    for account in (ME, BROTHER):
        row = populated_db.execute(
            "SELECT * FROM match_players WHERE match_id=? AND account_id=?",
            (SHARED_MATCH, account),
        ).fetchone()
        assert row is not None


# ── Scenario 6: transient-failure circuit breaker ────────────────────────────

def test_five_consecutive_500s_pause_drain_and_200_resets(populated_db, now):
    match_ids = list(range(100, 105))
    for mid in match_ids:
        populated_db.execute(
            "INSERT INTO fetch_queue(match_id, discovered_at) VALUES (?, ?)",
            (mid, now().isoformat()),
        )
    populated_db.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at) VALUES (?, ?)",
        (SHARED_MATCH, now().isoformat()),
    )
    populated_db.commit()

    client = FakeClient()
    for mid in match_ids:
        client.add(f"/v1/matches/{mid}/metadata", (500, {}, "server error"))
    client.add(f"/v1/matches/{SHARED_MATCH}/metadata", ok(metadata_body_for(SHARED_MATCH)))
    # Sleep must NOT advance the clock here: the failed rows' 5-minute retry
    # windows have to stay in the future so the next eligible row is the 200.
    sleep = FakeSleep()
    worker = make_worker(populated_db, client, now, sleep=sleep)

    for i, mid in enumerate(match_ids, start=1):
        assert worker.step() == "transient"
        row = populated_db.execute(
            "SELECT * FROM fetch_queue WHERE match_id=?", (mid,)
        ).fetchone()
        assert row["attempts"] == 0            # 5xx never counts against the match
        assert row["status"] == "failed"
        assert row["next_retry_at"] is not None
        if i < 5:
            assert worker.transient_strikes == i

    # Fifth consecutive strike tripped the breaker: 15-minute pause, reset.
    assert STRIKE_PAUSE_S in sleep.calls
    assert worker.transient_strikes == 0

    # A 200 on the next match keeps the streak at zero.
    assert worker.step() == "fetched"
    assert worker.transient_strikes == 0


def test_transient_retry_is_flat_five_minutes(populated_db, now):
    populated_db.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at) VALUES (?, ?)",
        (SHARED_MATCH, now().isoformat()),
    )
    populated_db.commit()
    client = FakeClient()
    client.add("/metadata", (503, {}, ""))
    worker = make_worker(populated_db, client, now)
    before = now()
    worker.step()
    row = queue_rows(populated_db)[0]
    expected = before.timestamp() + TRANSIENT_RETRY_S
    from datetime import datetime
    actual = datetime.fromisoformat(row["next_retry_at"]).timestamp()
    assert abs(actual - expected) < 2
