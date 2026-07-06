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
from ingest.discovery import discover_account, run_discovery
from ingest.drain import (
    DEFERRED_RETRY_S, DrainWorker, MAX_DEFER_AGE_S, STRIKE_PAUSE_S, TRANSIENT_RETRY_S,
)
from ingest.maintenance import revive_unavailable
from ingest.parse import insert_match
from tracker import rawstore
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
    # Migration 013 pre-seeds 12 curated eras; clear them so every ingested match
    # binds to this single epoch era instead of a curated one.
    db.execute("DELETE FROM patch_eras")
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


# ── Archive dedup: a good 200 metadata is archived only via matches.raw_json ──

def test_good_metadata_not_duplicated_into_raw_api_responses(populated_db, now):
    populated_db.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at) VALUES (?, ?)",
        (SHARED_MATCH, now().isoformat()),
    )
    populated_db.commit()
    body = metadata_body_for(SHARED_MATCH)
    client = FakeClient()
    client.add("/metadata", ok(body))
    worker = make_worker(populated_db, client, now)

    assert worker.step() == "fetched"

    # No duplicate: the 200 metadata body is NOT in raw_api_responses...
    assert populated_db.execute(
        "SELECT COUNT(*) FROM raw_api_responses WHERE url LIKE '%/metadata'"
    ).fetchone()[0] == 0
    # ...it lives compressed in matches.raw_json, round-tripping byte-for-byte.
    stored = populated_db.execute(
        "SELECT raw_json FROM matches WHERE match_id = ?", (SHARED_MATCH,)
    ).fetchone()["raw_json"]
    assert isinstance(stored, bytes)
    assert rawstore.load(stored) == body


def test_bad_payload_200_is_archived_for_forensics(populated_db, now):
    populated_db.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at) VALUES (?, ?)",
        (SHARED_MATCH, now().isoformat()),
    )
    populated_db.commit()
    client = FakeClient()
    client.add("/metadata", (200, {}, "{not valid json"))   # poison 200
    worker = make_worker(populated_db, client, now)

    assert worker.step() == "failed"                        # bad payload -> match fault

    # The unparseable body was never stored as a match, so it IS archived
    # (this is the one case a 200 lands in raw_api_responses).
    archived = populated_db.execute(
        "SELECT body FROM raw_api_responses WHERE url LIKE '%/metadata'"
    ).fetchall()
    assert len(archived) == 1
    assert archived[0]["body"] == "{not valid json"
    assert populated_db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0


def test_non_200_metadata_is_archived(populated_db, now):
    populated_db.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at) VALUES (?, ?)",
        (SHARED_MATCH, now().isoformat()),
    )
    populated_db.commit()
    client = FakeClient()
    client.add("/metadata", (404, {}, "not found"))
    worker = make_worker(populated_db, client, now)

    worker.step()

    assert populated_db.execute(
        "SELECT body FROM raw_api_responses WHERE url LIKE '%/metadata'"
    ).fetchone()["body"] == "not found"


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
    # Distinct discovered_at stamps keep drain order = insertion order (the
    # priority-0 tier orders by discovered_at first, newest match_id on ties).
    for mid in match_ids:
        populated_db.execute(
            "INSERT INTO fetch_queue(match_id, discovered_at) VALUES (?, ?)",
            (mid, now().isoformat()),
        )
        now.advance(1)
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


# ── Deferral: not-yet-parsed matches (the 400 "salts cannot be fetched" path) ─
#
# The metadata endpoint reports "no data for this match" with HTTP 400 + body
# `{"error":"Match salts for match X cannot be fetched",...}` (verified by the
# 2026-06-18 spike, docs/api-findings.md). That is the match's SITUATION -- it
# hasn't been parsed yet (or its old replay was purged) -- not its fault, so the
# drain loop DEFERS it: patient hourly retries OUTSIDE the attempt budget, lower
# priority than fresh work, given up only after MAX_DEFER_AGE_S.

from datetime import timedelta

from ingest.util import iso_to_unix


def salts_400(match_id: int = SHARED_MATCH):
    body = f'{{"error":"Match salts for match {match_id} cannot be fetched","status":400}}'
    return (400, {}, body)


