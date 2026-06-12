"""Database connection helper."""
import sqlite3
from pathlib import Path


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with sane defaults.

    Foreign-key enforcement is OFF by default in SQLite and must be enabled
    per-connection; forgetting this is a classic source of silent data bugs.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
