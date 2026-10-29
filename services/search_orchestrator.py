"""Live search: discover -> fetch (Playwright MCP / PDF) -> extract -> verify -> merge -> persist.

Failures are recorded and never abort the run; partial results are kept. Limits (pages, links,
retries, one browser at a time) come from Settings.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Protocol

from config.settings import Settings
from database.repository import ScholarshipRepository
from models.scholarship import Scholarship
from models.student import StudentProfile
from services.eligibility_engine import current_academic_year_start
from services.gemini_service import GeminiError, GeminiService
from services.page_extractor import Document, PageExtraction, profile_terms, rule_extract, select_links
from services.pdf_fetcher import fetch_pdf, is_pdf_url
from services.playwright_mcp_client import MCPUnavailableError, PageFetchError, PlaywrightMCPClient
from services.scholarship_normalizer import dedupe, normalize
from services.source_validator import RobotsChecker, classify_source, is_official_domain, is_safe

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]

# National Scholarship Portal scheme list (links to every scheme's guideline PDF).
# services.india.gov.in was requested as a source but does not resolve (NXDOMAIN) as of 2026-10-28, so it is not
# a default; add working seeds with EXTRA_SEED_URLS.
SEED_URLS = ("https://scholarships.gov.in/All-Scholarships",)


class WebSearcher(Protocol):
    def search(self, query: str, max_results: int) -> list[str]: ...


class DuckDuckGoSearcher:
    """Discovery only: it finds candidate URLs. Nothing from search snippets is used as scholarship data."""

    def search(self, query: str, max_results: int = 8) -> list[str]:
        from ddgs import DDGS

        try:
            return [r["href"] for r in DDGS().text(query, region="in-en", max_results=max_results) if r.get("href")]
        except Exception as e:  # rate limits / network: discovery degrades, seeds still run
            log.warning("web search failed: %s", type(e).__name__)
            return []


@dataclass
class SearchReport:
    scholarships: list[Scholarship] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pages_visited: int = 0
    from_cache: bool = False

    def as_state(self) -> dict:
        return {"live": True, "scholarships": self.scholarships, "errors": self.errors, "notes": self.notes}


def current_academic_year_label(today: date) -> str:
    y = current_academic_year_start(today)
    return f"{y}-{str(y + 1)[-2:]}"


def build_queries(profile: StudentProfile, today: date | None = None, limit: int = 4) -> list[str]:
    today = today or date.today()
    ay = current_academic_year_label(today)
    level = profile.education_level.value.lower() if profile.education_level else ""
    parts = lambda *xs: " ".join(x for x in xs if x)
    cat = profile.category.value if profile.category and profile.category.value != "General" else ""
    queries = [
        parts(profile.course, level, "scholarship eligibility guidelines", ay, "site:gov.in"),
        parts("National Scholarship Portal", cat, level, "scheme guidelines", ay),
        parts(profile.state, "state government scholarship", cat, level, ay, "official"),
    ]
    if profile.institution_name:
        queries.append(parts(profile.institution_name, "scholarships official"))
    if profile.preferences.kinds or profile.preferences.bases:
        queries.append(parts(profile.course or level, "scholarship", *(k.value for k in profile.preferences.kinds), ay))
    seen, out = set(), []
    for q in queries:
        q = " ".join(q.split())
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:limit]


def cache_key(profile: StudentProfile, today: date) -> str:
    """Keyed on what drives discovery only. Income and marks are deliberately excluded: they are
    applied locally afterwards, so one cached crawl serves many profiles and no profile is stored."""
    basis = [profile.course, profile.education_level, profile.state, profile.category,
             profile.institution_name, current_academic_year_label(today),
             [k.value for k in profile.preferences.kinds], [b.value for b in profile.preferences.bases]]
    return hashlib.sha256(json.dumps(basis, default=str).encode()).hexdigest()


class SearchOrchestrator:
    def __init__(self, settings: Settings, repo: ScholarshipRepository, gemini: GeminiService | None = None,
                 searcher: WebSearcher | None = None, client_factory=PlaywrightMCPClient):
        self.settings, self.repo = settings, repo
        self.gemini = gemini or GeminiService(settings, repo)
        self.searcher = searcher or DuckDuckGoSearcher()
        self.client_factory = client_factory

    # ------------------------------------------------------------------ public
    def search(self, profile: StudentProfile, progress: Progress | None = None, force_refresh: bool = False,
               save_history: bool = False, today: date | None = None) -> SearchReport:
        today = today or date.today()
        report = SearchReport()
        progress = progress or (lambda f, m: None)
        key = cache_key(profile, today)

        if not force_refresh:
            hit = self.repo.get_search(key, self.settings.cache_ttl_hours)
            if hit:
                ids, _, created = hit
                cached = self.repo.get_many(ids)
                if cached:
                    report.scholarships, report.from_cache = cached, True
                    report.notes.append(
                        f"Loaded {len(cached)} saved result(s) from a search on {created:%d %b %Y %H:%M} UTC. "
                        "Use “Force fresh verification” to re-check the official sources.")
                    progress(1.0, "Loaded saved results")
                    return report
        try:
            asyncio.run(self._run(profile, report, progress, today))
        except MCPUnavailableError as e:
            report.errors.append(str(e))
        except Exception as e:  # last-resort guard: the UI must never crash on a search
            log.exception("search failed")
            report.errors.append(f"Search stopped unexpectedly ({type(e).__name__}). Partial results are shown if any.")

        report.scholarships = dedupe(report.scholarships)
        report.scholarships = [s for s in report.scholarships if s.evidence]
        if report.scholarships:
            self.repo.upsert_many(report.scholarships)
            self.repo.save_search(key, [s.id for s in report.scholarships], "{}")
        if save_history:
            self.repo.add_history(" · ".join(x for x in (
                profile.education_level.value if profile.education_level else None, profile.course, profile.state,
                profile.category.value if profile.category else None) if x) or "(no details)", len(report.scholarships))
        if not self.gemini.available:
            report.notes.append("GEMINI_API_KEY is not set, so a limited rule-based extractor was used. "
                                "Fewer fields are verified; add a key for fuller extraction.")
        progress(1.0, "Done")
        return report

    # ------------------------------------------------------------------ pipeline
    async def _run(self, profile: StudentProfile, report: SearchReport, progress: Progress, today: date) -> None:
        s = self.settings
        terms = profile_terms(profile)
        progress(0.02, "Looking for official sources…")
        urls = await self._discover(profile, today)
        if not urls:
            report.errors.append("No candidate sources were found.")
            return
        budget = s.max_pages_per_search
        first_wave = urls[: max(2, budget // 2)]
        visited: set[str] = set()
        use_gemini = {"on": self.gemini.available, "fails": 0}
        gate = asyncio.Semaphore(3)  # at most 3 Gemini calls in flight
        pending: list[asyncio.Task] = []

        async def extract_bg(doc: Document) -> None:
            async with gate:
                await asyncio.to_thread(self._extract_into, doc, report, use_gemini)

        progress(0.05, "Starting the browser (Playwright MCP)…")
        async with self.client_factory(s) as browser:
            docs_done = 0

            async def process(url: str, label: str) -> Document | None:
                nonlocal docs_done
                if url in visited or docs_done >= budget:
                    return None
                visited.add(url)
                if s.respect_robots and not await asyncio.to_thread(browser.robots.allowed, url):
                    # Blocked sources must not use up the page budget.
                    report.errors.append(f"Could not read {url[:90]}: disallowed by robots.txt (or robots.txt unavailable)")
                    return None
                progress(0.1 + 0.85 * docs_done / budget, f"{label}: {url[:80]}")
                doc = await self._fetch(browser, url, report)
                docs_done += 1
                report.pages_visited = docs_done
                if doc:
                    visited.add(doc.url)
                    # Extract in the background while the browser loads the next page.
                    pending.append(asyncio.create_task(extract_bg(doc)))
                return doc

            fetched: list[Document] = []
            for url in first_wave:
                d = await process(url, "Reading")
                if d:
                    fetched.append(d)
            # Follow guideline / eligibility / FAQ links, most profile-relevant first, across all pages.
            cands = []
            for d in fetched:
                cands += [(l, d) for l in select_links(d, terms, s.max_follow_links_per_page * 3, visited)]
            seen_c: set[str] = set()
            for link, parent in cands:
                if link.url in seen_c:
                    continue
                seen_c.add(link.url)
                if docs_done >= budget:
                    break
                await process(link.url, "Following link")
            for url in urls[len(first_wave):]:
                if docs_done >= budget:
                    break
                await process(url, "Reading")
            progress(0.95, "Finishing extraction…")
            await asyncio.gather(*pending)

    async def _discover(self, profile: StudentProfile, today: date) -> list[str]:
        found: list[str] = [*SEED_URLS, *self.settings.extra_seed_urls]
        queries = build_queries(profile, today, self.settings.max_search_queries)
        batches = await asyncio.gather(*(asyncio.to_thread(self.searcher.search, q, 6) for q in queries),
                                       return_exceptions=True)
        for results in batches:
            if not isinstance(results, BaseException):
                found += results
        out, seen = [], set()
        for u in found:
            if u in seen or not is_safe(u, resolve=False):
                continue
            seen.add(u)
            out.append(u)
        # Official / government sources first; unknown domains last (they will be labelled unverified).
        rank = lambda u: 0 if u in SEED_URLS or u in self.settings.extra_seed_urls else 1 if is_official_domain(u) else 2 if classify_source(u, self.settings.trusted_provider_domains).is_authoritative else 3
        return sorted(out, key=rank)

    async def _fetch(self, browser: PlaywrightMCPClient, url: str, report: SearchReport) -> Document | None:
        try:
            if is_pdf_url(url):
                final, text, at = await fetch_pdf(url, browser.robots, self.settings.page_timeout_seconds,
                                                  self.settings.max_page_chars, self.settings.respect_robots)
                return Document(url=final, title=final.rsplit("/", 1)[-1], text=text, retrieved_at=at, kind="pdf")
            p = await browser.fetch_page(url)
            return Document(url=p.final_url, title=p.title, text=p.text, links=p.links, retrieved_at=p.retrieved_at)
        except PageFetchError as e:
            report.errors.append(f"Could not read {url[:90]}: {e.reason}")
        except Exception as e:
            report.errors.append(f"Could not read {url[:90]}: {type(e).__name__}")
        return None

    def _extract_into(self, doc: Document, report: SearchReport, use_gemini: dict) -> None:
        extraction: PageExtraction | None = None
        if use_gemini["on"]:
            try:
                extraction = self.gemini.extract(doc)
                use_gemini["fails"] = 0
            except GeminiError as e:
                use_gemini["fails"] = use_gemini.get("fails", 0) + 1
                if e.kind in ("auth", "quota", "no_key", "model") or use_gemini["fails"] >= 2:
                    use_gemini["on"] = False  # no point retrying every page; rule-based extractor takes over
                report.errors.append(f"Gemini extraction: {e.user_message}")
        if extraction is None:
            extraction = rule_extract(doc)
        for ex in extraction.scholarships:
            sch = normalize(ex, doc, self.settings.trusted_provider_domains)
            if sch:
                report.scholarships.append(sch)


def run_live_search(profile: StudentProfile, repo: ScholarshipRepository, settings: Settings,
                    force_refresh: bool = False, save_history: bool = False, progress: Progress | None = None) -> dict:
    """Entry point used by the Streamlit app. Returns the dict the UI stores in session state."""
    report = SearchOrchestrator(settings, repo).search(profile, progress, force_refresh, save_history)
    return report.as_state()
