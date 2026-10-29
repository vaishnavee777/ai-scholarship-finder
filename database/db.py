"""SQLite connection handling. One short-lived connection per operation; WAL for concurrent readers."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS scholarships (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    data TEXT NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 0,
    retrieved_at TEXT,
    last_verified_at TEXT,
    application_status TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_urls (
    scholarship_id TEXT NOT NULL REFERENCES scholarships(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    PRIMARY KEY (scholarship_id, url)
);
CREATE TABLE IF NOT EXISTS field_evidence (
    scholarship_id TEXT NOT NULL REFERENCES scholarships(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    quote TEXT NOT NULL,
    source_url TEXT NOT NULL,
    retrieved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_evidence_sch ON field_evidence(scholarship_id);
CREATE TABLE IF NOT EXISTS search_cache (
    cache_key TEXT PRIMARY KEY,
    scholarship_ids TEXT NOT NULL,
    report TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gemini_cache (
    cache_key TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT NOT NULL,
    result_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


def init_db(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
