"""Deterministic eligibility and application-status evaluation. No LLM involvement.

Eligibility status and application status are independent: a student can satisfy every
criterion for a scheme whose window has closed.

A criterion is only evaluated when the scholarship states it. When the extractor could not
determine a *core* criterion (listed in ``unresolved_fields``) it becomes an UNKNOWN result,
so a record can never be ELIGIBLE on the strength of fields nobody read.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from models.evaluation import (
    CriterionOutcome,
    CriterionResult,
    EligibilityEvaluation,
    EligibilityStatus,
)
from models.scholarship import ApplicationStatus, Scholarship, SourceReliability
from models.student import ApplicationType, StudentProfile

MET, NOT_MET, UNKNOWN = CriterionOutcome.MET, CriterionOutcome.NOT_MET, CriterionOutcome.UNKNOWN

# Fields whose absence from the source makes the result UNKNOWN rather than "no restriction".
CORE_FIELDS = (
    "max_family_income",
    "eligible_categories",
    "eligible_states",
    "eligible_courses",
    "eligible_education_levels",
    "min_percentage",
)

DEFAULT_STALE_SOURCE_DAYS = 180
DEFAULT_FRESHNESS_DAYS = 14

_ALL_INDIA = {"all india", "india", "all states", "pan india", "any", "all"}

# Course aliases -> canonical group. Used only to avoid false NOT_MET results.
_COURSE_GROUPS: dict[str, set[str]] = {
    "engineering": {"be", "btech", "bachelorofengineering", "bacheloroftechnology", "engineering", "mtech", "me"},
    "science": {"bsc", "bachelorofscience", "msc", "science"},
    "commerce": {"bcom", "bachelorofcommerce", "mcom", "commerce"},
    "arts": {"ba", "bachelorofarts", "ma", "arts", "humanities"},
    "medicine": {"mbbs", "bds", "medicine", "medical"},
    "management": {"bba", "mba", "management"},
    "computer applications": {"bca", "mca", "computerapplications"},
    "law": {"llb", "llm", "law"},
    "diploma": {"diploma", "polytechnic"},
}


# --------------------------------------------------------------------------- helpers
def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower().replace(".", "")).strip()


def _squash(text: str) -> str:
    return _norm(text).replace(" ", "")


def _course_group(course: str) -> str | None:
    key = _squash(course)
    for group, aliases in _COURSE_GROUPS.items():
        if key in aliases:
            return group
    return None


def _courses_match(student_course: str, listed: list[str]) -> bool | None:
    """True/False when we can tell, None when the names cannot be compared reliably.

    False is only returned when the student's course and *every* listed course belong to a
    known group and none overlap; an unfamiliar name (e.g. a branch) yields None, not False.
    """
    s = _squash(student_course)
    s_group = _course_group(student_course)
    for item in listed:
        i = _squash(item)
        if i and (s == i or s in i or i in s):
            return True
        if s_group and s_group == _course_group(item):
            return True
    if s_group and all(_course_group(i) for i in listed):
        return False
    return None


def _tokens(name: str) -> set[str]:
    stop = {"of", "the", "and", "college", "university", "institute", "for", "in"}
    return {t for t in _norm(name).split() if t not in stop}


def _institution_matches(student_inst: str, listed: list[str]) -> bool:
    s_norm, s_tok = _norm(student_inst), _tokens(student_inst)
    for item in listed:
        i_norm, i_tok = _norm(item), _tokens(item)
        if s_norm == i_norm or (len(i_norm) > 3 and (i_norm in s_norm or s_norm in i_norm)):
            return True
        if i_tok and s_tok and len(i_tok & s_tok) / len(i_tok) >= 0.75:
            return True
    return False


def _fmt_inr(amount: int) -> str:
    s = str(int(amount))
    if len(s) <= 3:
        return f"₹{s}"
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return "₹" + ",".join(parts + [tail])


def academic_year_start(label: str | None) -> int | None:
    """'2026-27' -> 2026. Returns None if the label cannot be parsed."""
    if not label:
        return None
    m = re.search(r"(20\d{2})\s*[-–/]\s*(\d{2,4})", label)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(20\d{2})\b", label)
    return int(m.group(1)) if m else None


def current_academic_year_start(today: date) -> int:
    """Indian academic years start around June."""
    return today.year if today.month >= 6 else today.year - 1


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- application status
def compute_application_status(
    sch: Scholarship,
    today: date | None = None,
    now: datetime | None = None,
    freshness_days: int = DEFAULT_FRESHNESS_DAYS,
) -> tuple[ApplicationStatus, str]:
    """Open/closed is derived from dates plus how recently they were verified.

    OPEN is only reported when the evidence is recent: a future deadline in a page read
    months ago says nothing about the window today (it may have been extended or withdrawn).
    """
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    if sch.deadline is None:
        return ApplicationStatus.UNKNOWN, "No application deadline was found in the source."
    if sch.deadline < today:
        return ApplicationStatus.CLOSED, (
            f"Deadline {sch.deadline:%d %b %Y} has passed (extensions are possible; check the official site)."
        )
    if sch.opening_date and sch.opening_date > today:
        return ApplicationStatus.UPCOMING, f"Applications open on {sch.opening_date:%d %b %Y}."
    checked = _aware(sch.last_verified_at) or _aware(sch.retrieved_at)
    if checked is None or now - checked > timedelta(days=freshness_days):
        return ApplicationStatus.UNKNOWN, (
            f"Deadline of {sch.deadline:%d %b %Y} has not passed, but this was last checked "
            f"{'at an unknown time' if checked is None else checked.strftime('%d %b %Y')}. "
            "Re-verify before relying on it."
        )
    return ApplicationStatus.OPEN, f"Open until {sch.deadline:%d %b %Y} (verified {checked:%d %b %Y})."


# --------------------------------------------------------------------------- source quality
def source_problem(
    sch: Scholarship,
    today: date,
    now: datetime,
    stale_days: int = DEFAULT_STALE_SOURCE_DAYS,
) -> str | None:
    if sch.source_reliability in (SourceReliability.UNVERIFIED, SourceReliability.THIRD_PARTY):
        return (
            f"The source ({sch.source_reliability.value.lower()}) is not an official or verified provider page, "
            "so these requirements cannot be relied on."
        )
    ay, cur = academic_year_start(sch.academic_year), current_academic_year_start(today)
    if ay is not None and ay < cur:
        return (
            f"The guidelines are for academic year {sch.academic_year}, which is older than the current "
            f"academic year ({cur}-{str(cur + 1)[-2:]})."
        )
    if sch.source_reliability != SourceReliability.DEMO:
        retrieved = _aware(sch.last_verified_at) or _aware(sch.retrieved_at)
        if retrieved is None or now - retrieved > timedelta(days=stale_days):
            return "The source was last retrieved too long ago (or never) to trust its requirements."
    return None


# --------------------------------------------------------------------------- evaluation
def evaluate(
    student: StudentProfile,
    sch: Scholarship,
    today: date | None = None,
    now: datetime | None = None,
    *,
    freshness_days: int = DEFAULT_FRESHNESS_DAYS,
    stale_days: int = DEFAULT_STALE_SOURCE_DAYS,
) -> EligibilityEvaluation:
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    unresolved = set(sch.unresolved_fields)
    results: list[CriterionResult] = []

    def add(criterion: str, outcome: CriterionOutcome, requirement: str, value: str | None, detail: str, field: str):
        results.append(CriterionResult(
            criterion=criterion, outcome=outcome, requirement=requirement,
            student_value=value, detail=detail, evidence=sch.evidence_for(field),
        ))

    def missing_in_source(criterion: str, field: str):
        if field in unresolved:
            add(criterion, UNKNOWN, f"{criterion}: not found in the source", None,
                f"{criterion} could not be determined from the source, so it cannot be checked.", field)

    def conflict(criterion: str, field: str) -> bool:
        if sch.has_conflict(field):
            c = next(c for c in sch.conflicts if c.field == field)
            add(criterion, UNKNOWN, "Sources disagree: " + " vs ".join(c.values), None,
                "Sources give conflicting values; check the official guideline.", field)
            return True
        return False

    # Family income
    if sch.max_family_income is not None and not conflict("Family income", "max_family_income"):
        op = "≤" if sch.income_limit_inclusive else "<"
        req = f"Annual family income {op} {_fmt_inr(sch.max_family_income)}"
        inc = student.annual_family_income
        lim, incl = sch.max_family_income, sch.income_limit_inclusive
        if inc is None and student.income_range_low is not None:
            lo, hi = student.income_range_low, student.income_range_high
            label = f"{_fmt_inr(lo)}–{_fmt_inr(hi)}" if hi is not None else f"above {_fmt_inr(lo)}"
            if hi is not None and (hi <= lim if incl else hi < lim):
                add("Family income", MET, req, label, "Your whole income range is within the limit.", "max_family_income")
            elif lo > lim or (not incl and lo == lim):
                add("Family income", NOT_MET, req, label, "Your whole income range is above the limit.", "max_family_income")
            else:
                add("Family income", UNKNOWN, req, label,
                    "Your income range straddles the limit; enter an exact amount to check.", "max_family_income")
        elif inc is None:
            add("Family income", UNKNOWN, req, None, "Enter your family income to check this.", "max_family_income")
        else:
            ok = inc <= lim if incl else inc < lim
            add("Family income", MET if ok else NOT_MET, req, _fmt_inr(inc),
                "Within the limit." if ok else "Above the published income limit.", "max_family_income")
    elif sch.max_family_income is None:
        missing_in_source("Family income", "max_family_income")

    # Category
    if sch.eligible_categories and not conflict("Category", "eligible_categories"):
        listed = ", ".join(c.value for c in sch.eligible_categories)
        req = f"Category: {listed}"
        if student.category is None:
            add("Category", UNKNOWN, req, None, "Select your category to check this.", "eligible_categories")
        elif student.category in sch.eligible_categories:
            add("Category", MET, req, student.category.value, "Your category is eligible.", "eligible_categories")
        elif student.category.value == "Other":
            add("Category", UNKNOWN, req, "Other", "Your category could not be matched to the listed ones.", "eligible_categories")
        else:
            add("Category", NOT_MET, req, student.category.value, "Your category is not among those listed.", "eligible_categories")
    elif not sch.eligible_categories:
        missing_in_source("Category", "eligible_categories")

    # State / domicile
    states = [s for s in sch.eligible_states if _norm(s) not in _ALL_INDIA]
    if states and not conflict("State / domicile", "eligible_states"):
        req = "Domicile / state: " + ", ".join(states)
        if not student.state:
            add("State / domicile", UNKNOWN, req, None, "Select your state to check this.", "eligible_states")
        elif _norm(student.state) in {_norm(s) for s in states}:
            add("State / domicile", MET, req, student.state, "Your state is eligible.", "eligible_states")
        else:
            add("State / domicile", NOT_MET, req, student.state, "Restricted to other state(s).", "eligible_states")
    elif not sch.eligible_states:
        missing_in_source("State / domicile", "eligible_states")

    # Education level
    if sch.eligible_education_levels:
        req = "Level: " + ", ".join(l.value for l in sch.eligible_education_levels)
        if student.education_level is None:
            add("Education level", UNKNOWN, req, None, "Select your education level.", "eligible_education_levels")
        elif student.education_level in sch.eligible_education_levels:
            add("Education level", MET, req, student.education_level.value, "Level matches.", "eligible_education_levels")
        else:
            add("Education level", NOT_MET, req, student.education_level.value, "Level is not covered.", "eligible_education_levels")
    elif not sch.eligible_courses:
        missing_in_source("Education level", "eligible_education_levels")

    # Course
    if sch.eligible_courses:
        req = "Course: " + ", ".join(sch.eligible_courses)
        if not student.course:
            add("Course", UNKNOWN, req, None, "Enter your degree / course.", "eligible_courses")
        else:
            m = _courses_match(student.course, sch.eligible_courses)
            if m is True:
                add("Course", MET, req, student.course, "Course matches.", "eligible_courses")
            elif m is False:
                add("Course", NOT_MET, req, student.course, "Course is not among those listed.", "eligible_courses")
            else:
                add("Course", UNKNOWN, req, student.course,
                    "Course names could not be compared reliably; check the guideline.", "eligible_courses")

    # Year of study
    if sch.eligible_years:
        req = "Year of study: " + ", ".join(str(y) for y in sorted(sch.eligible_years))
        if student.current_year is None:
            add("Year of study", UNKNOWN, req, None, "Enter your current year.", "eligible_years")
        elif student.current_year in sch.eligible_years:
            add("Year of study", MET, req, str(student.current_year), "Year matches.", "eligible_years")
        else:
            add("Year of study", NOT_MET, req, str(student.current_year), "Year is not covered.", "eligible_years")

    # Marks (percentage and CGPA are never converted into each other: conversion formulas vary)
    if sch.min_percentage is not None and not conflict("Minimum marks", "min_percentage"):
        req = f"Previous marks ≥ {sch.min_percentage:g}%"
        if student.previous_percentage is None:
            note = " (a CGPA was entered, but it is not converted to a percentage)" if student.previous_cgpa is not None else ""
            add("Minimum marks", UNKNOWN, req, None, "Enter your previous percentage" + note + ".", "min_percentage")
        else:
            ok = student.previous_percentage >= sch.min_percentage
            add("Minimum marks", MET if ok else NOT_MET, req, f"{student.previous_percentage:g}%",
                "Meets the minimum." if ok else "Below the minimum.", "min_percentage")
    elif sch.min_percentage is None:
        missing_in_source("Minimum marks", "min_percentage")
    if sch.min_cgpa is not None:
        req = f"Previous CGPA ≥ {sch.min_cgpa:g}"
        if student.previous_cgpa is None:
            add("Minimum CGPA", UNKNOWN, req, None, "Enter your CGPA (10-point scale).", "min_cgpa")
        else:
            ok = student.previous_cgpa >= sch.min_cgpa
            add("Minimum CGPA", MET if ok else NOT_MET, req, f"{student.previous_cgpa:g}",
                "Meets the minimum." if ok else "Below the minimum.", "min_cgpa")

    # Institution
    if sch.eligible_institutions:
        req = "Institution: " + ", ".join(sch.eligible_institutions)
        if not student.institution_name:
            add("Institution", UNKNOWN, req, None, "Enter your college / university.", "eligible_institutions")
        elif _institution_matches(student.institution_name, sch.eligible_institutions):
            add("Institution", MET, req, student.institution_name, "Your institution is listed.", "eligible_institutions")
        else:
            add("Institution", NOT_MET, req, student.institution_name,
                "Your institution is not in the list (check spelling / abbreviations).", "eligible_institutions")
    if sch.eligible_institution_types:
        req = "Institution type: " + ", ".join(sch.eligible_institution_types)
        if not student.institution_type:
            add("Institution type", UNKNOWN, req, None, "Select your institution type.", "eligible_institution_types")
        elif _norm(student.institution_type) in {_norm(t) for t in sch.eligible_institution_types}:
            add("Institution type", MET, req, student.institution_type, "Type matches.", "eligible_institution_types")
        else:
            add("Institution type", NOT_MET, req, student.institution_type, "Type is not covered.", "eligible_institution_types")

    # Gender (only when the scheme states it)
    if sch.eligible_genders:
        req = "Gender: " + ", ".join(g.value for g in sch.eligible_genders)
        if student.gender is None:
            add("Gender", UNKNOWN, req, None, "This scheme has a gender criterion; select a gender to check it.", "eligible_genders")
        elif student.gender in sch.eligible_genders:
            add("Gender", MET, req, student.gender.value, "Matches.", "eligible_genders")
        else:
            add("Gender", NOT_MET, req, student.gender.value, "Not covered.", "eligible_genders")

    # Disability
    if sch.requires_disability:
        req = "Disability certificate required"
        if student.has_disability is None:
            add("Disability", UNKNOWN, req, None, "This scheme is for students with disabilities.", "requires_disability")
        else:
            add("Disability", MET if student.has_disability else NOT_MET, req,
                "Yes" if student.has_disability else "No",
                "Matches." if student.has_disability else "Scheme is for students with disabilities.", "requires_disability")

    # Fresh vs renewal
    if sch.accepts_fresh is False or sch.accepts_renewal is False:
        accepted = [n for n, ok in (("fresh", sch.accepts_fresh), ("renewal", sch.accepts_renewal)) if ok is not False]
        req = "Accepts: " + (" and ".join(accepted) if accepted else "neither (check guideline)") + " applications"
        t = student.application_type
        if t is None:
            add("Fresh / renewal", UNKNOWN, req, None, "Say whether you are applying fresh or renewing.", "accepts_fresh")
        else:
            ok = (sch.accepts_fresh is not False) if t == ApplicationType.FRESH else (sch.accepts_renewal is not False)
            add("Fresh / renewal", MET if ok else NOT_MET, req, t.value,
                "Application type is accepted." if ok else f"{t.value} applications are not accepted.",
                "accepts_fresh" if t == ApplicationType.FRESH else "accepts_renewal")

    app_status, app_note = compute_application_status(sch, today, now, freshness_days)

    problem = source_problem(sch, today, now, stale_days)
    has_any_criterion = bool(results)
    if problem:
        status, why = EligibilityStatus.UNVERIFIED, problem
    elif not has_any_criterion:
        status, why = EligibilityStatus.UNVERIFIED, "No eligibility criteria could be extracted from the source."
    elif any(c.outcome == NOT_MET for c in results):
        failed = "; ".join(f"{c.criterion} ({c.detail.rstrip('.')})" for c in results if c.outcome == NOT_MET)
        status, why = EligibilityStatus.LIKELY_INELIGIBLE, f"A verified mandatory condition is not met: {failed}."
    elif any(c.outcome == UNKNOWN for c in results):
        unk = ", ".join(c.criterion for c in results if c.outcome == UNKNOWN)
        status, why = EligibilityStatus.POTENTIAL_MATCH, (
            f"No known exclusion, but these criteria could not be confirmed: {unk}."
        )
    else:
        status, why = EligibilityStatus.ELIGIBLE, (
            f"All {len(results)} evaluated mandatory condition(s) are satisfied."
        )

    return EligibilityEvaluation(
        scholarship_id=sch.id, status=status, explanation=why, criteria=results,
        application_status=app_status, application_note=app_note, evaluated_at=now,
    )