def enqueue(db, match_id, now, **cols):
    """Insert one fetch_queue row (pending by default; override any column)."""
    row = {"discovered_at": now().isoformat(), "status": "pending"}
    row.update(cols)
    columns = ["match_id", *row]
    db.execute(
        f"INSERT INTO fetch_queue({', '.join(columns)})"
        f" VALUES ({', '.join('?' for _ in columns)})",
        [match_id, *row.values()],
    )
    db.commit()


def test_not_parsed_match_is_deferred_not_faulted(populated_db, now):
    enqueue(populated_db, SHARED_MATCH, now)
    client = FakeClient()
    client.add("/metadata", salts_400())
    worker = make_worker(populated_db, client, now)

    outcome = worker.step()

    row = queue_rows(populated_db)[0]
    assert outcome == "deferred"
    assert row["status"] == "deferred"
    assert row["attempts"] == 0                     # attempt budget untouched
    assert worker.transient_strikes == 0            # circuit breaker untouched
    assert row["deferred_since"] is not None        # give-up clock started
    # next_retry_at is exactly DEFERRED_RETRY_S in the future.
    assert iso_to_unix(row["next_retry_at"]) - iso_to_unix(now().isoformat()) == DEFERRED_RETRY_S


def test_deferred_yields_to_fresh_work(populated_db, now):
    # Both a due deferred row and a pending row are eligible at once.
    enqueue(populated_db, 111, now, status="deferred",
            next_retry_at=now().isoformat(), deferred_since=now().isoformat())
    enqueue(populated_db, 222, now, status="pending")
    worker = make_worker(populated_db, FakeClient(), now)

    # Fresh work wins: the pending row is picked before the due deferred row.
    assert worker._next_row()["match_id"] == 222

    # Only once no pending/failed row is eligible does the deferred row come up.
    populated_db.execute("UPDATE fetch_queue SET status='fetched' WHERE match_id=222")
    populated_db.commit()
    assert worker._next_row()["match_id"] == 111

    # A deferred row whose retry isn't due yet is left alone.
    populated_db.execute(
        "UPDATE fetch_queue SET next_retry_at='2999-01-01T00:00:00+00:00' WHERE match_id=111")
    populated_db.commit()
    assert worker._next_row() is None


def test_deferred_match_later_parses_and_ingests(populated_db, now):
    enqueue(populated_db, SHARED_MATCH, now, status="deferred",
            next_retry_at=now().isoformat(), deferred_since=now().isoformat())
    client = FakeClient()
    client.add("/metadata", ok(metadata_body_for(SHARED_MATCH)))
    worker = make_worker(populated_db, client, now)

    assert worker.step() == "fetched"
    assert queue_rows(populated_db)[0]["status"] == "fetched"
    assert populated_db.execute(
        "SELECT 1 FROM match_players WHERE match_id=? AND account_id=?",
        (SHARED_MATCH, ME),
    ).fetchone() is not None


def test_deferred_past_max_age_gives_up_as_unavailable(populated_db, now):
    old = (now() - timedelta(seconds=MAX_DEFER_AGE_S + 3600)).isoformat()
    enqueue(populated_db, SHARED_MATCH, now, status="deferred",
            next_retry_at=now().isoformat(), deferred_since=old)
    client = FakeClient()
    client.add("/metadata", salts_400())
    worker = make_worker(populated_db, client, now)

    assert worker.step() == "unavailable"
    row = queue_rows(populated_db)[0]
    assert row["status"] == "unavailable"        # dead match still terminates
    assert row["attempts"] == 0                  # without ever spending the budget


def test_generic_400_without_salts_is_still_a_match_fault(populated_db, now):
    # Detection is body-specific: a plain 400 is a real fault and DOES count.
    enqueue(populated_db, SHARED_MATCH, now)
    client = FakeClient()
    client.add("/metadata", (400, {}, '{"error":"bad request","status":400}'))
    worker = make_worker(populated_db, client, now)

    assert worker.step() == "failed"
    row = queue_rows(populated_db)[0]
    assert row["status"] == "failed"
    assert row["attempts"] == 1


# ── Bad payload: a 200 whose body can't be parsed/inserted ───────────────────
#
# HTTP 200 says the request succeeded, not that the body is usable metadata. An
# empty, truncated, or unexpected body makes json.loads / parse_metadata /
# insert_match raise. Left unguarded that exception kills the daemon while the
# row stays 'pending', so a restart refetches the same match and crashes again:
# one poison message halts ingestion for everyone. We treat it as the match's
# FAULT (raw body already archived), spending the attempt budget like a 404.


