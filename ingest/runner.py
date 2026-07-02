"""How the three loops are actually run.

Because all state lives in SQLite, run-once and run-daemon are the *same
code* invoked differently — the graduation path from "a script you run when
you play" to "a persistent daemon" with no rearchitecting.
"""
import logging
import time
from datetime import timedelta

from ingest.discovery import discover_all
from ingest.drain import DrainWorker
from ingest.maintenance import run_maintenance
from ingest.ranks import run_rank_sync
from ingest.util import utcnow

log = logging.getLogger(__name__)

MAINTENANCE_INTERVAL_S = 24 * 3600
DISCOVERY_INTERVAL_S = 30 * 60
IDLE_SLEEP_S = 60


def _accounts_with_new_matches(new_by_account: dict[int, int]) -> list[int]:
    """The accounts discovery just queued new matches for -- the gate for rank
    ingestion (skip accounts with nothing new this cycle)."""
    return [account_id for account_id, count in new_by_account.items() if count > 0]


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
    last_discovery = None
    iterations = 0
    log.info("daemon started")
    try:
        while max_iterations is None or iterations < max_iterations:
            iterations += 1
            current = now()

            if maintenance_due(conn, now=now):
                run_maintenance(conn, client, now=now)

            if last_discovery is None or (current - last_discovery).total_seconds() >= DISCOVERY_INTERVAL_S:
                new_by_account = discover_all(conn, client, now=now)
                run_rank_sync(
                    conn, client, _accounts_with_new_matches(new_by_account), now=now)
                last_discovery = current

            if worker.step() is None:
                # Queue empty: idle a minute before looking again.
                sleep(IDLE_SLEEP_S)
    except KeyboardInterrupt:
        log.info("daemon stopped (keyboard interrupt)")
