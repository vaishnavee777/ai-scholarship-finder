"""Loads the clearly-fictional demo records. These must never be mixed into live results."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from models.scholarship import Evidence, Scholarship

DEMO_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_scholarships.json"


def load_demo_scholarships(path: Path = DEMO_PATH, now: datetime | None = None) -> list[Scholarship]:
    now = now or datetime.now(timezone.utc)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))["scholarships"]
    out = []
    for r in raw:
        r = dict(r)
        offset = r.pop("deadline_in_days", None)
        if offset is not None:
            r["deadline"] = (now + timedelta(days=offset)).date().isoformat()
        r["retrieved_at"] = r["last_verified_at"] = now.isoformat()
        r["is_demo"] = True
        r["evidence"] = [Evidence(retrieved_at=now, **e).model_dump(mode="json") for e in r.get("evidence", [])]
        out.append(Scholarship.model_validate(r))
    return out