@pytest.mark.parametrize("body", ["", "not json", "{}"])
def test_bad_payload_marks_match_failed_and_drain_continues(populated_db, now, body):
    HEALTHY = SHARED_MATCH
    POISON = 55555
    enqueue(populated_db, POISON, now)          # discovered first -> drained first
    now.advance(1)                              # distinct stamp keeps FIFO order
    enqueue(populated_db, HEALTHY, now)
    client = FakeClient()
    client.add(f"/v1/matches/{POISON}/metadata", (200, {}, body))
    client.add(f"/v1/matches/{HEALTHY}/metadata", ok(metadata_body_for(HEALTHY)))
    worker = make_worker(populated_db, client, now)

    steps = worker.drain()

    assert steps == 2                            # both rows processed, no crash
    poison = populated_db.execute(
        "SELECT * FROM fetch_queue WHERE match_id=?", (POISON,)).fetchone()
    assert poison["status"] == "failed"
    assert poison["attempts"] == 1              # counts against the budget
    assert poison["last_error"].startswith("bad payload: ")
    # The healthy match behind the poison one still ingested.
    healthy = populated_db.execute(
        "SELECT * FROM fetch_queue WHERE match_id=?", (HEALTHY,)).fetchone()
    assert healthy["status"] == "fetched"
    assert populated_db.execute(
        "SELECT 1 FROM match_players WHERE match_id=? AND account_id=?",
        (HEALTHY, ME)).fetchone() is not None


def test_five_bad_payloads_land_unavailable(populated_db, now):
    enqueue(populated_db, SHARED_MATCH, now)
    client = FakeClient()
    client.add("/metadata", (200, {}, "not json"))
    worker = make_worker(populated_db, client, now)

    for attempt in range(1, 6):
        worker.step()
        row = queue_rows(populated_db)[0]
        assert row["attempts"] == attempt
        if attempt < 5:
            assert row["status"] == "failed"
            now.advance(2 * 86400)             # jump past the backoff window
        else:
            assert row["status"] == "unavailable"
    assert worker.step() is None               # nothing eligible left


# ── Unknown hero: a match with a hero released after the last asset refresh ───
#
# match_players.hero_id has an enforced FK to heroes, so a brand-new hero fails
# the insert with IntegrityError -- the same crash loop as a poison payload, but
# on patch days. Recovery: refresh assets ONCE per daemon run to try to learn the
# hero, and if it's still unknown, write a placeholder heroes row so the FK holds
# (the nightly asset refresh fills the real name later).

UNKNOWN_HERO = 9999


def metadata_with_unknown_hero(match_id: int, hero_id: int = UNKNOWN_HERO) -> str:
    meta = load_fixture(f"match_metadata_{SHARED_MATCH}.json")
    meta["match_info"]["match_id"] = match_id
    meta["match_info"]["players"][0]["hero_id"] = hero_id
    return json.dumps(meta)


def heroes_fixture_plus(hero_id: int, name: str) -> str:
    heroes = load_fixture("assets_heroes_match.json")
    heroes.append({"id": hero_id, "name": name, "class_name": "hero_new",
                   "images": {}, "disabled": False, "player_selectable": True})
    return json.dumps(heroes)


def add_asset_routes(client: FakeClient, heroes_body: str) -> None:
    client.add("/v1/assets/heroes", (200, {}, heroes_body))
    client.add("/v1/assets/items", ok(fixture_text("assets_items_match.json")))
    client.add("/v1/assets/ranks", ok(fixture_text("assets_ranks.json")))


def test_unknown_hero_learned_by_refresh_then_ingests(populated_db, now):
    enqueue(populated_db, SHARED_MATCH, now)
    client = FakeClient()
    client.add("/metadata", ok(metadata_with_unknown_hero(SHARED_MATCH)))
    # The refresh teaches us the new hero, so no placeholder is needed.
    add_asset_routes(client, heroes_fixture_plus(UNKNOWN_HERO, "Freshly Released"))
    worker = make_worker(populated_db, client, now)

    assert worker.step() == "fetched"
    assert len(client.calls_matching("assets/heroes")) == 1     # exactly one refresh
    hero = populated_db.execute(
        "SELECT name FROM heroes WHERE hero_id=?", (UNKNOWN_HERO,)).fetchone()
    assert hero["name"] == "Freshly Released"                   # real name, not placeholder
    assert populated_db.execute(
        "SELECT 1 FROM match_players WHERE match_id=? AND hero_id=?",
        (SHARED_MATCH, UNKNOWN_HERO)).fetchone() is not None


