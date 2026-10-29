"""Hostile scraped text must never become live HTML or markdown links in the UI."""
from streamlit.testing.v1 import AppTest

from ui.styles import md_escape, safe_http_url


def test_md_escape_neutralises_markdown():
    out = md_escape("[click](http://evil.example) **bold** <img src=x onerror=alert(1)>")
    assert "](" not in out.replace("\\]\\(", "") and "<img" not in out.replace("\\<img", "")


def test_safe_http_url():
    assert safe_http_url("https://scholarships.gov.in/x") and safe_http_url("javascript:alert(1)") is None
    assert safe_http_url("data:text/html,<script>") is None and safe_http_url("https://a.gov.in/a b") is None


def _render_hostile():
    from datetime import datetime, timezone
    import streamlit as st
    from models.evaluation import RankedResult
    from models.scholarship import Evidence, Scholarship
    from models.student import StudentProfile
    from services.ranking_service import build_results
    from ui.scholarship_card import render_card
    from ui.styles import inject_css
    now = datetime.now(timezone.utc)
    evil = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    s = Scholarship(id="evil", name=evil, provider=evil, description=evil, max_family_income=100, required_documents=[evil, "[phish](https://evil.example)"],
                    application_url="javascript:alert(1)", guideline_url="https://scholarships.gov.in/ok", retrieved_at=now,
                    evidence=[Evidence(field="max_family_income", quote="[phish](https://evil.example) " + evil, source_url="javascript:alert(2)")],
                    eligible_states=[evil], eligible_courses=[evil])
    for r in build_results(StudentProfile(annual_family_income=5), [s]):
        render_card(r)


def _live_markup(text: str) -> str:
    """What a markdown/HTML renderer would treat as syntax: drop backslash-escaped characters and HTML-entity text."""
    import re
    return re.sub(r"&(?:lt|gt|amp|quot|#x27|#91|#93);", "", re.sub(r"\\[\\`*_{}\[\]()#+!|<>~&$]", "", text))


def test_hostile_content_is_inert():
    at = AppTest.from_function(_render_hostile, default_timeout=30).run()
    assert not at.exception
    live = _live_markup(" ".join(m.value for m in at.markdown))
    assert "<script" not in live and "<img" not in live
    assert "](https://evil.example)" not in live
    assert all("javascript:" not in str(b.proto) for b in at.get("link_button"))
