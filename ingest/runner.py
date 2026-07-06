"""How the three loops are actually run.

Because all state lives in SQLite, run-once and run-daemon are the *same
code* invoked differently — the graduation path from "a script you run when
you play" to "a persistent daemon" with no rearchitecting.
"""
import logging
import time
from datetime import timedelta

from ingest.discovery import discover_account, discover_all, discover_due
from ingest.drain import DrainWorker
from ingest.maintenance import refresh_baselines, run_fast_maintenance, run_maintenance
from ingest.ranks import run_rank_sync
from ingest.util import utcnow
from tracker.paths import worker_heartbeat_path

log = logging.getLogger(__name__)

MAINTENANCE_INTERVAL_S = 24 * 3600
IDLE_SLEEP_S = 60
# Last-resort backoff: how long the daemon pauses after an UNEXPECTED error in an
# iteration before trying again, rather than crashing the whole process.
UNEXPECTED_ERROR_SLEEP_S = 30


def _touch_heartbeat() -> None:
    """Stamp the liveness file the container healthcheck reads. A daemon that is
    running but wedged (stuck loop, not a crash) is caught by a stale heartbeat --
    restart-on-crash alone can't detect that. Best-effort: an unwritable state
    dir must never take down the daemon, so we log and carry on."""
    try:
        path = worker_heartbeat_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time()))
    except OSError:
        log.warning("could not write worker heartbeat", exc_info=True)


def _accounts_with_new_matches(new_by_account: dict[int, int]) -> list[int]:
    """The accounts discovery just queued new matches for -- the gate for rank
    ingestion (skip accounts with nothing new this cycle)."""
    return [account_id for account_id, count in new_by_account.items() if count > 0]


def _never_synced_accounts(conn) -> list[int]:
    """Tracked accounts that discovery has never run for (sync_state.last_synced_at
    IS NULL). These are freshly imported -- via POST /api/accounts or a first Steam
    login -- and get an immediate discovery + rank sync so their summaries and rank
    series appear within seconds instead of waiting up to 30 min for the timer. The
    LEFT JOIN also catches a tracked account somehow missing its sync_state row
    (add_account normally creates it). Cheap: a two-table join over a tiny registry."""
    rows = conn.execute(
        "SELECT ta.account_id FROM tracked_accounts ta"
        " LEFT JOIN sync_state ss ON ss.account_id = ta.account_id"
        " WHERE ss.last_synced_at IS NULL"
    ).fetchall()
    return [r["account_id"] for r in rows]


def maintenance_due(conn, *, now=utcnow) -> bool:
    row = conn.execute(
        "SELECT value FROM worker_meta WHERE key = 'last_maintenance_at'"
    ).fetchone()
    if row is None:
        return True
    last = row["value"]
    cutoff = (now() - timedelta(seconds=MAINTENANCE_INTERVAL_S)).isoformat()
    return last < cutoff


def run_once(conn, client, *, now=utcnow, sleep=None) -> dict:
    """Maintenance (if due) -> discovery -> drain until nothing is eligible.
    The simplest shape: run it when you play, it catches up and exits."""
    if maintenance_due(conn, now=now):
        log.info("run-once: maintenance is due")
        run_maintenance(conn, client, now=now)

    new_by_account = discover_all(conn, client, now=now)
    discovered = sum(new_by_account.values())
    # Rank ingestion is gated on new matches: mmr-history only changes when you
    # play, so we only re-fetch an account's rank series when discovery just
    # queued new matches for it (avoids a wasted request every cycle).
    ranked = run_rank_sync(
        conn, client, _accounts_with_new_matches(new_by_account), now=now)
    worker = DrainWorker(conn, client, now=now, sleep=sleep)
    steps = worker.drain()
    log.info("run-once: discovered %d, ranks %d, drained %d", discovered, ranked, steps)
    return {"discovered": discovered, "ranks": ranked, "drained": steps}


def run_daemon(conn, client, *, now=utcnow, sleep=time.sleep, max_iterations=None) -> None:
    """Persistent shape: discovery every 30 min, drain continuously, nightly
    maintenance. Crash-safe — all progress is in the database, so killing and
    restarting resumes exactly where it left off."""
    worker = DrainWorker(conn, client, now=now, sleep=sleep)
    iterations = 0
    log.info("daemon started")
    try:
        while max_iterations is None or iterations < max_iterations:
            iterations += 1

            # Liveness signal for the container healthcheck: refreshed every pass,
            # including idle ones, so a healthy-but-idle daemon still looks alive.
            _touch_heartbeat()

            # Last-resort guard: a single unexpected exception in one iteration
            # (a bug, an exotic API response a handler missed) must NOT kill the
            # daemon, which would halt ingestion until someone notices. Log the
            # traceback, back off, and move on -- crash-safety (all progress is in
            # the DB) means the next iteration resumes cleanly. KeyboardInterrupt
            # is a BaseException, so it slips past `except Exception` and reaches
            # the outer handler for a clean shutdown.
            try:
                # Only the FAST maintenance runs on the nightly schedule; the
                # ~470-call baseline refresh is chunked into the idle branch
                # below so it can never block user-facing draining.
                if maintenance_due(conn, now=now):
                    run_fast_maintenance(conn, client, now=now)

                # Discovery is now per-iteration but self-throttled: discover_due
                # visits only accounts whose next_discovery_at has lapsed (or that
                # the web process flagged in discovery_requests), so an idle
                # account is checked every 6-24 h instead of every 30 min. The due
                # query is one cheap indexed SELECT, so running it each iteration
                # is what lets a web-side request be picked up on the very next
                # pass rather than waiting out a timer.
                new_by_account = discover_due(conn, client, now=now)
                if new_by_account:
                    run_rank_sync(
                        conn, client, _accounts_with_new_matches(new_by_account), now=now)

                # Immediate pickup of freshly imported accounts: don't make a brand-
                # new account wait up to 30 min for the discovery timer. Runs AFTER
                # the timed block so on the cycle discover_all just ran (which stamps
                # last_synced_at for everyone) this query finds nothing and no history
                # is fetched twice. discover_account stamps last_synced_at, so each
                # new account is picked up exactly once. Rank sync fires
                # unconditionally here: a new account needs its initial rank series
                # regardless of the new-matches gate that governs the steady state.
                new_accounts = _never_synced_accounts(conn)
                if new_accounts:
                    for account_id in new_accounts:
                        discover_account(conn, client, account_id, now=now)
                    run_rank_sync(conn, client, new_accounts, now=now)

                if worker.step() is None:
                    # Nothing eligible to drain. Use the idle gap for at most ONE
                    # due baseline span (~36 calls), then loop again immediately:
                    # new drain work gets noticed right away, and a fresh
                    # database's full baseline rebuild converges over its idle
                    # gaps one span at a time (baseline_refresh_state records
                    # per-era progress, so this is resumable by construction).
                    if refresh_baselines(conn, client, now=now, max_spans=1) == 0:
                        # Queue empty and no span due: idle a minute.
                        sleep(IDLE_SLEEP_S)
            except Exception:
                log.exception("daemon iteration failed; backing off %ds", UNEXPECTED_ERROR_SLEEP_S)
                sleep(UNEXPECTED_ERROR_SLEEP_S)
    except KeyboardInterrupt:
        log.info("daemon stopped (keyboard interrupt)")
