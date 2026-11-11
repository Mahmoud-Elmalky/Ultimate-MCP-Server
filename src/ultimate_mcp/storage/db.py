"""SQLite connection, schema migration, and capability helpers (ADR-0003).

Uses standard library ``sqlite3`` with WAL mode and busy timeout for safe concurrent access
between the MCP server process and the local human approval CLI.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY,
            action_type TEXT NOT NULL,
            permission_level TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_digest TEXT NOT NULL,
            summary TEXT NOT NULL,
            target_path TEXT,
            target_Pre_hash TEXT,
            requester_id TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            decided_at REAL,
            decided_by TEXT,
            decision_reason TEXT,
            consumed_at REAL,
            result_ref TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_approvals_status
            ON approvals (status, expires_at);

        CREATE TABLE IF NOT EXISTS audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT,
            duration_ms INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 1,
            approval_id TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        """,
    ),
)


def check_fts5_available() -> bool:
    """Return True if the active Python SQLite build supports FTS5."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(content);")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def init_database(db_path: Path) -> None:
    """Initialize SQLite database file and apply pending migrations atomically."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            """
        )
        cur = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations;")
        current_version = int(cur.fetchone()[0])
        for version, sql in MIGRATIONS:
            if version > current_version:
                conn.executescript(sql)
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?);",
                    (version,),
                )
        conn.commit()


@contextmanager
def open_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a configured SQLite connection with ``Row`` factory."""
    init_database(db_path)
    conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
    finally:
        conn.close()
