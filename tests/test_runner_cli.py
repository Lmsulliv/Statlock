"""Tests for ingest.runner (run-once flow) and the ingest CLI (status, add-account)."""
import json

import pytest

import ingest.drain as drain_module
import ingest.runner as runner_module
from ingest.accounts import add_account
from ingest.maintenance import DECADE_BRACKETS
from ingest.runner import (
    IDLE_SLEEP_S, UNEXPECTED_ERROR_SLEEP_S, maintenance_due, run_daemon, run_once,
)
from ingest.__main__ import main as cli_main

from tests.fakes import FakeClient, FakeSleep, ManualNow, fixture_text, load_fixture, ok

ME = 891231519
N_BRACKETS = len(DECADE_BRACKETS)


def _no_idle_baselines(monkeypatch):
    """Disable the daemon's idle-time baseline chunk for tests that aren't about
    it (an empty queue would otherwise trigger a real span refresh)."""
    monkeypatch.setattr(runner_module, "refresh_baselines", lambda *a, **k: 0)


def full_client() -> FakeClient:
    client = FakeClient()
    client.add(f"/v1/players/{ME}/match-history", ok(fixture_text(f"match_history_{ME}.json")))
    client.add(f"/v1/players/{ME}/mmr-history", ok(fixture_text("mmr_history_891231519.json")))
    meta = load_fixture("match_metadata_86714494.json")
    for match_id in (86704689, 86707774, 86714494):
        meta["match_info"]["match_id"] = match_id
        client.add(f"/v1/matches/{match_id}/metadata", ok(json.dumps(meta)))
    client.add("hero-counter-stats", ok(fixture_text("counter_stats.json")))
    client.add("item-stats", ok(fixture_text("item_stats_bucket_hero.json")))
    client.add("/v1/assets/heroes", ok(fixture_text("assets_heroes_match.json")))
    client.add("/v1/assets/items", ok(fixture_text("assets_items_match.json")))
    client.add("/v1/assets/ranks", ok(fixture_text("assets_ranks.json")))
    client.add("GetNewsForApp", ok(fixture_text("steam_news.json")))
    return client


def test_run_once_does_maintenance_discovery_drain(db):
    now = ManualNow()
    # Migration 013 pre-seeds 12 curated eras; clear them so ingested matches bind
    # to this single epoch era.
    db.execute("DELETE FROM patch_eras")
    db.execute("INSERT INTO patch_eras(label, started_at) VALUES('all', '1970-01-01T00:00:00+00:00')")
    db.commit()
    add_account(db, ME, display_name="me", is_self=True, now=now)
    client = full_client()

    run_once(db, client, now=now, sleep=FakeSleep(now))

    # Maintenance ran (no stamp existed -> due), discovery queued 3 matches,
    # drain fetched them all.
    assert db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 3
    statuses = [r["status"] for r in db.execute("SELECT status FROM fetch_queue")]
    assert statuses == ["fetched"] * 3
    assert db.execute(
        "SELECT value FROM worker_meta WHERE key='last_maintenance_at'"
    ).fetchone() is not None
    # Discovery found new matches, so rank ingestion fired and populated the
    # rank-over-time series.
    assert db.execute("SELECT COUNT(*) FROM account_rank_history").fetchone()[0] > 0
    assert len(client.calls_matching("mmr-history")) == 1

    # Second run within 24h: maintenance skipped (still one news poll). Discovery
    # finds nothing new, so the rank fetch is gated off (still just one call).
    run_once(db, client, now=now, sleep=FakeSleep(now))
    assert len(client.calls_matching("GetNewsForApp")) == 1
    assert len(client.calls_matching("mmr-history")) == 1


def test_maintenance_due(db):
    now = ManualNow()
    assert maintenance_due(db, now=now)  # never ran
    db.execute(
        "INSERT INTO worker_meta(key, value) VALUES('last_maintenance_at', ?)",
        (now().isoformat(),),
    )
    db.commit()
    assert not maintenance_due(db, now=now)
    now.advance(25 * 3600)
    assert maintenance_due(db, now=now)


# ── Daemon last-resort guard ─────────────────────────────────────────────────
#
# An unhandled exception in one iteration must NOT kill the daemon (which would
# halt ingestion until someone notices). The per-iteration body is wrapped: it
# logs the traceback, sleeps 30s, and moves on. KeyboardInterrupt is the one
# exception that still exits cleanly.