def test_unknown_hero_falls_back_to_placeholder(populated_db, now):
    enqueue(populated_db, SHARED_MATCH, now)
    client = FakeClient()
    client.add("/metadata", ok(metadata_with_unknown_hero(SHARED_MATCH)))
    # The refresh returns the SAME stale assets: the hero stays unknown.
    add_asset_routes(client, fixture_text("assets_heroes_match.json"))
    worker = make_worker(populated_db, client, now)

    assert worker.step() == "fetched"
    assert len(client.calls_matching("assets/heroes")) == 1     # still refreshed once
    hero = populated_db.execute(
        "SELECT name FROM heroes WHERE hero_id=?", (UNKNOWN_HERO,)).fetchone()
    assert hero["name"] == f"Unknown hero {UNKNOWN_HERO}"        # placeholder holds the FK
    assert populated_db.execute(
        "SELECT 1 FROM match_players WHERE match_id=? AND hero_id=?",
        (SHARED_MATCH, UNKNOWN_HERO)).fetchone() is not None


def test_refresh_assets_runs_at_most_once_per_daemon_run(populated_db, now):
    # Two unknown-hero matches in one run: the refresh fires only for the first.
    enqueue(populated_db, 61000, now)
    enqueue(populated_db, 61001, now)
    client = FakeClient()
    client.add("/v1/matches/61000/metadata", ok(metadata_with_unknown_hero(61000)))
    client.add("/v1/matches/61001/metadata", ok(metadata_with_unknown_hero(61001)))
    add_asset_routes(client, fixture_text("assets_heroes_match.json"))
    worker = make_worker(populated_db, client, now)

    assert worker.drain() == 2
    assert len(client.calls_matching("assets/heroes")) == 1     # once per daemon run
    assert populated_db.execute(
        "SELECT COUNT(*) FROM matches WHERE match_id IN (61000, 61001)"
    ).fetchone()[0] == 2


# ── Discovery tolerates a malformed / non-list 200 body ──────────────────────

@pytest.mark.parametrize("body", ["", "not json", '{"error":"nope"}'])
def test_discovery_tolerates_malformed_history_body(populated_db, now, body):
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history", (200, {}, body))

    assert run_discovery(populated_db, client, now=now) == 0    # no rows, no crash

    # The queue is untouched and a later drain step still runs cleanly.
    assert queue_rows(populated_db) == []
    worker = make_worker(populated_db, client, now)
    assert worker.step() is None


def test_discovery_skips_history_rows_without_match_id(populated_db, now):
    body = json.dumps([{"match_id": 42, "start_time": 1780538995},
                       {"not_a_match": 1}, {"match_id": None}])
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history", (200, {}, body))

    assert run_discovery(populated_db, client, now=now) == 1    # only the valid row
    rows = queue_rows(populated_db)
    assert [r["match_id"] for r in rows] == [42]


# ── account_match_summaries: instant data for a freshly imported account ──────

def _history_entry(match_id, *, start_time, player_team=0, match_result=0, **over):
    """One synthetic match-history row with all the fields discovery materializes."""
    row = {
        "match_id": match_id, "hero_id": 7, "start_time": start_time,
        "game_mode": 1, "player_team": player_team, "match_result": match_result,
        "player_kills": 4, "player_deaths": 5, "player_assists": 6,
        "net_worth": 30000, "last_hits": 100, "denies": 3, "match_duration_s": 1800,
    }
    row.update(over)
    return row


def summary_rows(db, account_id):
    return db.execute(
        "SELECT * FROM account_match_summaries WHERE account_id = ? ORDER BY match_id",
        (account_id,),
    ).fetchall()


