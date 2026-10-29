"""Persistence for scholarships, evidence, cached searches, Gemini responses and optional history.

Student profiles are never stored. Search history keeps only a coarse text summary and only
when the caller explicitly opts in.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database.db import connect, init_db
from models.scholarship import Scholarship

_write_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class ScholarshipRepository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        init_db(self.db_path)

    # ---- scholarships
    def upsert(self, sch: Scholarship) -> None:
        self.upsert_many([sch])

    def upsert_many(self, items: list[Scholarship]) -> None:
        with _write_lock, connect(self.db_path) as c:
            for s in items:
                c.execute(
                    """INSERT INTO scholarships (id, name, data, is_demo, retrieved_at, last_verified_at, application_status, updated_at)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET name=excluded.name, data=excluded.data, is_demo=excluded.is_demo,
                         retrieved_at=excluded.retrieved_at, last_verified_at=excluded.last_verified_at,
                         application_status=excluded.application_status, updated_at=excluded.updated_at""",
                    (s.id, s.name, s.model_dump_json(), int(s.is_demo), _iso(s.retrieved_at),
                     _iso(s.last_verified_at), s.application_status.value, _iso(_now())),
                )
                c.execute("DELETE FROM source_urls WHERE scholarship_id=?", (s.id,))
                c.execute("DELETE FROM field_evidence WHERE scholarship_id=?", (s.id,))
                c.executemany("INSERT OR IGNORE INTO source_urls VALUES (?,?)", [(s.id, u) for u in s.source_urls])
                c.executemany(
                    "INSERT INTO field_evidence VALUES (?,?,?,?,?)",
                    [(s.id, e.field, e.quote, e.source_url, _iso(e.retrieved_at)) for e in s.evidence],
                )

    def get(self, scholarship_id: str) -> Scholarship | None:
        with connect(self.db_path) as c:
            row = c.execute("SELECT data FROM scholarships WHERE id=?", (scholarship_id,)).fetchone()
        return Scholarship.model_validate_json(row["data"]) if row else None

    def get_many(self, ids: list[str]) -> list[Scholarship]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        with connect(self.db_path) as c:
            rows = c.execute(f"SELECT data FROM scholarships WHERE id IN ({marks})", ids).fetchall()
        by_id = {s.id: s for s in (Scholarship.model_validate_json(r["data"]) for r in rows)}
        return [by_id[i] for i in ids if i in by_id]

    def list_all(self, include_demo: bool = False) -> list[Scholarship]:
        sql = "SELECT data FROM scholarships" + ("" if include_demo else " WHERE is_demo=0")
        with connect(self.db_path) as c:
            rows = c.execute(sql).fetchall()
        return [Scholarship.model_validate_json(r["data"]) for r in rows]

    def count_evidence(self, scholarship_id: str) -> int:
        with connect(self.db_path) as c:
            return c.execute("SELECT COUNT(*) FROM field_evidence WHERE scholarship_id=?", (scholarship_id,)).fetchone()[0]

    # ---- search cache
    def save_search(self, key: str, ids: list[str], report_json: str) -> None:
        import json
        with _write_lock, connect(self.db_path) as c:
            c.execute(
                "INSERT OR REPLACE INTO search_cache VALUES (?,?,?,?)",
                (key, json.dumps(ids), report_json, _iso(_now())),
            )

    def get_search(self, key: str, max_age_hours: int) -> tuple[list[str], str, datetime] | None:
        import json
        with connect(self.db_path) as c:
            row = c.execute("SELECT * FROM search_cache WHERE cache_key=?", (key,)).fetchone()
        if not row:
            return None
        created = datetime.fromisoformat(row["created_at"])
        if _now() - created > timedelta(hours=max_age_hours):
            return None
        return json.loads(row["scholarship_ids"]), row["report"], created

    # ---- Gemini cache
    def save_gemini(self, key: str, response_json: str) -> None:
        with _write_lock, connect(self.db_path) as c:
            c.execute("INSERT OR REPLACE INTO gemini_cache VALUES (?,?,?)", (key, response_json, _iso(_now())))

    def get_gemini(self, key: str, max_age_hours: int) -> str | None:
        with connect(self.db_path) as c:
            row = c.execute("SELECT response, created_at FROM gemini_cache WHERE cache_key=?", (key,)).fetchone()
        if not row or _now() - datetime.fromisoformat(row["created_at"]) > timedelta(hours=max_age_hours):
            return None
        return row["response"]

    # ---- optional history
    def add_history(self, summary: str, result_count: int) -> None:
        with _write_lock, connect(self.db_path) as c:
            c.execute("INSERT INTO search_history (summary, result_count, created_at) VALUES (?,?,?)",
                      (summary, result_count, _iso(_now())))

    def list_history(self, limit: int = 20) -> list[dict]:
        with connect(self.db_path) as c:
            rows = c.execute("SELECT summary, result_count, created_at FROM search_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---- clearing
    def clear_search_data(self) -> None:
        """Remove cached searches, Gemini responses and history. Scholarship records stay."""
        with _write_lock, connect(self.db_path) as c:
            for t in ("search_cache", "gemini_cache", "search_history"):
                c.execute(f"DELETE FROM {t}")

    def clear_scholarships(self, include_demo: bool = False) -> None:
        with _write_lock, connect(self.db_path) as c:
            c.execute("DELETE FROM scholarships" + ("" if include_demo else " WHERE is_demo=0"))
