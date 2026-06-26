"""Database migration: apply pending schema steps and advance user_version.

Usage:
    python -m tracker.migrate <db_path>

`PRAGMA user_version` is SQLite's built-in integer slot for schema
versioning. Migrations are an ordered list of SQL files; a database at
version N gets every step after the Nth applied, in order. Re-running is
a no-op — safe to call on every startup.
"""
import sqlite3
import sys
from pathlib import Path

from tracker.db import connect

_DB_DIR = Path(__file__).parent.parent / "db"

# Step k upgrades a database from user_version k to k+1.
_STEPS = [
    _DB_DIR / "schema.sql",                          # 0 -> 1: full initial schema
    _DB_DIR / "migrations" / "002_ingest_worker.sql",  # 1 -> 2: era_candidates, worker_meta
    _DB_DIR / "migrations" / "003_ranks_and_same_lane.sql",  # 2 -> 3: ranks, same_lane
    _DB_DIR / "migrations" / "004_baseline_refresh_state.sql",  # 3 -> 4: staggered refresh
    _DB_DIR / "migrations" / "005_player_slot.sql",  # 4 -> 5: player_slot per-match key
    _DB_DIR / "migrations" / "006_kill_events.sql",  # 5 -> 6: per-kill attribution table
    _DB_DIR / "migrations" / "007_deferred_status.sql",  # 6 -> 7: deferred not-yet-parsed matches
    _DB_DIR / "migrations" / "008_steam_personas.sql",  # 7 -> 8: steam_personas
    _DB_DIR / "migrations" / "009_account_labels.sql",  # 8 -> 9: account_labels
    _DB_DIR / "migrations" / "010_laning_stats.sql",  # 9 -> 10: end-of-laning snapshot table
    _DB_DIR / "migrations" / "011_per_user_identity.sql",  # 10 -> 11: users, user_accounts, account_labels.user_id
    _DB_DIR / "migrations" / "012_auth.sql",  # 11 -> 12: users.steam_account_id, sessions
    _DB_DIR / "migrations" / "013_curated_eras.sql",  # 12 -> 13: reseed patch_eras to 12 curated eras
]


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= len(_STEPS):
        print(f"Already at schema version {version}, nothing to do.")
        return

    for step, sql_file in enumerate(_STEPS[version:], start=version + 1):
        conn.executescript(sql_file.read_text(encoding="utf-8"))
        # executescript commits automatically; set user_version separately.
        conn.execute(f"PRAGMA user_version = {step}")
        conn.commit()
        print(f"Migrated to schema version {step} ({sql_file.name}).")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m tracker.migrate <db_path>", file=sys.stderr)
        sys.exit(1)
    db_path = Path(sys.argv[1])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        migrate(conn)


if __name__ == "__main__":
    main()
