"""Validates extracted facts against the source text and builds Scholarship records.

Trust rule: an extracted value survives only if (1) its quote is a verbatim substring of the
document and (2) the quote actually supports the value (e.g. the quote contains that amount/date).
Everything else is dropped and listed as unresolved. Gemini output is never trusted on its own.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin

from models.scholarship import (
    AwardCountScope, Conflict, Evidence, ProviderType, Scholarship, SourceReliability, make_scholarship_id,
)
from models.student import Category, EducationLevel, Gender
from services.eligibility_engine import CORE_FIELDS, academic_year_start
from services.page_extractor import Document, ExtractedScholarship, parse_dates, parse_inr_amounts, squash_ws
from services.source_validator import classify_source, is_safe, normalize_url, same_site

MIN_QUOTE_CHARS = 8

# evidence field names Gemini/rules may use -> canonical Scholarship field
_FIELD_ALIASES = {
    "max_family_income_inr": "max_family_income", "income": "max_family_income", "family_income": "max_family_income",
    "award_amount_inr": "award_amount", "amount": "award_amount", "categories": "eligible_categories",
    "courses": "eligible_courses", "education_levels": "eligible_education_levels", "states": "eligible_states",
    "institutions": "eligible_institutions", "genders": "eligible_genders", "documents": "required_documents",
}

# Abbreviations are matched case-sensitively; spelled-out phrases case-insensitively.
_CATEGORY_PATTERNS = {
    Category.SC: r"\bSCs?\b|(?i:scheduled\s+castes?)",
    Category.ST: r"\bSTs?\b|(?i:scheduled\s+tribes?)",
    Category.OBC: r"\bOBCs?\b|(?i:other\s+backward|backward\s+class)",
    Category.EWS: r"\bEWS\b|(?i:economically\s+weaker)",
    Category.GENERAL: r"(?i:\bgeneral\b|all\s+categories|\bunreserved\b)",
    Category.OTHER: r"(?i:minorit|denotified)|\bPVTG\b|\bDNT\b",
}
_LEVEL_PATTERNS = {
    EducationLevel.SCHOOL: r"(?i:school|matric|class\s+\w+)",
    EducationLevel.DIPLOMA: r"(?i:diploma|polytechnic)",
    EducationLevel.UNDERGRADUATE: r"(?i:under\s*-?graduate|bachelor|degree|graduation)|\bUG\b|\bB\.?(?:E|Tech|Sc|Com|A|BA)\b",
    EducationLevel.POSTGRADUATE: r"(?i:post\s*-?graduate|master)|\bPG\b|\bM\.?(?:E|Tech|Sc|Com|A|BA)\b",
    EducationLevel.PHD: r"(?i:ph\.?\s*d|doctora|d\.?phil)",
}
_GENDER_PATTERNS = {Gender.FEMALE: r"(?i:girl|women|woman|female|daughter)", Gender.MALE: r"(?i:\bboys?\b|\bmale\b|\bmen\b)"}
_PROVIDER_MAP = {
    "central": ProviderType.CENTRAL_GOVERNMENT, "state": ProviderType.STATE_GOVERNMENT,
    "college": ProviderType.INSTITUTION, "university": ProviderType.INSTITUTION,
    "foundation": ProviderType.PRIVATE_FOUNDATION, "trust": ProviderType.PRIVATE_FOUNDATION,
    "corporate": ProviderType.CORPORATE, "company": ProviderType.CORPORATE,
}
_SCOPE_MAP = {"national": AwardCountScope.NATIONAL, "state": AwardCountScope.STATE, "category": AwardCountScope.CATEGORY,
              "institution": AwardCountScope.INSTITUTION, "previous": AwardCountScope.PREVIOUS_YEAR}

_EVIDENCE_REQUIRED = (
    "academic_year", "max_family_income", "min_percentage", "min_cgpa", "award_amount", "award_count", "deadline",
    "opening_date", "eligible_courses", "eligible_education_levels", "eligible_years", "eligible_categories",
    "eligible_states", "eligible_institutions", "eligible_genders", "requires_disability", "accepts_fresh",
    "accepts_renewal", "required_documents",
)


def _canon(field: str) -> str:
    f = field.strip().lower().replace(" ", "_")
    return _FIELD_ALIASES.get(f, f)


def _quote_in_doc(quote: str, doc_norm: str) -> bool:
    q = squash_ws(quote).lower()
    return len(q) >= MIN_QUOTE_CHARS and q in doc_norm


def _num_in(quote: str, value: float) -> bool:
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", quote)]
    return any(abs(n - value) < 1e-9 for n in nums)


def _coerce_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip()[:10])
    except ValueError:
        return None


def _provider_type(raw: str | None) -> ProviderType:
    low = (raw or "").lower()
    for key, val in _PROVIDER_MAP.items():
        if key in low:
            return val
    return ProviderType.UNKNOWN


def _match_enum(values: list[str], patterns: dict, quotes: str, enum_cls) -> list:
    """Keep only values that are recognised members AND corroborated by wording in the quotes."""
    out = []
    for raw in values:
        member = next((m for m in enum_cls if raw.strip().lower() in (m.value.lower(), m.name.lower())), None)
        if member and member not in out and member in patterns and re.search(patterns[member], quotes):
            out.append(member)
    return out


_ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth"}


def _year_in(quote: str, y: int) -> bool:
    return bool(re.search(rf"\b{y}(?:st|nd|rd|th)?\b", quote) or _ORDINALS[y] in quote.lower())


def _url_ok(url: str | None, doc: Document) -> str | None:
    """Accept only URLs that appear on the page or sit on the same site, and pass the SSRF check."""
    if not url:
        return None
    try:
        absolute = normalize_url(urljoin(doc.url, url.strip()))
    except Exception:
        return None
    on_page = any(normalize_url(l.url) == absolute for l in doc.links)
    if not is_safe(absolute, resolve=False) or not (on_page or same_site(absolute, doc.url)):
        return None
    return absolute


def normalize(ex: ExtractedScholarship, doc: Document, trusted_domains: tuple[str, ...] = (),
              today: date | None = None) -> Scholarship | None:
    """Build a Scholarship from one extraction. Returns None for unusable records (no name)."""
    name = squash_ws(ex.name or "")
    if len(name) < 4:
        return None
    doc_norm = squash_ws(doc.text).lower() + " " + squash_ws(doc.title).lower()

    quotes_by_field: dict[str, list[str]] = {}
    verified_evidence: list[Evidence] = []
    for e in ex.evidence:
        f = _canon(e.field)
        if _quote_in_doc(e.quote, doc_norm):
            quotes_by_field.setdefault(f, []).append(squash_ws(e.quote))
            verified_evidence.append(Evidence(field=f, quote=squash_ws(e.quote)[:600], source_url=doc.url, retrieved_at=doc.retrieved_at))
    dropped: list[str] = []

    def quotes(f: str) -> str:
        return " ".join(quotes_by_field.get(f, []))

    def keep(f: str, ok: bool) -> bool:
        if not ok:
            dropped.append(f)
        return ok

    fields: dict = {}
    if ex.academic_year and keep("academic_year", academic_year_start(ex.academic_year) is not None
                                 and str(academic_year_start(ex.academic_year)) in quotes("academic_year")):
        y = academic_year_start(ex.academic_year)
        fields["academic_year"] = f"{y}-{str(y + 1)[-2:]}"

    if ex.max_family_income_inr is not None and keep(
            "max_family_income", ex.max_family_income_inr in parse_inr_amounts(quotes("max_family_income"))):
        fields["max_family_income"] = ex.max_family_income_inr
        q = quotes("max_family_income")
        strict = bool(re.search(r"less\s+than|below|under\b|<", q, re.I)) and not re.search(r"not\s+(?:be\s+)?(?:more|exceed|above|greater)|up\s*to|upto", q, re.I)
        fields["income_limit_inclusive"] = not strict if ex.income_limit_inclusive is None else (ex.income_limit_inclusive and not strict)
    if ex.award_amount_inr is not None and keep("award_amount", ex.award_amount_inr in parse_inr_amounts(quotes("award_amount"))):
        fields["award_amount"] = ex.award_amount_inr
        fields["award_amount_text"] = (ex.award_amount_text or squash_ws(quotes("award_amount")))[:200]
    for src, f in ((ex.min_percentage, "min_percentage"), (ex.min_cgpa, "min_cgpa")):
        if src is not None and keep(f, 0 < src <= 100 and _num_in(quotes(f), src)):
            fields[f] = src
    if ex.award_count is not None and keep("award_count", ex.award_count > 0 and _num_in(quotes("award_count"), ex.award_count)):
        fields["award_count"] = ex.award_count
        scope = (ex.award_count_scope or "").lower()
        fields["award_count_scope"] = next((v for k, v in _SCOPE_MAP.items() if k in scope), AwardCountScope.UNKNOWN)

    for f in ("deadline", "opening_date"):
        d = _coerce_date(getattr(ex, f))
        if getattr(ex, f) and keep(f, d is not None and d in parse_dates(quotes(f))):
            fields[f] = d

    if ex.eligible_categories:
        cats = _match_enum(ex.eligible_categories, _CATEGORY_PATTERNS, quotes("eligible_categories"), Category)
        if keep("eligible_categories", bool(cats)):
            fields["eligible_categories"] = cats
    if ex.eligible_education_levels:
        lv = _match_enum(ex.eligible_education_levels, _LEVEL_PATTERNS, quotes("eligible_education_levels"), EducationLevel)
        if keep("eligible_education_levels", bool(lv)):
            fields["eligible_education_levels"] = lv
    if ex.eligible_genders:
        gs = _match_enum(ex.eligible_genders, _GENDER_PATTERNS, quotes("eligible_genders"), Gender)
        if keep("eligible_genders", bool(gs)):
            fields["eligible_genders"] = gs
    for f, items in (("eligible_courses", ex.eligible_courses), ("eligible_states", ex.eligible_states),
                     ("eligible_institutions", ex.eligible_institutions), ("required_documents", ex.required_documents)):
        if items:
            q = quotes(f).lower()
            good = [squash_ws(i) for i in items if squash_ws(i) and (squash_ws(i).lower() in q or f == "required_documents"
                    and any(w in q for w in re.findall(r"[a-z]{5,}", squash_ws(i).lower())))]
            if keep(f, bool(good)):
                fields[f] = good
    if ex.eligible_years and keep("eligible_years", all(1 <= y <= 8 and _year_in(quotes("eligible_years"), y) for y in ex.eligible_years)):
        fields["eligible_years"] = sorted(set(ex.eligible_years))
    for f in ("requires_disability", "accepts_fresh", "accepts_renewal"):
        v = getattr(ex, f)
        if v is not None and keep(f, bool(quotes(f))):
            fields[f] = v

    explicit_open = {_canon(f) for f in ex.no_restriction_fields if quotes(_canon(f))}
    present = set(fields)
    unresolved = [f for f in CORE_FIELDS if f not in present and f not in explicit_open]
    # Course and level are alternatives: knowing either is enough to compare programme level.
    if present & {"eligible_courses", "eligible_education_levels"}:
        unresolved = [f for f in unresolved if f not in ("eligible_courses", "eligible_education_levels")]
    else:
        unresolved = [f for f in unresolved if f != "eligible_courses"]
    # Heuristic: in a full guideline document (several core criteria found), silence about a minimum
    # percentage means none is stated, rather than "not yet read". Listing pages never get this.
    is_guideline = doc.kind == "pdf" or bool(re.search(r"guideline|eligib", doc.url, re.I))
    if is_guideline and len(present & set(CORE_FIELDS)) >= 3 and "min_percentage" in unresolved:
        unresolved.remove("min_percentage")

    reliability = classify_source(doc.url, trusted_domains)
    scoring_fields = ("academic_year", "deadline", "max_family_income", "eligible_categories", "eligible_states", "min_percentage")
    confidence = round(0.2 + 0.8 * sum(f in present for f in scoring_fields) / len(scoring_fields), 2)

    guideline = _url_ok(ex.guideline_url, doc) or (doc.url if doc.kind == "pdf" or re.search(r"guideline|eligib", doc.url, re.I) else None)
    ay = fields.get("academic_year")
    provider = squash_ws(ex.provider)[:200] if ex.provider else None
    return Scholarship(
        id=make_scholarship_id(name.lower(), provider, ay),
        name=name[:200], provider=provider, provider_type=_provider_type(ex.provider_type),
        description=squash_ws(ex.description)[:600] if ex.description else None,
        tags=[t.lower()[:40] for t in ex.tags[:6]],
        application_url=_url_ok(ex.application_url, doc), guideline_url=guideline,
        evidence=verified_evidence, source_urls=[doc.url], source_reliability=reliability,
        retrieved_at=doc.retrieved_at, last_verified_at=doc.retrieved_at,
        extraction_confidence=confidence, unresolved_fields=unresolved + [f"{d} (unverified claim dropped)" for d in dropped if d not in fields],
        **fields,
    )


# --------------------------------------------------------------------------- dedupe & merge
def _name_tokens(name: str) -> set[str]:
    stop = {"the", "of", "for", "and", "scheme", "scholarship", "central", "sector", "students", "student", "to", "in"}
    return {t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in stop}


_GENERIC_NAME_TOKENS = {"merit", "need", "based", "national", "post", "pre", "matric", "girls", "research", "fellowship", "general",
                        "minority", "education", "higher", "top", "class", "college", "university", "sector", "welfare", "state"}


def same_scheme(a: Scholarship, b: Scholarship) -> bool:
    """Same scheme if names are near-identical, or one name is the other plus a short prefix/suffix
    (e.g. 'AICTE Swanath Scholarship' vs 'Swanath Scholarship Scheme') and the shared part is distinctive."""
    if a.id == b.id:
        return True
    ta, tb = _name_tokens(a.name), _name_tokens(b.name)
    if not ta or not tb:
        return False
    if a.academic_year and b.academic_year and a.academic_year != b.academic_year:
        return False
    inter = ta & tb
    if len(inter) / len(ta | tb) >= 0.8:
        return True
    small = ta if len(ta) <= len(tb) else tb
    return small <= (ta & tb) and (len(small) >= 2 or bool(small - _GENERIC_NAME_TOKENS))


_SCALARS = ("max_family_income", "min_percentage", "min_cgpa", "award_amount", "award_count", "deadline", "opening_date")
_LISTS = ("eligible_courses", "eligible_education_levels", "eligible_years", "eligible_categories", "eligible_states",
          "eligible_institutions", "eligible_genders", "required_documents", "tags", "source_urls")


def merge(base: Scholarship, other: Scholarship) -> Scholarship:
    """Combine two records of the same scheme. Disagreeing verified scalars become Conflicts."""
    data = base.model_dump()
    conflicts = list(base.conflicts)
    for f in _SCALARS:
        a, b = getattr(base, f), getattr(other, f)
        if a is None and b is not None:
            data[f] = b
        elif a is not None and b is not None and a != b and not any(c.field == f for c in conflicts):
            conflicts.append(Conflict(field=f, values=[str(a), str(b)], source_urls=[*base.source_urls[:1], *other.source_urls[:1]]))
    for f in _LISTS:
        merged = list(getattr(base, f))
        for item in getattr(other, f):
            if item not in merged:
                merged.append(item)
        data[f] = merged
    for f in ("provider", "description", "academic_year", "application_url", "guideline_url", "award_amount_text"):
        data[f] = getattr(base, f) or getattr(other, f)
    seen = {(e.field, e.quote) for e in base.evidence}
    data["evidence"] = [*base.evidence, *(e for e in other.evidence if (e.field, e.quote) not in seen)]
    still = set(base.unresolved_fields) & set(other.unresolved_fields)
    data["unresolved_fields"] = [f for f in base.unresolved_fields if f in still or f.endswith("dropped)")]
    data["conflicts"] = [c.model_dump() for c in conflicts]
    data["extraction_confidence"] = max(base.extraction_confidence, other.extraction_confidence)
    if other.source_reliability.is_authoritative and not base.source_reliability.is_authoritative:
        data["source_reliability"] = other.source_reliability
    data["retrieved_at"] = max(filter(None, (base.retrieved_at, other.retrieved_at)), default=None)
    data["last_verified_at"] = data["retrieved_at"]
    if other.provider_type != ProviderType.UNKNOWN and base.provider_type == ProviderType.UNKNOWN:
        data["provider_type"] = other.provider_type
    return Scholarship.model_validate(data)


def dedupe(items: list[Scholarship]) -> list[Scholarship]:
    out: list[Scholarship] = []
    for s in items:
        for i, existing in enumerate(out):
            if same_scheme(existing, s):
                out[i] = merge(existing, s)
                break
        else:
            out.append(s)
    return out