def test_full_history_materializes_one_summary_per_row(populated_db, now):
    # 300 rows from a SINGLE api call, won alternating via player_team vs match_result.
    history = [
        _history_entry(1000 + i, start_time=1780538995 + i,
                       player_team=0, match_result=(i % 2))   # won when i even
        for i in range(300)
    ]
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history", ok(json.dumps(history)))

    discover_account(populated_db, client, ME, now=now)

    assert len(client.calls_matching("match-history")) == 1     # one API call
    rows = summary_rows(populated_db, ME)
    assert len(rows) == 300
    by_id = {r["match_id"]: r for r in rows}
    assert by_id[1000]["won"] == 1        # i=0 even -> match_result 0 == player_team 0
    assert by_id[1001]["won"] == 0        # i=1 odd  -> match_result 1 != player_team 0
    assert by_id[1000]["kills"] == 4 and by_id[1000]["net_worth"] == 30000


def test_summaries_upsert_is_idempotent(populated_db, now):
    history = [_history_entry(2000, start_time=1780538995, net_worth=30000)]
    client = FakeClient()
    # Second response corrects net_worth: proves ON CONFLICT DO UPDATE fires.
    updated = [_history_entry(2000, start_time=1780538995, net_worth=45000)]
    client.add(f"/v1/players/{ME}/match-history",
               ok(json.dumps(history)), ok(json.dumps(updated)))

    discover_account(populated_db, client, ME, now=now)
    discover_account(populated_db, client, ME, now=now)

    rows = summary_rows(populated_db, ME)
    assert len(rows) == 1                       # not duplicated
    assert rows[0]["net_worth"] == 45000        # updated in place


def test_summaries_skip_malformed_rows_and_keep_nulls(populated_db, now):
    history = [
        _history_entry(3000, start_time=1780538995),         # fully valid
        {"not_a_match": 1},                                  # no match_id -> skip
        {"match_id": 3001},                                  # no start_time -> skip
        # Present match_id + start_time but every stat absent -> stored as NULL, not 0.
        {"match_id": 3002, "start_time": 1780540000},
    ]
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history", ok(json.dumps(history)))

    discover_account(populated_db, client, ME, now=now)      # must not raise

    rows = summary_rows(populated_db, ME)
    assert [r["match_id"] for r in rows] == [3000, 3002]
    sparse = rows[1]
    for col in ("kills", "deaths", "assists", "net_worth", "last_hits",
                "denies", "duration_s", "won", "hero_id", "game_mode"):
        assert sparse[col] is None                           # NULL, never a fake 0


# ── Prioritized, fair, newest-first draining (schema v18) ────────────────────
#
# A fresh import used to drain its OLDEST matches first (global FIFO with an
# ascending match_id tiebreak) and could starve every other account. Now:
# discovery queues an account's newest 50 as priority-1 'pending' and the rest
# as 'backfill'; the drain loop serves priority tiers with per-account
# round-robin fairness (sync_state.last_drained_at) and newest-first order
# inside each account.

from ingest.discovery import PRIORITY_IMPORT_COUNT


def _import_history(n: int, start_id: int) -> str:
    """n synthetic history entries with match ids start_id .. start_id+n-1
    (higher id = newer match, matching the real API)."""
    return json.dumps([
        _history_entry(start_id + i, start_time=1780538995 + i) for i in range(n)
    ])


def _owner(db, match_id):
    return db.execute(
        "SELECT * FROM fetch_queue WHERE match_id = ?", (match_id,)).fetchone()


def test_first_import_prioritizes_newest_50_rest_backfill(populated_db, now):
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history", ok(_import_history(60, 1000)))

    assert discover_account(populated_db, client, ME, now=now) == 60

    rows = queue_rows(populated_db)
    assert len(rows) == 60
    newest_50 = [r for r in rows if r["match_id"] >= 1010]
    oldest_10 = [r for r in rows if r["match_id"] < 1010]
    assert all(r["status"] == "pending" and r["priority"] == 1 for r in newest_50)
    assert all(r["status"] == "backfill" and r["priority"] == 0 for r in oldest_10)
    assert all(r["discovered_for_account"] == ME for r in rows)
    assert len(newest_50) == PRIORITY_IMPORT_COUNT