def _skip_maintenance(db, now):
    db.execute(
        "INSERT INTO worker_meta(key, value) VALUES('last_maintenance_at', ?)",
        (now().isoformat(),),
    )
    db.commit()


def test_daemon_survives_unexpected_iteration_error(db, monkeypatch):
    now = ManualNow()
    _skip_maintenance(db, now)          # no tracked accounts -> discovery is a no-op
    _no_idle_baselines(monkeypatch)
    sleep = FakeSleep()
    calls = {"n": 0}

    def flaky_step(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return None                     # second iteration: queue empty

    monkeypatch.setattr(drain_module.DrainWorker, "step", flaky_step)

    run_daemon(db, FakeClient(), now=now, sleep=sleep, max_iterations=2)

    assert calls["n"] == 2              # the daemon kept going after the crash
    assert UNEXPECTED_ERROR_SLEEP_S in sleep.calls   # slept 30s instead of exiting


def test_daemon_immediately_picks_up_new_account(db, monkeypatch):
    """A brand-new account (sync_state.last_synced_at IS NULL) is discovered and
    rank-synced on the very next iteration, WITHOUT waiting for the 30-min discovery
    timer -- the whole point of the immediate-pickup path."""
    now = ManualNow()
    _skip_maintenance(db, now)              # keep the test focused on the pickup path
    _no_idle_baselines(monkeypatch)         # idle iteration 1 must SLEEP (see below)
    client = full_client()

    added = {"done": False}

    def sleep(seconds):
        # The first idle sleep happens after iteration 1 finds an empty queue; that
        # is where a fresh account is imported (as POST /api/accounts would).
        if not added["done"]:
            add_account(db, ME, display_name="me", is_self=True, now=now)
            added["done"] = True
        now.advance(seconds)               # only ~60s elapses -- far under 30 min

    run_daemon(db, client, now=now, sleep=sleep, max_iterations=2)

    # Discovery timer was NOT due on iteration 2 (only seconds passed), yet the new
    # account got its match-history and mmr-history fetched exactly once each.
    assert len(client.calls_matching("match-history")) == 1
    assert len(client.calls_matching("mmr-history")) == 1
    # Summaries, queue, and the rank series are all populated within that iteration.
    assert db.execute(
        "SELECT COUNT(*) FROM account_match_summaries WHERE account_id = ?", (ME,)
    ).fetchone()[0] == 3
    assert db.execute("SELECT COUNT(*) FROM fetch_queue").fetchone()[0] == 3
    assert db.execute(
        "SELECT COUNT(*) FROM account_rank_history WHERE account_id = ?", (ME,)
    ).fetchone()[0] > 0


def test_daemon_touches_heartbeat_each_iteration(db, monkeypatch, tmp_path):
    """The daemon writes a liveness file the container healthcheck reads. A worker
    that is alive but wedged (loop stuck) is caught by a stale heartbeat -- something
    restart-on-crash alone can't detect. The file lands under DEADLOCK_STATE_DIR
    (the mounted volume in prod), not the ephemeral image filesystem."""
    from tracker.paths import worker_heartbeat_path

    monkeypatch.setenv("DEADLOCK_STATE_DIR", str(tmp_path))
    now = ManualNow()
    _skip_maintenance(db, now)
    _no_idle_baselines(monkeypatch)

    assert not worker_heartbeat_path().exists()
    run_daemon(db, FakeClient(), now=now, sleep=FakeSleep(), max_iterations=2)
    assert worker_heartbeat_path().exists()


def test_daemon_keyboard_interrupt_still_exits(db, monkeypatch):
    now = ManualNow()
    _skip_maintenance(db, now)

    def interrupt_step(self):
        raise KeyboardInterrupt()

    monkeypatch.setattr(drain_module.DrainWorker, "step", interrupt_step)

    # Must return (clean shutdown), not raise or loop forever.
    run_daemon(db, FakeClient(), now=now, sleep=FakeSleep(), max_iterations=5)


# ── Baselines off the critical path ──────────────────────────────────────────
#
# The daemon no longer front-loads the ~470-call baseline rebuild. The nightly
# maintenance's FAST part (revive, assets, personas, era candidates) still runs
# when due, but baselines refresh incrementally: only when a drain step found
# nothing eligible, one due era span at a time, continuing the loop (not
# sleeping) after each span so new drain work is noticed immediately.

def _seed_two_eras(db):
    db.execute("DELETE FROM patch_eras")
    db.execute("INSERT INTO patch_eras(label, started_at) VALUES('jan', '2026-01-01T00:00:00+00:00')")
    db.execute("INSERT INTO patch_eras(label, started_at) VALUES('jun', '2026-06-01T00:00:00+00:00')")
    db.commit()


def test_daemon_baselines_gated_on_empty_queue_one_span_at_a_time(db, monkeypatch):
    """While the queue holds ANY eligible work, no baseline span fires; each idle
    pass refreshes exactly one span and keeps looping instead of sleeping."""
    now = ManualNow()
    _skip_maintenance(db, now)
    outcomes = iter(["fetched", "fetched", None, None, None])
    monkeypatch.setattr(drain_module.DrainWorker, "step",
                        lambda self: next(outcomes))
    refresh_calls = []

    def fake_refresh(conn, client, *, now, max_spans=None):
        refresh_calls.append(max_spans)
        return 1 if len(refresh_calls) <= 2 else 0     # 2 due spans, then done

    monkeypatch.setattr(runner_module, "refresh_baselines", fake_refresh)
    sleep = FakeSleep()

    run_daemon(db, FakeClient(), now=now, sleep=sleep, max_iterations=5)

    assert refresh_calls == [1, 1, 1]     # only on the 3 idle passes, one span each
    # The two refreshing passes continued the loop; only the empty third slept.
    assert sleep.calls == [IDLE_SLEEP_S]


def test_daemon_idle_refreshes_one_real_span_per_pass(db):
    """End-to-end: an idle daemon works through due era spans one per pass."""
    now = ManualNow()
    _skip_maintenance(db, now)
    _seed_two_eras(db)                     # 3 spans due: jan, jun, all-time
    client = FakeClient()
    client.add("hero-counter-stats", ok(fixture_text("counter_stats.json")))
    client.add("item-stats", ok(fixture_text("item_stats_bucket_hero.json")))
    sleep = FakeSleep(now)

    run_daemon(db, client, now=now, sleep=sleep, max_iterations=2)

    # Two idle passes -> exactly two spans' worth of calls, and no idle sleep.
    assert len(client.calls) == 2 * 3 * N_BRACKETS
    assert IDLE_SLEEP_S not in sleep.calls
    assert db.execute(
        "SELECT COUNT(*) FROM baseline_refresh_state").fetchone()[0] == 2


def test_daemon_sleeps_when_queue_empty_and_no_span_due(db):
    now = ManualNow()
    _skip_maintenance(db, now)
    _seed_two_eras(db)
    # Every span was refreshed this instant -> nothing due, nothing fetched.
    for era_id in [r["era_id"] for r in db.execute("SELECT era_id FROM patch_eras")] + [0]:
        db.execute(
            "INSERT INTO baseline_refresh_state(era_id, last_refreshed_at) VALUES (?, ?)",
            (era_id, now().isoformat()))
    db.execute("INSERT INTO baseline_snapshots(fetched_at, notes) VALUES (?, 't')",
               (now().isoformat(),))
    db.commit()
    client = FakeClient()                  # no routes: any call would fail loudly
    sleep = FakeSleep(now)

    run_daemon(db, client, now=now, sleep=sleep, max_iterations=1)

    assert client.calls == []
    assert IDLE_SLEEP_S in sleep.calls


def test_fresh_import_drains_all_prioritized_before_any_baseline_span(db):
    """Acceptance: on a fresh database with one imported account, every
    prioritized match (and here the whole 55-match queue) is fetched before the
    first baseline span fires -- the first user sees matches in minutes, not
    after an hour of baseline calls."""
    now = ManualNow()
    db.execute("DELETE FROM patch_eras")
    db.execute("INSERT INTO patch_eras(label, started_at) VALUES('all', '1970-01-01T00:00:00+00:00')")
    db.commit()
    add_account(db, ME, display_name="me", is_self=True, now=now)

    client = FakeClient()
    history = [{"match_id": 1000 + i, "start_time": 1780538995 + i} for i in range(55)]
    client.add(f"/v1/players/{ME}/match-history", ok(json.dumps(history)))
    client.add(f"/v1/players/{ME}/mmr-history", ok(fixture_text("mmr_history_891231519.json")))
    meta = load_fixture("match_metadata_86714494.json")
    for i in range(55):
        meta["match_info"]["match_id"] = 1000 + i
        client.add(f"/v1/matches/{1000 + i}/metadata", ok(json.dumps(meta)))
    client.add("hero-counter-stats", ok(fixture_text("counter_stats.json")))
    client.add("item-stats", ok(fixture_text("item_stats_bucket_hero.json")))
    client.add("/v1/assets/heroes", ok(fixture_text("assets_heroes_match.json")))
    client.add("/v1/assets/items", ok(fixture_text("assets_items_match.json")))
    client.add("/v1/assets/ranks", ok(fixture_text("assets_ranks.json")))
    client.add("GetNewsForApp", ok(fixture_text("steam_news.json")))

    run_daemon(db, client, now=now, sleep=FakeSleep(now), max_iterations=60)

    # Every metadata fetch happened before the first baseline call.
    metadata_idx = [i for i, u in enumerate(client.calls) if "/metadata" in u]
    baseline_idx = [i for i, u in enumerate(client.calls) if "hero-counter-stats" in u]
    assert len(metadata_idx) == 55
    assert baseline_idx, "idle passes after the drain must refresh baselines"
    assert max(metadata_idx) < min(baseline_idx)
    # The whole import landed: 50 prioritized + 5 backfill, all fetched.
    assert db.execute(
        "SELECT COUNT(*) FROM fetch_queue WHERE status = 'fetched'"
    ).fetchone()[0] == 55


def test_run_once_still_refreshes_all_due_spans(db):
    """run-once keeps the manual catch-up shape: the full maintenance (fast part
    plus EVERY due baseline span) runs before discovery and drain."""
    now = ManualNow()
    _seed_two_eras(db)                     # 3 spans due: jan, jun, all-time
    client = FakeClient()
    client.add("hero-counter-stats", ok(fixture_text("counter_stats.json")))
    client.add("item-stats", ok(fixture_text("item_stats_bucket_hero.json")))
    client.add("/v1/assets/heroes", ok(fixture_text("assets_heroes_match.json")))
    client.add("/v1/assets/items", ok(fixture_text("assets_items_match.json")))
    client.add("/v1/assets/ranks", ok(fixture_text("assets_ranks.json")))
    client.add("GetNewsForApp", ok(fixture_text("steam_news.json")))

    run_once(db, client, now=now, sleep=FakeSleep(now))

    assert len(client.calls_matching("hero-counter-stats")) == 2 * N_BRACKETS * 3
    assert db.execute(
        "SELECT COUNT(*) FROM baseline_refresh_state").fetchone()[0] == 3


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_cli_add_account_and_status(tmp_path, capsys):
    db_path = str(tmp_path / "cli.db")

    cli_main(["--db", db_path, "add-account", "76561198851497247", "--name", "me", "--self"])
    out = capsys.readouterr().out
    assert "891231519" in out

    cli_main(["--db", db_path, "status"])
    out = capsys.readouterr().out
    # Queue depth and counts by status, per the observability section.
    assert "depth" in out.lower()
    assert "pending" in out.lower()
    assert "891231519" in out


def test_cli_status_counts(db, tmp_path, capsys, monkeypatch):
    db_path = tmp_path / "counts.db"
    from tracker.db import connect
    from tracker.migrate import migrate
    conn = connect(db_path)
    migrate(conn)
    conn.execute("INSERT INTO fetch_queue(match_id, discovered_at) VALUES (1, '2026-06-11')")
    conn.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at, status) VALUES (2, '2026-06-11', 'fetched')"
    )
    conn.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at, status, attempts) "
        "VALUES (3, '2026-06-11', 'unavailable', 5)"
    )
    conn.execute(
        "INSERT INTO fetch_queue(match_id, discovered_at, status) "
        "VALUES (4, '2026-06-11', 'backfill')"
    )
    conn.commit()
    conn.close()

    cli_main(["--db", str(db_path), "status"])
    out = capsys.readouterr().out.lower()
    assert "pending" in out and "fetched" in out and "unavailable" in out
    # Backfill is visible as its own bucket, but the depth line stays
    # pending + retryable failed (the fresh backlog a user actually waits on).
    assert "backfill" in out
    assert "depth (pending + retryable failed): 1" in out
