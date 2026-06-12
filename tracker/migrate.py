"""Database migration: apply schema.sql and advance user_version.

Usage:
    python -m tracker.migrate <db_path>

`PRAGMA user_version` is SQLite's built-in integer slot for schema
versioning. At version 0 (fresh database) we apply the full schema and
set it to 1. Re-running is a no-op — safe to call on every startup.
"""
import sqlite3
import sys
from pathlib import Path

from tracker.db import connect

_SCHEMA = Path(__file__).parent.parent / "db" / "schema.sql"
_TARGET_VERSION = 1


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= _TARGET_VERSION:
        print(f"Already at schema version {version}, nothing to do.")
        return

    sql = _SCHEMA.read_text(encoding="utf-8")
    conn.executescript(sql)
    # executescript commits automatically; set user_version in a separate call.
    conn.execute(f"PRAGMA user_version = {_TARGET_VERSION}")
    conn.commit()
    print(f"Migrated to schema version {_TARGET_VERSION}.")


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
