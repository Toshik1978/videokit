"""SQLite connection and schema. Synchronous; callers off-load it with `asyncio.to_thread`."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    tool_id TEXT NOT NULL,
    state TEXT NOT NULL,
    failure_reason TEXT,
    failure_code TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    upload_length INTEGER NOT NULL,
    upload_offset INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    queued_at REAL,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    duration_s REAL,
    language TEXT,
    no_speech INTEGER NOT NULL DEFAULT 0,
    providers TEXT NOT NULL DEFAULT '{}',
    usage TEXT NOT NULL,
    source_name TEXT
);
CREATE INDEX IF NOT EXISTS jobs_state_queued_at ON jobs (state, queued_at);
CREATE INDEX IF NOT EXISTS jobs_expires_at ON jobs (expires_at);
"""
# Columns added after the first release: (name, SQL type). Added in place to databases created before them.
_ADDED_COLUMNS = (("failure_code", "TEXT"), ("source_name", "TEXT"))


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for name, sql_type in _ADDED_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {sql_type}")


def connect(path: Path) -> sqlite3.Connection:
    """Open a new connection (WAL, autocommit, Row factory) and ensure the current schema exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn
