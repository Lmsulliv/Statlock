"""Database connection helper."""
import sqlite3
from pathlib import Path


def connect(db_path: str | Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a SQLite connection with sane defaults.

    Foreign-key enforcement is OFF by default in SQLite and must be enabled
    per-connection; forgetting this is a classic source of silent data bugs.

    WAL journal mode and a 5s busy timeout make the file safe for concurrent
    access: in a deployed setup the drain daemon writes while the API both reads
    and writes (label renames, era confirm/dismiss). WAL lets readers and a
    single writer proceed concurrently, and the busy timeout makes a second
    writer wait-and-retry instead of immediately raising "database is locked".

    `check_same_thread=False` is needed by the FastAPI read layer: a request's
    connection is created in one threadpool worker and may be used by the path
    function in another. It is safe here because each connection is confined to
    a single request and never shared concurrently.
    """
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    # busy_timeout first: a competing writer waits up to 5s for the lock instead
    # of immediately raising "database is locked". Setting it before the WAL
    # switch means that switch also waits if another connection holds a lock.
    conn.execute("PRAGMA busy_timeout = 5000")
    # WAL (write-ahead logging) lets readers and a single writer run
    # concurrently. journal_mode is persisted in the DB file header, so
    # re-setting it per connection is cheap and idempotent.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