def test_first_discoverer_wins_owner_and_priority(populated_db, now):
    # Match 1059 is already queued as BROTHER's backfill; ME's first import sees
    # it among the newest 50, but INSERT OR IGNORE keeps the original row.
    enqueue(populated_db, 1059, now, status="backfill", priority=0,
            discovered_for_account=BROTHER)
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history", ok(_import_history(60, 1000)))

    discover_account(populated_db, client, ME, now=now)

    row = _owner(populated_db, 1059)
    assert row["status"] == "backfill"
    assert row["priority"] == 0
    assert row["discovered_for_account"] == BROTHER


def test_synced_account_queues_new_matches_at_priority_1(populated_db, now):
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history",
               ok(_import_history(60, 1000)),          # first import
               ok(_import_history(1, 2000)))           # later cycle: one new game
    discover_account(populated_db, client, ME, now=now)   # ME is now synced

    discover_account(populated_db, client, ME, now=now)

    row = _owner(populated_db, 2000)
    assert row["status"] == "pending"
    assert row["priority"] == 1
    assert row["discovered_for_account"] == ME


def test_first_fetch_after_fresh_import_is_newest_match(populated_db, now):
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history", ok(_import_history(60, 1000)))
    client.add("/v1/matches/1059/metadata", ok(metadata_body_for(1059)))
    discover_account(populated_db, client, ME, now=now)
    worker = make_worker(populated_db, client, now)

    assert worker.step() == "fetched"

    first_fetch = client.calls_matching("/metadata")[0]
    assert "/v1/matches/1059/metadata" in first_fetch    # newest, not oldest


class _TickingNow(ManualNow):
    """A clock that moves 1s per reading. The round-robin cursor is a timestamp,
    so the interleave test needs time to actually pass between fetches (as it
    always does under the 1-req/5-s rate limit); a frozen ManualNow would stamp
    both accounts with the identical instant and reduce the tiebreak to
    account_id."""

    def __call__(self):
        self.advance(1)
        return self.t


def test_two_simultaneous_imports_interleave(populated_db):
    """Two 300-match first imports must alternate accounts across consecutive
    fetches (round-robin on sync_state.last_drained_at), each account newest
    first — one user's bulk import can't starve the other."""
    now = _TickingNow()
    add_account(populated_db, BROTHER, display_name="brother", now=now)
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history", ok(_import_history(300, 1000)))
    client.add(f"/v1/players/{BROTHER}/match-history", ok(_import_history(300, 5000)))
    for mid in (1299, 1298, 1297, 5299, 5298, 5297):     # the 6 we let drain
        client.add(f"/v1/matches/{mid}/metadata", ok(metadata_body_for(mid)))
    discover_account(populated_db, client, ME, now=now)
    discover_account(populated_db, client, BROTHER, now=now)
    worker = make_worker(populated_db, client, now)

    assert worker.drain(max_steps=6) == 6

    fetched = [int(u.split("/")[-2]) for u in client.calls_matching("/metadata")]
    owners = [ME if mid < 5000 else BROTHER for mid in fetched]
    # Strict alternation: consecutive fetches never repeat an account.
    assert owners[0::2] == [owners[0]] * 3
    assert owners[1::2] == [owners[1]] * 3
    assert owners[0] != owners[1]
    # Each account drains its own matches newest first.
    assert [m for m in fetched if m < 5000] == [1299, 1298, 1297]
    assert [m for m in fetched if m >= 5000] == [5299, 5298, 5297]
    # The round-robin cursor advanced for both accounts.
    for account in (ME, BROTHER):
        assert populated_db.execute(
            "SELECT last_drained_at FROM sync_state WHERE account_id = ?", (account,)
        ).fetchone()["last_drained_at"] is not None


def test_backfill_drains_only_when_no_fresh_work(populated_db, now):
    enqueue(populated_db, 100, now, status="backfill", priority=0,
            discovered_for_account=ME)
    enqueue(populated_db, 50, now, status="pending")     # priority-0 pending
    worker = make_worker(populated_db, FakeClient(), now)

    # A pending row beats backfill even at priority 0 and a lower match_id.
    assert worker._next_row()["match_id"] == 50

    # A DUE failed row also beats backfill...
    populated_db.execute("UPDATE fetch_queue SET status='fetched' WHERE match_id=50")
    populated_db.commit()
    enqueue(populated_db, 60, now, status="failed",
            next_retry_at=now().isoformat())
    assert worker._next_row()["match_id"] == 60

    # ...but a failed row that is NOT yet due does not block backfill.
    populated_db.execute(
        "UPDATE fetch_queue SET next_retry_at='2999-01-01T00:00:00+00:00'"
        " WHERE match_id=60")
    populated_db.commit()
    assert worker._next_row()["match_id"] == 100


