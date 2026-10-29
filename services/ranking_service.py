"""Explainable ranking and filtering. The score is a relevance ordering, never a chance of winning."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

from models.evaluation import (
    CriterionOutcome,
    EligibilityEvaluation,
    EligibilityStatus,
    RankedResult,
    RedFlag,
)
from models.scholarship import ApplicationStatus, ProviderType, Scholarship
from models.student import ScholarshipBasis, ScholarshipKind, SearchPreferences, StudentProfile
from services.eligibility_engine import _aware

WEIGHTS = {
    "eligibility": 40, "application_open": 15, "relevance": 15,
    "location_institution": 10, "amount": 10, "evidence": 5, "freshness": 5,
}

_ELIG_POINTS = {
    EligibilityStatus.ELIGIBLE: 1.0, EligibilityStatus.POTENTIAL_MATCH: 0.55,
    EligibilityStatus.UNVERIFIED: 0.2, EligibilityStatus.LIKELY_INELIGIBLE: 0.0,
}
_APP_POINTS = {
    ApplicationStatus.OPEN: 1.0, ApplicationStatus.UPCOMING: 0.4,
    ApplicationStatus.UNKNOWN: 0.2, ApplicationStatus.CLOSED: 0.0,
}

_KIND_PROVIDERS = {
    ScholarshipKind.GOVERNMENT: {ProviderType.CENTRAL_GOVERNMENT},
    ScholarshipKind.STATE_GOVERNMENT: {ProviderType.STATE_GOVERNMENT},
    ScholarshipKind.PRIVATE_FOUNDATION: {ProviderType.PRIVATE_FOUNDATION},
    ScholarshipKind.CORPORATE: {ProviderType.CORPORATE},
}


def _outcome(ev: EligibilityEvaluation, *names: str) -> CriterionOutcome | None:
    found = [c.outcome for c in ev.criteria if c.criterion in names]
    if not found:
        return None
    if CriterionOutcome.NOT_MET in found:
        return CriterionOutcome.NOT_MET
    return CriterionOutcome.UNKNOWN if CriterionOutcome.UNKNOWN in found else CriterionOutcome.MET


def _fraction(outcomes: list[CriterionOutcome | None]) -> tuple[float, bool]:
    """(share of applicable checks that are met, whether any check applied)."""
    applicable = [o for o in outcomes if o is not None]
    if not applicable:
        return 0.5, False
    pts = sum(1.0 if o == CriterionOutcome.MET else 0.4 if o == CriterionOutcome.UNKNOWN else 0.0 for o in applicable)
    return pts / len(applicable), True


def _amount_points(amount: int | None) -> float:
    if not amount:
        return 0.0
    return min(1.0, math.log10(max(amount, 1)) / 5.5) if amount >= 1000 else 0.1


def rank(
    sch: Scholarship, ev: EligibilityEvaluation, flags: list[RedFlag], now: datetime | None = None,
) -> RankedResult:
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []

    elig = _ELIG_POINTS[ev.status]
    reasons.append(f"Eligibility: {ev.status.value.lower()}")

    app = _APP_POINTS[ev.application_status]
    reasons.append(f"Application {ev.application_status.value.lower()}")

    rel, rel_any = _fraction([_outcome(ev, "Course"), _outcome(ev, "Education level"), _outcome(ev, "Year of study")])
    if rel_any:
        reasons.append("Course / level relevance " + ("matches" if rel >= 0.9 else "partly confirmed" if rel > 0.3 else "does not match"))

    loc, loc_any = _fraction([_outcome(ev, "State / domicile"), _outcome(ev, "Institution", "Institution type")])
    if loc_any:
        reasons.append("State / institution " + ("compatible" if loc >= 0.9 else "partly confirmed" if loc > 0.3 else "incompatible"))

    amt = _amount_points(sch.award_amount)
    reasons.append("Published amount available" if sch.award_amount else "Award amount not published")

    ev_fields = {e.field for e in sch.evidence}
    completeness = min(1.0, len(ev_fields) / 5) * (1.0 if sch.source_reliability.is_authoritative or sch.is_demo else 0.5)
    reasons.append("Backed by official evidence" if sch.source_reliability.is_authoritative else "Limited / unofficial evidence")

    checked = _aware(sch.last_verified_at) or _aware(sch.retrieved_at)
    if checked is None:
        fresh = 0.0
    else:
        age = (now - checked).days
        fresh = 1.0 if age <= 7 else 0.6 if age <= 30 else 0.2 if age <= 180 else 0.0
    breakdown = {
        "eligibility": elig * WEIGHTS["eligibility"],
        "application_open": app * WEIGHTS["application_open"],
        "relevance": rel * WEIGHTS["relevance"],
        "location_institution": loc * WEIGHTS["location_institution"],
        "amount": amt * WEIGHTS["amount"],
        "evidence": completeness * WEIGHTS["evidence"],
        "freshness": fresh * WEIGHTS["freshness"],
    }
    return RankedResult(
        scholarship=sch, evaluation=ev, red_flags=flags,
        score=round(sum(breakdown.values()), 1),
        score_breakdown={k: round(v, 1) for k, v in breakdown.items()}, reasons=reasons,
    )


def rank_all(results: list[RankedResult]) -> list[RankedResult]:
    return sorted(results, key=lambda r: (-r.score, r.scholarship.name.lower()))


# --------------------------------------------------------------------------- preferences & filters
def matches_preferences(sch: Scholarship, prefs: SearchPreferences, ev: EligibilityEvaluation) -> bool:
    if prefs.kinds:
        allowed = set().union(*(_KIND_PROVIDERS[k] for k in prefs.kinds))
        if sch.provider_type not in allowed and sch.provider_type != ProviderType.UNKNOWN:
            return False
    if prefs.bases:
        tags = {t.lower() for t in sch.tags}
        wanted = set()
        for b in prefs.bases:
            wanted |= {
                ScholarshipBasis.MERIT: {"merit"}, ScholarshipBasis.NEED: {"need", "need-based", "means"},
                ScholarshipBasis.RESEARCH: {"research", "fellowship"},
                ScholarshipBasis.COURSE_OR_INSTITUTION: {"course-specific", "institution-specific"},
            }[b]
        if tags and not (tags & wanted):
            return False
    if prefs.open_only and ev.application_status != ApplicationStatus.OPEN:
        return False
    if prefs.min_amount and (sch.award_amount is None or sch.award_amount < prefs.min_amount):
        # unpublished amounts are kept out only when the user set a floor: we cannot confirm they meet it
        return False
    return True


def filter_results(
    results: list[RankedResult],
    statuses: set[EligibilityStatus] | None = None,
    provider_types: set[ProviderType] | None = None,
    min_amount: int | None = None,
    deadline_before: date | None = None,
    open_only: bool = False,
    provider_query: str = "",
) -> list[RankedResult]:
    out = []
    q = provider_query.strip().lower()
    for r in results:
        s = r.scholarship
        if statuses and r.evaluation.status not in statuses:
            continue
        if provider_types and s.provider_type not in provider_types:
            continue
        if min_amount and (s.award_amount or 0) < min_amount:
            continue
        if deadline_before and (s.deadline is None or s.deadline > deadline_before):
            continue
        if open_only and r.evaluation.application_status != ApplicationStatus.OPEN:
            continue
        if q and q not in (s.provider or "").lower() and q not in s.name.lower():
            continue
        out.append(r)
    return out


def build_results(
    student: StudentProfile, scholarships: list[Scholarship], today: date | None = None,
    now: datetime | None = None, *, freshness_days: int = 14, apply_preferences: bool = True,
) -> list[RankedResult]:
    """Evaluate, flag, filter by search preferences and rank."""
    from services.eligibility_engine import evaluate
    from services.red_flag_detector import detect_red_flags

    now = now or datetime.now(timezone.utc)
    out = []
    for s in scholarships:
        ev = evaluate(student, s, today, now, freshness_days=freshness_days)
        if apply_preferences and not matches_preferences(s, student.preferences, ev):
            continue
        s = s.model_copy(update={"application_status": ev.application_status})
        out.append(rank(s, ev, detect_red_flags(s, ev, today, now, freshness_days), now))
    return rank_all(out)
