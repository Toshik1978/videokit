"""SQLite connection and schema. Synchronous; callers off-load it with `asyncio.to_thread`."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    tool_id TEXT NOT NULL,
    state TEXT NOT NULL,
    failure_reason TEXT,
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
    usage TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_state_queued_at ON jobs (state, queued_at);
CREATE INDEX IF NOT EXISTS jobs_expires_at ON jobs (expires_at);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open a new connection (WAL, autocommit, Row factory) and ensure the schema exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn
