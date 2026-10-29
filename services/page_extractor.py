"""Turns fetched documents into *candidate* scholarship facts.

* Extraction schemas shared by the Gemini extractor and the rule-based fallback.
* Deterministic parsers for rupee amounts and dates (also used to verify quotes later).
* Selection of guideline / eligibility / FAQ links worth following, ranked by relevance to the profile.
* A deliberately conservative rule-based extractor used when Gemini is unavailable.

Nothing here is trusted yet: scholarship_normalizer verifies every value against its quote.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from pydantic import BaseModel, Field

from models.student import EducationLevel, StudentProfile
from services.playwright_mcp_client import Link
from services.source_validator import is_official_domain, is_safe, normalize_url, same_site


# --------------------------------------------------------------------------- documents
@dataclass
class Document:
    url: str
    title: str
    text: str
    links: list[Link] = field(default_factory=list)
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    kind: str = "page"  # "page" | "pdf"


# --------------------------------------------------------------------------- extraction schemas
class ExtractedEvidence(BaseModel):
    field: str
    quote: str = Field(description="Verbatim substring of the document text supporting the field")


class ExtractedScholarship(BaseModel):
    name: str
    provider: str | None = None
    provider_type: str | None = Field(default=None, description="central government | state government | college/university | private foundation | corporate | other")
    description: str | None = None
    academic_year: str | None = Field(default=None, description="e.g. 2026-27")
    eligible_courses: list[str] = Field(default_factory=list)
    eligible_education_levels: list[str] = Field(default_factory=list, description="school | diploma | undergraduate | postgraduate | phd")
    eligible_years: list[int] = Field(default_factory=list)
    eligible_categories: list[str] = Field(default_factory=list, description="General | EWS | SC | ST | OBC | Other")
    min_percentage: float | None = None
    min_cgpa: float | None = None
    max_family_income_inr: int | None = None
    income_limit_inclusive: bool | None = None
    eligible_states: list[str] = Field(default_factory=list)
    eligible_institutions: list[str] = Field(default_factory=list)
    eligible_genders: list[str] = Field(default_factory=list)
    requires_disability: bool | None = None
    accepts_fresh: bool | None = None
    accepts_renewal: bool | None = None
    award_amount_inr: int | None = None
    award_amount_text: str | None = None
    award_count: int | None = None
    award_count_scope: str | None = Field(default=None, description="national | state | category | institution | previous year")
    opening_date: str | None = Field(default=None, description="YYYY-MM-DD")
    deadline: str | None = Field(default=None, description="YYYY-MM-DD")
    required_documents: list[str] = Field(default_factory=list)
    application_url: str | None = None
    guideline_url: str | None = None
    tags: list[str] = Field(default_factory=list, description="merit | need | research | course-specific | institution-specific")
    no_restriction_fields: list[str] = Field(default_factory=list, description="Eligibility fields the text explicitly says are unrestricted")
    evidence: list[ExtractedEvidence] = Field(default_factory=list)


class PageExtraction(BaseModel):
    scholarships: list[ExtractedScholarship] = Field(default_factory=list)


# --------------------------------------------------------------------------- parsers
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_NUM = r"\d[\d,]*(?:\.\d+)?"
_AMOUNT_RE = re.compile(
    rf"(?:(?:rs|inr|₹)\.?\s*({_NUM})\s*(?:/-)?\s*(lakhs?|lacs?|crores?)?|({_NUM})\s*(lakhs?|lacs?|crores?))", re.I)
_DATE_NUM = re.compile(r"\b(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(20\d{2})\b")
_DATE_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s,-]+(?:of\s+)?([A-Za-z]{3,9})\.?,?[\s,-]+(20\d{2})\b")
_DATE_MDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b")


def parse_inr_amounts(text: str) -> list[int]:
    out = []
    for m in _AMOUNT_RE.finditer(text):
        num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        try:
            value = float(num.replace(",", ""))
        except ValueError:
            continue
        unit = (unit or "").lower()
        if unit.startswith(("lakh", "lac")):
            value *= 100_000
        elif unit.startswith("crore"):
            value *= 10_000_000
        out.append(int(round(value)))
    return out


def parse_dates(text: str) -> list[date]:
    found: list[tuple[int, date]] = []
    for m in _DATE_NUM.finditer(text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            found.append((m.start(), date(y, mo, d)))
        except ValueError:
            pass
    for m in _DATE_DMY.finditer(text):
        mo = _MONTHS.get(m.group(2)[:3].lower())
        if mo:
            try:
                found.append((m.start(), date(int(m.group(3)), mo, int(m.group(1)))))
            except ValueError:
                pass
    for m in _DATE_MDY.finditer(text):
        mo = _MONTHS.get(m.group(1)[:3].lower())
        if mo:
            try:
                found.append((m.start(), date(int(m.group(3)), mo, int(m.group(2)))))
            except ValueError:
                pass
    return [d for _, d in sorted(found)]


def squash_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- link selection
_FOLLOW_KW = ("guideline", "eligib", "faq", "scheme", "specification", "instruction", "brochure", "notification",
              "how to apply", "apply", "criteria", "scholarship", "fellowship")
_SKIP_KW = ("login", "signin", "sign-in", "register", "signup", "youtube", "youtu.be", "facebook", "twitter", "instagram",
            "linkedin", "whatsapp", "play.google", "apps.apple", "mailto:", "tel:", "javascript:", "helpdesk",
            "usermanual", "grievance", "nodalofficer", "contact", "sitemap", "privacy", "disclaimer")
_LEVEL_TERMS = {
    EducationLevel.SCHOOL: ["school", "pre matric", "post matric", "matric", "class"],
    EducationLevel.DIPLOMA: ["diploma", "polytechnic", "technical"],
    EducationLevel.UNDERGRADUATE: ["undergraduate", " ug", "graduat", "college", "university", "degree", "post matric"],
    EducationLevel.POSTGRADUATE: ["postgraduate", " pg", "master", "post graduate"],
    EducationLevel.PHD: ["phd", "doctoral", "research", "fellowship"],
}
_CATEGORY_TERMS = {"SC": ["scheduled caste"], "ST": ["scheduled tribe", "tribal"], "OBC": ["obc", "backward"],
                   "EWS": ["ews", "economically weaker"], "General": [], "Other": ["minorit"]}
_COURSE_TERMS = {"be": ["engineering", "technical", "aicte"], "btech": ["engineering", "technical", "aicte"],
                 "bsc": ["science"], "bcom": ["commerce"], "mbbs": ["medical"], "mba": ["management"]}


def profile_terms(profile: StudentProfile) -> list[str]:
    terms: list[str] = []
    if profile.education_level:
        terms += _LEVEL_TERMS[profile.education_level]
    if profile.category:
        terms += _CATEGORY_TERMS.get(profile.category.value, [])
    if profile.course:
        key = re.sub(r"[^a-z]", "", profile.course.lower())
        terms += _COURSE_TERMS.get(key, []) + [profile.course.lower()]
    if profile.state:
        terms.append(profile.state.lower())
    if profile.gender and profile.gender.value == "Female":
        terms += ["girl", "women", "female"]
    if profile.has_disability:
        terms += ["disab", "divyang"]
    return terms


def select_links(doc: Document, terms: list[str], limit: int, exclude: set[str]) -> list[Link]:
    """Rank same-site / official links that look like scheme documents, favouring profile relevance."""
    scored: dict[str, tuple[float, Link]] = {}
    for link in doc.links:
        try:
            url = normalize_url(link.url)
        except Exception:
            continue
        low = f"{link.text} {url} {link.context}".lower()
        if url in exclude or any(k in low for k in _SKIP_KW) or not is_safe(url, resolve=False):
            continue
        if not (same_site(url, doc.url) or is_official_domain(url)):
            continue
        kw = sum(k in low for k in _FOLLOW_KW)
        if kw == 0:
            continue
        score = kw + 2 * sum(t in low for t in terms)
        if url.lower().endswith(".pdf"):
            score += 1.5 if any(k in low for k in ("guideline", "specification", "eligib")) else 0.5
        if url not in scored or score > scored[url][0]:
            scored[url] = (score, link)
    ranked = sorted(scored.values(), key=lambda t: -t[0])
    return [Link(text=l.text, url=normalize_url(l.url), context=l.context) for _, l in ranked[:limit]]


# --------------------------------------------------------------------------- rule-based fallback
_AY_RE = re.compile(r"\b(20\d{2})\s*[-–/]\s*(\d{2,4})\b")
_INCOME_RE = re.compile(
    r"[^.\n]*\b(?:annual\s+)?(?:family|parental|parents'?|household|gross)?\s*income\b[^.\n]{0,160}?"
    r"(?:not\s+(?:be\s+)?(?:more|exceed|greater|above)|should\s+not\s+exceed|up\s*to|upto|less\s+than|below|maximum|max\.?|≤|<)"
    r"[^.\n]{0,60}?(?:rs|inr|₹)\.?[^\n]{0,80}", re.I)
_MARKS_RE = re.compile(r"[^.\n]*\b(?:at\s+least|minimum|not\s+less\s+than|min\.?)\s+(\d{2,3}(?:\.\d+)?)\s*%[^.\n]*", re.I)
_DEADLINE_RE = re.compile(r"[^.\n]*\b(?:last\s+date|closing\s+date|last\s+day|deadline|apply\s+(?:till|before|by))\b[^\n]*", re.I)
_NAME_RE = re.compile(r"^.{0,80}\b(scholarship|fellowship|scheme|yojana|grant)\b.{0,100}$", re.I)


def _find_evidence_sentence(match_text: str) -> str:
    return squash_ws(match_text)[:400]


def rule_extract(doc: Document) -> PageExtraction:
    """Low-recall extraction of a few verifiable facts from a single-scheme guideline PDF.

    Web pages are skipped on purpose: portals list many schemes with different dates and limits, and
    attributing one page-level figure to a "scheme" would be wrong. Returns nothing unless the document
    has a scheme-like title AND at least one verifiable fact."""
    if doc.kind != "pdf":
        return PageExtraction()
    text = doc.text
    evidence: list[ExtractedEvidence] = []
    out = ExtractedScholarship(name="")

    # Name: a scheme-like line near the top of the document, else the page title.
    head = [squash_ws(l) for l in text.splitlines()[:40] if l.strip()]
    candidates = [l for l in head if _NAME_RE.match(l) and 8 < len(l) < 160 and "guideline" not in l.lower()[:12]]
    name = candidates[0] if candidates else (doc.title if _NAME_RE.match(doc.title or "") else "")
    if not name:
        return PageExtraction()
    out.name = name.title() if name.isupper() else name

    m = _AY_RE.search(text[:6000]) or _AY_RE.search(text)
    if m:
        ay = f"{m.group(1)}-{m.group(2)[-2:]}"
        out.academic_year = ay
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        line = squash_ws(text[line_start: line_end if line_end != -1 else len(text)])[:200]
        evidence.append(ExtractedEvidence(field="academic_year", quote=line))

    for m in _INCOME_RE.finditer(text):
        sentence = _find_evidence_sentence(m.group(0))
        amounts = sorted(set(parse_inr_amounts(sentence)))
        if len(amounts) == 1:
            out.max_family_income_inr = amounts[0]
            out.income_limit_inclusive = not re.search(r"less\s+than|below|<", sentence, re.I)
            evidence.append(ExtractedEvidence(field="max_family_income", quote=sentence))
            break

    m = _MARKS_RE.search(text)
    if m:
        out.min_percentage = float(m.group(1))
        evidence.append(ExtractedEvidence(field="min_percentage", quote=_find_evidence_sentence(m.group(0))))

    for m in _DEADLINE_RE.finditer(text):
        sentence = _find_evidence_sentence(m.group(0))
        dates = sorted(set(parse_dates(sentence)))
        if len(dates) == 1:
            out.deadline = dates[0].isoformat()
            evidence.append(ExtractedEvidence(field="deadline", quote=sentence))
            break

    verifiable = [e for e in evidence if e.field != "academic_year"]
    if not verifiable:
        return PageExtraction()
    out.evidence = evidence
    return PageExtraction(scholarships=[out])
