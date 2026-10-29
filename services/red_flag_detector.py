"""Red-flag detection. Every flag says whether it is a source-confirmed restriction or an inference.

Award counts are never extrapolated: a figure is only described with the scope the source gave,
and an unpublished figure is reported as "Number of awards not publicly verified."
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from models.evaluation import EligibilityEvaluation, RedFlag, RedFlagType, Severity
from models.scholarship import AwardCountScope, Scholarship, SourceReliability
from services.eligibility_engine import (
    DEFAULT_FRESHNESS_DAYS,
    _aware,
    _fmt_inr,
    academic_year_start,
    current_academic_year_start,
)

STRICT_INCOME_LIMIT = 250_000
DEADLINE_WARNING_DAYS = 14
DEADLINE_CRITICAL_DAYS = 3
LIMITED_AWARD_THRESHOLD = 100

NO_AWARD_COUNT_TEXT = "Number of awards not publicly verified."


def describe_award_count(sch: Scholarship) -> str:
    """Wording for the award count that never overstates its scope."""
    if sch.award_count is None:
        return NO_AWARD_COUNT_TEXT
    scope = {
        AwardCountScope.NATIONAL: "nationally",
        AwardCountScope.STATE: "for a state allocation",
        AwardCountScope.CATEGORY: "for a category allocation",
        AwardCountScope.INSTITUTION: "at an institution level",
        AwardCountScope.PREVIOUS_YEAR: "selected in a previous year (not a guaranteed quota)",
        AwardCountScope.UNKNOWN: "(scope not stated by the source)",
    }[sch.award_count_scope]
    return f"{sch.award_count:,} award(s) {scope}"


def detect_red_flags(
    sch: Scholarship,
    evaluation: EligibilityEvaluation | None = None,
    today: date | None = None,
    now: datetime | None = None,
    freshness_days: int = DEFAULT_FRESHNESS_DAYS,
) -> list[RedFlag]:
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    flags: list[RedFlag] = []
    default_url = sch.primary_source_url

    def flag(t: RedFlagType, sev: Severity, text: str, field: str | None, confirmed: bool):
        ev = sch.evidence_for(field)[:1] if field else []
        flags.append(RedFlag(
            type=t, severity=sev, explanation=text,
            evidence=ev[0].quote if ev else None,
            source_url=ev[0].source_url if ev else default_url,
            confirmed=confirmed and bool(ev) or (confirmed and sch.is_demo),
        ))

    if sch.max_family_income is not None:
        strict = sch.max_family_income <= STRICT_INCOME_LIMIT
        op = "up to" if sch.income_limit_inclusive else "below"
        flag(RedFlagType.STRICT_INCOME_LIMIT, Severity.WARNING if strict else Severity.INFO,
             f"Family income must be {op} {_fmt_inr(sch.max_family_income)} per year"
             + (" (a low cap)." if strict else "."), "max_family_income", True)

    if sch.award_count is None:
        flag(RedFlagType.AWARD_COUNT_UNVERIFIED, Severity.INFO, NO_AWARD_COUNT_TEXT, None, False)
    elif sch.award_count <= LIMITED_AWARD_THRESHOLD:
        extra = ""
        if sch.award_count_scope == AwardCountScope.PREVIOUS_YEAR:
            extra = " This is a previous year's figure, not necessarily this year's quota."
        flag(RedFlagType.LIMITED_AWARDS, Severity.WARNING,
             f"Limited seats: {describe_award_count(sch)}.{extra}", "award_count", True)

    if sch.eligible_institutions:
        flag(RedFlagType.INSTITUTION_SPECIFIC, Severity.WARNING,
             "Only students of specific institutions can apply: " + ", ".join(sch.eligible_institutions[:5]) + ".",
             "eligible_institutions", True)
    states = [s for s in sch.eligible_states if s.strip().lower() not in ("all india", "india", "all states")]
    if states:
        flag(RedFlagType.STATE_RESTRICTION, Severity.WARNING,
             "Restricted to residents / domiciles of: " + ", ".join(states[:6]) + ".", "eligible_states", True)
    if sch.eligible_categories:
        flag(RedFlagType.CATEGORY_SPECIFIC, Severity.INFO,
             "Open only to: " + ", ".join(c.value for c in sch.eligible_categories) + ".", "eligible_categories", True)
    if sch.min_percentage is not None:
        flag(RedFlagType.MINIMUM_MARKS, Severity.INFO,
             f"Minimum previous marks: {sch.min_percentage:g}%.", "min_percentage", True)
    if sch.min_cgpa is not None:
        flag(RedFlagType.MINIMUM_MARKS, Severity.INFO, f"Minimum CGPA: {sch.min_cgpa:g}.", "min_cgpa", True)
    if sch.accepts_renewal is False:
        flag(RedFlagType.FRESH_ONLY, Severity.WARNING, "Only fresh applications are accepted; renewals are not.",
             "accepts_renewal", True)
    if sch.accepts_fresh is False:
        flag(RedFlagType.RENEWAL_REQUIREMENT, Severity.WARNING,
             "Only renewals are accepted; first-time applicants are not eligible.", "accepts_fresh", True)

    # Deadlines
    if sch.deadline is not None:
        days = (sch.deadline - today).days
        if days < 0:
            flag(RedFlagType.DEADLINE_PASSED, Severity.CRITICAL,
                 f"The deadline ({sch.deadline:%d %b %Y}) has passed.", "deadline", True)
        elif days <= DEADLINE_WARNING_DAYS:
            flag(RedFlagType.DEADLINE_APPROACHING,
                 Severity.CRITICAL if days <= DEADLINE_CRITICAL_DAYS else Severity.WARNING,
                 f"Deadline in {days} day(s) ({sch.deadline:%d %b %Y}).", "deadline", True)
        checked = _aware(sch.last_verified_at) or _aware(sch.retrieved_at)
        if days >= 0 and not sch.is_demo and (checked is None or (now - checked).days > freshness_days):
            flag(RedFlagType.STALE_VERIFICATION, Severity.WARNING,
                 "The application status was not verified recently; confirm on the official site.", None, False)

    if not sch.required_documents:
        flag(RedFlagType.MISSING_DOCUMENTS, Severity.INFO,
             "The source did not list required documents; check the official guideline.", None, False)
    else:
        certs = [d for d in sch.required_documents if "certificate" in d.lower()]
        if certs:
            flag(RedFlagType.MISSING_DOCUMENTS, Severity.INFO,
                 "Certificates needed (can take time to obtain): " + ", ".join(certs[:4]) + ".",
                 "required_documents", True)

    for c in sch.conflicts:
        flags.append(RedFlag(
            type=RedFlagType.CONFLICTING_INFO, severity=Severity.CRITICAL,
            explanation=f"Sources disagree on {c.field}: " + " vs ".join(c.values) + ".",
            evidence=None, source_url=(c.source_urls[0] if c.source_urls else default_url), confirmed=True,
        ))

    ay = academic_year_start(sch.academic_year)
    if ay is not None and ay < current_academic_year_start(today):
        flag(RedFlagType.OUTDATED_GUIDELINES, Severity.CRITICAL,
             f"These guidelines are for {sch.academic_year}; they may not apply to the current year.",
             "academic_year", True)
    elif ay is None and not sch.is_demo:
        flag(RedFlagType.OUTDATED_GUIDELINES, Severity.INFO,
             "No academic year was found, so it is unclear which year's guidelines these are.", None, False)

    if sch.source_reliability in (SourceReliability.UNVERIFIED, SourceReliability.THIRD_PARTY):
        flag(RedFlagType.UNVERIFIED_PROVIDER, Severity.CRITICAL,
             f"Source is not a verified official page ({sch.source_reliability.value.lower()}). "
             "Confirm on the provider's official website before applying.", None, True)

    if sch.award_amount is None:
        flag(RedFlagType.UNCLEAR_AMOUNT, Severity.INFO,
             "The award amount is not published in the source"
             + (f" (source says: “{sch.award_amount_text}”)" if sch.award_amount_text else "") + ".", None, False)

    flags.sort(key=lambda f: -f.severity.rank)
    return flags
