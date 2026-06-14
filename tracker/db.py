"""Database connection helper."""
import sqlite3
from pathlib import Path


def connect(db_path: str | Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a SQLite connection with sane defaults.

    Foreign-key enforcement is OFF by default in SQLite and must be enabled
    per-connection; forgetting this is a classic source of silent data bugs.

    `check_same_thread=False` is needed by the FastAPI read layer: a request's
    connection is created in one threadpool worker and may be used by the path
    function in another. It is safe here because each connection is confined to
    a single request and never shared concurrently.
    """
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