def test_backfill_is_fair_across_accounts_and_newest_first(populated_db, now):
    add_account(populated_db, BROTHER, display_name="brother", now=now)
    for mid, owner in ((200, ME), (210, ME), (205, BROTHER)):
        enqueue(populated_db, mid, now, status="backfill", priority=0,
                discovered_for_account=owner)
    # BROTHER was served a moment ago; ME never -> ME's turn (NULL sorts first).
    populated_db.execute(
        "UPDATE sync_state SET last_drained_at=? WHERE account_id=?",
        (now().isoformat(), BROTHER))
    populated_db.commit()
    worker = make_worker(populated_db, FakeClient(), now)

    assert worker._next_row()["match_id"] == 210         # ME's NEWEST backfill

    # Once ME has been served more recently than BROTHER, it's BROTHER's turn.
    now.advance(10)
    populated_db.execute(
        "UPDATE fetch_queue SET status='fetched' WHERE match_id=210")
    populated_db.execute(
        "UPDATE sync_state SET last_drained_at=? WHERE account_id=?",
        (now().isoformat(), ME))
    populated_db.commit()
    assert worker._next_row()["match_id"] == 205


def test_backfill_beats_deferred(populated_db, now):
    enqueue(populated_db, 111, now, status="deferred",
            next_retry_at=now().isoformat(), deferred_since=now().isoformat())
    enqueue(populated_db, 222, now, status="backfill", priority=0,
            discovered_for_account=ME)
    worker = make_worker(populated_db, FakeClient(), now)

    assert worker._next_row()["match_id"] == 222         # backfill first

    populated_db.execute("UPDATE fetch_queue SET status='fetched' WHERE match_id=222")
    populated_db.commit()
    assert worker._next_row()["match_id"] == 111         # deferred still last


def test_priority0_orders_by_discovered_at_then_newest(populated_db, now):
    # Same discovery batch (equal discovered_at): newest match first. An older
    # batch still drains before a newer one (FIFO across batches).
    enqueue(populated_db, 300, now, status="pending")
    enqueue(populated_db, 400, now, status="pending")
    now.advance(60)
    enqueue(populated_db, 500, now, status="pending")
    worker = make_worker(populated_db, FakeClient(), now)

    assert worker._next_row()["match_id"] == 400          # newest of the old batch


def test_prioritized_work_beats_priority0(populated_db, now):
    enqueue(populated_db, 900, now, status="pending")     # priority 0, queued first
    now.advance(60)
    enqueue(populated_db, 800, now, status="pending", priority=1,
            discovered_for_account=ME)
    worker = make_worker(populated_db, FakeClient(), now)

    assert worker._next_row()["match_id"] == 800          # priority wins over FIFO


def test_last_drained_at_stamped_even_on_failure(populated_db, now):
    # Fairness tracks API attempts, not successes: a 404 outcome still advances
    # the account's round-robin cursor.
    enqueue(populated_db, 700, now, status="pending", priority=1,
            discovered_for_account=ME)
    client = FakeClient()
    client.add("/metadata", (404, {}, "not found"))
    worker = make_worker(populated_db, client, now)

    assert worker.step() == "failed"

    stamp = populated_db.execute(
        "SELECT last_drained_at FROM sync_state WHERE account_id = ?", (ME,)
    ).fetchone()["last_drained_at"]
    assert stamp == now().isoformat()


def test_summaries_store_street_brawl_mode(populated_db, now):
    # The recorded fixture's first row (match 86704689) is a game_mode 4 (Street
    # Brawl) game: player_team 1, match_result 0 -> a loss.
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history", ok(fixture_text(f"match_history_{ME}.json")))

    discover_account(populated_db, client, ME, now=now)

    brawl = populated_db.execute(
        "SELECT * FROM account_match_summaries WHERE account_id = ? AND match_id = 86704689",
        (ME,),
    ).fetchone()
    assert brawl["game_mode"] == "4"        # mode int stored as text
    assert brawl["won"] == 0                # player_team 1 != match_result 0
