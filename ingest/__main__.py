"""CLI entry point: python -m ingest <command>.

Commands:
    run-once          maintenance (if due) -> discovery -> drain to empty, then exit
    run-daemon        persistent loop running all three schedules
    status            queue depth, counts by status, sync freshness, candidates
    add-account       register a tracked account (accepts ID / SteamID64 / URL)
    reprocess-archive rebuild kill_events (and recover unstorable matches) from
                      the raw_api_responses archive, no API calls
"""
import argparse
import logging
import sys
from pathlib import Path

from ingest.accounts import add_account
from ingest.client import Client
from ingest.ratelimit import DEFAULT_STAMP, TokenBucket
from ingest.reprocess import reprocess_archive
from ingest.runner import run_daemon, run_once
from tracker.db import connect
from tracker.migrate import migrate

DEFAULT_DB = Path("data") / "tracker.db"


def _build_client() -> Client:
    bucket = TokenBucket(stamp_path=DEFAULT_STAMP)
    return Client(bucket)


def _open_db(db_path: str):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    migrate(conn)
    return conn


def cmd_add_account(conn, args) -> None:
    account_id = add_account(
        conn, args.identifier, display_name=args.name, is_self=args.self_account
    )
    label = f" ({args.name})" if args.name else ""
    flag = " [self]" if args.self_account else ""
    print(f"Tracking account {account_id}{label}{flag}.")


def cmd_status(conn, args) -> None:
    counts = {
        r["status"]: r["n"]
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM fetch_queue GROUP BY status"
        ).fetchall()
    }
    depth = counts.get("pending", 0) + counts.get("failed", 0)

    print("Queue")
    print(f"  depth (pending + retryable failed): {depth}")
    for status in ("pending", "fetched", "failed", "unavailable"):
        print(f"  {status:<12} {counts.get(status, 0)}")

    print("Tracked accounts")
    accounts = conn.execute(
        "SELECT t.account_id, t.display_name, t.is_self, s.last_synced_at, s.last_match_id"
        " FROM tracked_accounts t LEFT JOIN sync_state s USING (account_id)"
        " ORDER BY t.account_id"
    ).fetchall()
    if not accounts:
        print("  (none - add one with: python -m ingest add-account <id>)")
    for a in accounts:
        name = a["display_name"] or ""
        self_flag = " [self]" if a["is_self"] else ""
        synced = a["last_synced_at"] or "never"
        print(f"  {a['account_id']} {name}{self_flag}: last synced {synced}, "
              f"high-water {a['last_match_id']}")

    pending_candidates = conn.execute(
        "SELECT COUNT(*) AS n FROM era_candidates WHERE status = 'pending'"
    ).fetchone()["n"]
    print(f"Pending era candidates: {pending_candidates}")

    maint = conn.execute(
        "SELECT value FROM worker_meta WHERE key = 'last_maintenance_at'"
    ).fetchone()
    print(f"Last maintenance: {maint['value'] if maint else 'never'}")


def cmd_run_once(conn, args) -> None:
    result = run_once(conn, _build_client())
    print(f"Discovered {result['discovered']} new match(es), drained {result['drained']}.")


def cmd_run_daemon(conn, args) -> None:
    run_daemon(conn, _build_client())


def cmd_reprocess_archive(conn, args) -> None:
    result = reprocess_archive(conn)
    print(f"Reprocessed archive: recovered {result['matches_recovered']} match(es), "
          f"rebuilt {result['kill_events_rebuilt']} kill event(s).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ingest", description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="path to the SQLite database")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run-once", help="discovery + drain once, then exit")
    sub.add_parser("run-daemon", help="run all three loops continuously")
    sub.add_parser("status", help="print queue depth and counts by status")
    sub.add_parser("reprocess-archive",
                   help="rebuild kill_events from the archive (no API calls)")

    add = sub.add_parser("add-account", help="track a new account")
    add.add_argument("identifier", help="account ID, SteamID64, or profile URL")
    add.add_argument("--name", help="display name")
    add.add_argument("--self", dest="self_account", action="store_true",
                     help="mark as your own account (used by the my-stats views)")
    return parser


_DISPATCH = {
    "run-once": cmd_run_once,
    "run-daemon": cmd_run_daemon,
    "status": cmd_status,
    "add-account": cmd_add_account,
    "reprocess-archive": cmd_reprocess_archive,
}


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    conn = _open_db(args.db)
    try:
        _DISPATCH[args.command](conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
