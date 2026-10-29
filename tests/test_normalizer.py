from datetime import date

import pytest

from models.scholarship import AwardCountScope, ProviderType, SourceReliability
from models.student import Category, EducationLevel
from services.page_extractor import (
    Document, ExtractedEvidence as EE, ExtractedScholarship as ES, parse_dates, parse_inr_amounts, rule_extract, select_links,
)
from services.playwright_mcp_client import Link
from services.scholarship_normalizer import dedupe, merge, normalize

URL = "https://scholarships.gov.in/public/schemeGuidelines/AICTE/AICTE_3039_G.pdf"
TEXT = """AICTE – SWANATH SCHOLARSHIP SCHEME FOR STUDENTS
Academic Year 2026-27
2.0 ELIGIBILITY FOR SCHOLARSHIP:
Family income from all sources should not be more than Rs. 8 lakh per annum during the
financial year of the application.
The candidate should be currently studying in AICTE approved degree or diploma institutions.
Open to SC, ST and OBC candidates. 1000 scholarships per year at national level.
Last date for submission of applications is 31-10-2026.
Documents: Income certificate issued by State Government, Aadhaar-seeded bank passbook
"""


def doc(text=TEXT, url=URL, kind="pdf", links=()):
    return Document(url=url, title="AICTE Swanath", text=text, kind=kind, links=list(links))


def ext(**kw):
    base = dict(name="AICTE Swanath Scholarship Scheme", provider="AICTE", provider_type="central government")
    base.update(kw)
    return ES(**base)


# ---- parsers
@pytest.mark.parametrize("text,expected", [
    ("Rs. 8 lakh per annum", [800_000]), ("₹2.5 lakh", [250_000]), ("INR 2,50,000/-", [250_000]),
    ("Rs 12000", [12_000]), ("income of 1.5 lakhs", [150_000]), ("no amounts here, 2026-27", []),
])
def test_parse_inr(text, expected):
    assert parse_inr_amounts(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("31-10-2026", [date(2026, 10, 31)]), ("31/10/2026", [date(2026, 10, 31)]), ("31st October 2026", [date(2026, 10, 31)]),
    ("Oct 31, 2026", [date(2026, 10, 31)]), ("31 Feb 2026", []), ("15-11-2026 and 30/11/2026", [date(2026, 11, 15), date(2026, 11, 30)]),
])
def test_parse_dates(text, expected):
    assert parse_dates(text) == expected


# ---- verification of claims against quotes
def test_supported_claims_survive():
    s = normalize(ext(
        academic_year="2026-27", max_family_income_inr=800_000, deadline="2026-10-31",
        eligible_categories=["SC", "ST", "OBC"], award_count=1000, award_count_scope="national",
        evidence=[EE(field="academic_year", quote="Academic Year 2026-27"),
                  EE(field="max_family_income", quote="Family income from all sources should not be more than Rs. 8 lakh per annum"),
                  EE(field="deadline", quote="Last date for submission of applications is 31-10-2026"),
                  EE(field="eligible_categories", quote="Open to SC, ST and OBC candidates"),
                  EE(field="award_count", quote="1000 scholarships per year at national level")]), doc())
    assert s.max_family_income == 800_000 and s.income_limit_inclusive
    assert s.deadline == date(2026, 10, 31) and s.academic_year == "2026-27"
    assert s.eligible_categories == [Category.SC, Category.ST, Category.OBC]
    assert s.award_count == 1000 and s.award_count_scope == AwardCountScope.NATIONAL
    assert s.source_reliability == SourceReliability.OFFICIAL_GOVERNMENT and s.provider_type == ProviderType.CENTRAL_GOVERNMENT
    assert {e.field for e in s.evidence} >= {"max_family_income", "deadline"}
    assert all(e.source_url == URL and e.retrieved_at for e in s.evidence)
    assert s.guideline_url == URL


def test_value_not_supported_by_quote_is_dropped():
    s = normalize(ext(max_family_income_inr=250_000,  # quote says 8 lakh
                      evidence=[EE(field="max_family_income", quote="should not be more than Rs. 8 lakh per annum")]), doc())
    assert s.max_family_income is None and "max_family_income" in s.unresolved_fields


def test_fabricated_quote_is_dropped():
    s = normalize(ext(max_family_income_inr=800_000,
                      evidence=[EE(field="max_family_income", quote="income must be under Rs. 8 lakh as announced yesterday")]), doc())
    assert s.max_family_income is None and not s.evidence


def test_claim_without_any_quote_is_dropped():
    s = normalize(ext(deadline="2026-12-31", award_amount_inr=50_000), doc())
    assert s.deadline is None and s.award_amount is None


def test_quote_with_wrong_date_is_dropped():
    s = normalize(ext(deadline="2026-11-30", evidence=[EE(field="deadline", quote="Last date for submission of applications is 31-10-2026")]), doc())
    assert s.deadline is None


def test_category_not_corroborated_by_quote_is_dropped():
    s = normalize(ext(eligible_categories=["SC", "General"],
                      evidence=[EE(field="eligible_categories", quote="Open to SC, ST and OBC candidates")]), doc())
    assert s.eligible_categories == [Category.SC]


def test_lowercase_sc_inside_word_not_matched_as_category():
    d = doc("Applicants scrutinised carefully; scholarship open to the poor.")
    s = normalize(ext(eligible_categories=["SC"], evidence=[EE(field="eligible_categories", quote="Applicants scrutinised carefully")]), d)
    assert s.eligible_categories == []


def test_strict_income_wording():
    d = doc("Annual family income should be less than Rs. 2.5 lakh to be eligible.")
    s = normalize(ext(max_family_income_inr=250_000, income_limit_inclusive=True,
                      evidence=[EE(field="max_family_income", quote="family income should be less than Rs. 2.5 lakh")]), d)
    assert s.income_limit_inclusive is False


def test_untrusted_urls_filtered():
    d = doc(links=[Link("Apply", "https://scholarships.gov.in/apply")], kind="page", url="https://scholarships.gov.in/x")
    s = normalize(ext(application_url="https://scholarships.gov.in/apply", guideline_url="http://169.254.169.254/x"), d)
    assert s.application_url == "https://scholarships.gov.in/apply" and s.guideline_url is None
    s = normalize(ext(application_url="https://evil.example.net/phish"), d)
    assert s.application_url is None


def test_unofficial_source_classified_not_official():
    d = doc(url="https://random-blog.com/swanath", kind="page")
    assert normalize(ext(), d).source_reliability == SourceReliability.UNVERIFIED


def test_unresolved_core_fields_and_explicit_no_restriction():
    s = normalize(ext(no_restriction_fields=["eligible_states"],
                      evidence=[EE(field="eligible_states", quote="Open to SC, ST and OBC candidates")]), doc())
    assert "eligible_states" not in s.unresolved_fields and "max_family_income" in s.unresolved_fields
    s2 = normalize(ext(), doc())
    assert {"eligible_states", "max_family_income", "eligible_categories"} <= set(s2.unresolved_fields)


def test_nameless_record_rejected():
    assert normalize(ext(name=" "), doc()) is None


# ---- dedupe & conflicts
def test_dedupe_merges_same_scheme_and_flags_conflicts():
    a = normalize(ext(academic_year="2026-27", max_family_income_inr=800_000, evidence=[EE(field="academic_year", quote="Academic Year 2026-27"),
                  EE(field="max_family_income", quote="not be more than Rs. 8 lakh per annum")]), doc())
    d2 = Document(url="https://scholarships.gov.in/faq", title="FAQ", kind="page",
                  text="Academic Year 2026-27. Income limit: family income not more than Rs. 2.5 lakh per annum.")
    b = normalize(ext(name="AICTE Swanath Scholarship", academic_year="2026-27", max_family_income_inr=250_000, evidence=[
        EE(field="academic_year", quote="Academic Year 2026-27"), EE(field="max_family_income", quote="family income not more than Rs. 2.5 lakh per annum")]), d2)
    out = dedupe([a, b, normalize(ext(name="Totally Different Fellowship"), doc())])
    assert len(out) == 2
    merged = next(s for s in out if "Swanath" in s.name)
    assert merged.has_conflict("max_family_income") and len(merged.source_urls) == 2
    assert len({(e.field, e.quote) for e in merged.evidence}) == len(merged.evidence)


def test_different_academic_years_not_merged():
    a = normalize(ext(academic_year="2025-26", evidence=[EE(field="academic_year", quote="Academic Year 2025-26")]), doc("Academic Year 2025-26 scheme"))
    b = normalize(ext(academic_year="2026-27", evidence=[EE(field="academic_year", quote="Academic Year 2026-27")]), doc())
    assert len(dedupe([a, b])) == 2


# ---- link selection
def test_select_links_prefers_relevant_official_and_skips_junk():
    from models.student import StudentProfile
    from services.page_extractor import profile_terms
    links = [
        Link("Specifications", "https://scholarships.gov.in/public/g/AICTE_1.pdf", "AICTE Pragati Scholarship for technical degree students"),
        Link("Specifications", "https://scholarships.gov.in/public/g/Labour.pdf", "Ministry of Labour scholarship for beedi workers"),
        Link("Login", "https://scholarships.gov.in/login", "login to apply"),
        Link("Guidelines", "https://evil.example.net/guidelines.pdf", "guidelines"),
        Link("FAQ", "http://127.0.0.1/faq", "faq"),
        Link("Watch", "https://youtu.be/x", "scheme video"),
    ]
    d = Document(url="https://scholarships.gov.in/All-Scholarships", title="", text="", links=links, kind="page")
    terms = profile_terms(StudentProfile(education_level="Undergraduate", course="B.Tech"))
    got = select_links(d, terms, 5, exclude=set())
    assert [l.url for l in got][0].endswith("AICTE_1.pdf")
    urls = {l.url for l in got}
    assert not any("login" in u or "evil" in u or "127.0.0.1" in u or "youtu" in u for u in urls)


# ---- rule-based fallback
def test_rule_extract_reads_only_verifiable_facts():
    out = rule_extract(doc())
    assert len(out.scholarships) == 1
    s = normalize(out.scholarships[0], doc())
    assert s.max_family_income == 800_000 and s.deadline == date(2026, 10, 31) and s.academic_year == "2026-27"
    assert s.eligible_categories == []  # the rule extractor does not guess categories


def test_rule_extract_ignores_listing_pages_and_ambiguous_dates():
    assert rule_extract(Document(url="https://scholarships.gov.in/", title="NSP", text="Welcome to the portal. Scholarships for all.")).scholarships == []
    amb = "Central Scholarship Scheme\nClosing date for student application is 31-10-2026 and closing date for institute verification is 15-11-2026"
    assert rule_extract(Document(url=URL, title="x", text=amb)).scholarships == []


def test_portal_page_with_many_schemes_yields_no_rule_based_record():
    portal = Document(url="https://ssp.karnataka.gov.in/", title="Karnataka State Scholarship Portal", kind="page", text=(
        "Karnataka State Scholarship Portal\nLast date to Submit Application for Social Welfare Department Scheme is: 31/01/2027 .\n"
        "Last date to Submit Application for Tribal Welfare Department Scheme is: 31/12/2026 ."))
    assert rule_extract(portal).scholarships == []


def test_variant_names_of_same_scheme_merge_but_generic_names_do_not():
    from tests.conftest import make_scholarship
    a = make_scholarship(id="a", name="Aicte – Swanath Scholarship", academic_year=None)
    b = make_scholarship(id="b", name="Swanath Scholarship Scheme", academic_year=None)
    assert len(dedupe([a, b])) == 1
    c = make_scholarship(id="c", name="Merit Scholarship", academic_year=None)
    d = make_scholarship(id="d", name="Merit Scholarship for Engineering Students", academic_year=None)
    assert len(dedupe([c, d])) == 2
    e = make_scholarship(id="e", name="Aicte Pragati Scholarship", academic_year=None)
    assert len(dedupe([a, e])) == 2
