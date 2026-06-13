"""How the three loops are actually run.

Because all state lives in SQLite, run-once and run-daemon are the *same
code* invoked differently — the graduation path from "a script you run when
you play" to "a persistent daemon" with no rearchitecting.
"""
import logging
import time
from datetime import timedelta

from ingest.discovery import run_discovery
from ingest.drain import DrainWorker
from ingest.maintenance import run_maintenance
from ingest.util import utcnow

log = logging.getLogger(__name__)

MAINTENANCE_INTERVAL_S = 24 * 3600
DISCOVERY_INTERVAL_S = 30 * 60
IDLE_SLEEP_S = 60


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

    discovered = run_discovery(conn, client, now=now)
    worker = DrainWorker(conn, client, now=now, sleep=sleep)
    steps = worker.drain()
    log.info("run-once: discovered %d, drained %d", discovered, steps)
    return {"discovered": discovered, "drained": steps}


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
                run_discovery(conn, client, now=now)
                last_discovery = current

            if worker.step() is None:
                # Queue empty: idle a minute before looking again.
                sleep(IDLE_SLEEP_S)
    except KeyboardInterrupt:
        log.info("daemon stopped (keyboard interrupt)")
