"""Orchestrator tests with FAKE browser, searcher and Gemini (mocks). The real stack is covered by test_live.py."""
from datetime import date, datetime, timezone

import pytest

from config.settings import Settings
from database.repository import ScholarshipRepository
from models.student import StudentProfile
from services import search_orchestrator as so
from services.page_extractor import ExtractedEvidence as EE, ExtractedScholarship as ES, PageExtraction
from services.playwright_mcp_client import Link, MCPUnavailableError, PageContent, PageFetchError
from services.search_orchestrator import SearchOrchestrator, build_queries, cache_key

TODAY = date(2026, 10, 28)
GUIDE = """AICTE Swanath Scholarship Scheme
Academic Year 2026-27
Family income should not be more than Rs. 8 lakh per annum.
Last date for submission of applications is 31-12-2026."""


class FakeBrowser:
    pages: dict = {}
    started = 0

    def __init__(self, settings):
        self.robots = None

    async def __aenter__(self):
        if self.pages is None:
            raise MCPUnavailableError("no server")
        FakeBrowser.started += 1
        return self

    async def __aexit__(self, *a): pass

    async def fetch_page(self, url):
        if url not in self.pages:
            raise PageFetchError(url, "HTTP 404")
        title, text, links = self.pages[url]
        return PageContent(url, url, title, text, links)


class FakeSearcher:
    def __init__(self, urls): self.urls, self.queries = urls, []
    def search(self, q, n): self.queries.append(q); return self.urls


class NoGemini:
    available = False


class FakeGemini:
    available = True
    def __init__(self, fail=None): self.fail, self.calls = fail, 0
    def extract(self, doc):
        self.calls += 1
        if self.fail:
            raise self.fail
        if "Swanath" not in doc.text:
            return PageExtraction()
        return PageExtraction(scholarships=[ES(name="AICTE Swanath Scholarship Scheme", academic_year="2026-27", max_family_income_inr=800_000,
            evidence=[EE(field="academic_year", quote="Academic Year 2026-27"), EE(field="max_family_income", quote="Family income should not be more than Rs. 8 lakh per annum")])])


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(so, "is_safe", lambda u, resolve=True: True)
    monkeypatch.setattr("services.page_extractor.is_safe", lambda u, resolve=True: True)
    async def fake_pdf(url, robots, timeout, max_chars, respect_robots=True):
        if url not in FakeBrowser.pages:
            raise PageFetchError(url, "HTTP 404")
        return url, FakeBrowser.pages[url][1], datetime.now(timezone.utc)
    monkeypatch.setattr(so, "fetch_pdf", fake_pdf)
    FakeBrowser.pages = {
        "https://scholarships.gov.in/All-Scholarships": ("Schemes", "List of schemes", [Link("Guidelines", "https://scholarships.gov.in/g/swanath-guidelines.pdf", "Technical degree scholarship guidelines")]),
        "https://scholarships.gov.in/g/swanath-guidelines.pdf": ("Swanath guidelines", GUIDE, []),
    }
    s = Settings(db_path=tmp_path / "t.db", max_pages_per_search=6, respect_robots=False)
    return s, ScholarshipRepository(s.db_path)


def orch(env, gemini=None, urls=()):
    s, repo = env
    return SearchOrchestrator(s, repo, gemini or NoGemini(), FakeSearcher(list(urls)), FakeBrowser), repo


PROFILE = StudentProfile(course="B.Tech", education_level="Undergraduate", state="Karnataka", category="OBC", annual_family_income=100_000)


def test_end_to_end_with_rule_extractor(env):
    o, repo = orch(env)
    rep = o.search(PROFILE, today=TODAY)
    assert len(rep.scholarships) == 1
    sch = rep.scholarships[0]
    assert sch.max_family_income == 800_000 and sch.deadline == date(2026, 12, 31)
    assert sch.source_reliability.value.startswith("Official") and sch.evidence and not sch.is_demo
    assert any("rule-based" in n for n in rep.notes)
    assert repo.get(sch.id) is not None


def test_gemini_path_and_failure_fallback(env):
    g = FakeGemini()
    o, _ = orch(env, g)
    assert len(o.search(PROFILE, today=TODAY, force_refresh=True).scholarships) == 1 and g.calls >= 2
    from services.gemini_service import GeminiError
    g2 = FakeGemini(fail=GeminiError("quota"))
    o2, _ = orch(env, g2)
    rep = o2.search(PROFILE, today=TODAY, force_refresh=True)
    assert g2.calls == 1  # quota error stops further Gemini calls
    assert any("quota" in e.lower() for e in rep.errors)
    assert len(rep.scholarships) == 1  # rule-based fallback still produced a verified record


def test_unavailable_pages_recorded_and_search_continues(env):
    o, _ = orch(env, urls=["https://x.gov.in/missing"])
    rep = o.search(PROFILE, today=TODAY)
    assert any("404" in e for e in rep.errors) and len(rep.scholarships) == 1


def test_mcp_unavailable_is_reported_not_raised(env):
    FakeBrowser.pages = None
    o, _ = orch(env)
    rep = o.search(PROFILE, today=TODAY)
    assert rep.scholarships == [] and any("no server" in e for e in rep.errors)


def test_page_budget_respected(env):
    s, repo = env
    s2 = Settings(db_path=s.db_path, max_pages_per_search=1, respect_robots=False)
    o = SearchOrchestrator(s2, repo, NoGemini(), FakeSearcher([f"https://a.gov.in/{i}" for i in range(10)]), FakeBrowser)
    assert o.search(PROFILE, today=TODAY).pages_visited <= 1


def test_cache_hit_skips_browser_and_force_bypasses(env):
    o, _ = orch(env)
    o.search(PROFILE, today=TODAY)
    before = FakeBrowser.started
    rep = o.search(PROFILE, today=TODAY)
    assert rep.from_cache and FakeBrowser.started == before and rep.scholarships
    o.search(PROFILE, today=TODAY, force_refresh=True)
    assert FakeBrowser.started == before + 1


def test_cache_key_ignores_income_and_marks():
    a = PROFILE.model_copy(update={"annual_family_income": 1})
    b = PROFILE.model_copy(update={"annual_family_income": 900_000, "previous_percentage": 50.0})
    assert cache_key(a, TODAY) == cache_key(b, TODAY)
    assert cache_key(a, TODAY) != cache_key(PROFILE.model_copy(update={"state": "Kerala"}), TODAY)


def test_history_is_opt_in_and_coarse(env):
    o, repo = orch(env)
    o.search(PROFILE, today=TODAY)
    assert repo.list_history() == []
    o.search(PROFILE, today=TODAY, force_refresh=True, save_history=True)
    h = repo.list_history()[0]["summary"]
    assert "B.Tech" in h and "100000" not in h


def test_queries_use_profile_and_current_year():
    qs = build_queries(PROFILE, TODAY)
    assert qs and all("2026-27" in q or "scholarship" in q.lower() for q in qs)
    assert any("Karnataka" in q for q in qs) and len(qs) <= 4
    assert "OBC" in " ".join(qs) and "100000" not in " ".join(qs)
