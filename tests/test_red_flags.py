from datetime import timedelta

from models.evaluation import RedFlagType as T
from models.evaluation import Severity
from models.scholarship import AwardCountScope, Conflict, SourceReliability
from models.student import Category
from services.eligibility_engine import evaluate
from services.red_flag_detector import NO_AWARD_COUNT_TEXT, describe_award_count, detect_red_flags
from tests.conftest import NOW, TODAY, ev, make_scholarship


def flags(**kw):
    s = make_scholarship(**kw)
    return detect_red_flags(s, None, TODAY, NOW)


def types(fl):
    return {f.type for f in fl}


def test_unpublished_award_count_never_invents_a_number():
    fl = flags()
    f = next(f for f in fl if f.type == T.AWARD_COUNT_UNVERIFIED)
    assert f.explanation == NO_AWARD_COUNT_TEXT and f.confirmed is False
    assert T.LIMITED_AWARDS not in types(fl)


def test_limited_awards_uses_source_scope():
    s = make_scholarship(award_count=5, award_count_scope=AwardCountScope.INSTITUTION, evidence=[ev("award_count", "5 awards per college")])
    f = next(f for f in detect_red_flags(s, None, TODAY, NOW) if f.type == T.LIMITED_AWARDS)
    assert "institution" in f.explanation and "nationally" not in f.explanation
    assert f.confirmed and f.evidence == "5 awards per college"


def test_national_wording_only_when_scope_is_national():
    s = make_scholarship(award_count=5, award_count_scope=AwardCountScope.UNKNOWN)
    assert "nationally" not in describe_award_count(s)
    assert "scope not stated" in describe_award_count(s)


def test_previous_year_figure_is_labelled():
    s = make_scholarship(award_count=40, award_count_scope=AwardCountScope.PREVIOUS_YEAR, evidence=[ev("award_count")])
    f = next(f for f in detect_red_flags(s, None, TODAY, NOW) if f.type == T.LIMITED_AWARDS)
    assert "previous year" in f.explanation.lower()


def test_large_award_count_not_flagged_as_limited():
    assert T.LIMITED_AWARDS not in types(flags(award_count=10_000, award_count_scope=AwardCountScope.NATIONAL))


def test_income_flag_severity():
    low = next(f for f in flags(max_family_income=200_000) if f.type == T.STRICT_INCOME_LIMIT)
    high = next(f for f in flags(max_family_income=800_000) if f.type == T.STRICT_INCOME_LIMIT)
    assert low.severity == Severity.WARNING and high.severity == Severity.INFO


def test_deadline_flags():
    soon = flags(deadline=TODAY + timedelta(days=2))
    assert next(f for f in soon if f.type == T.DEADLINE_APPROACHING).severity == Severity.CRITICAL
    assert T.DEADLINE_PASSED in types(flags(deadline=TODAY - timedelta(days=1)))
    assert not types(flags(deadline=TODAY + timedelta(days=60))) & {T.DEADLINE_APPROACHING, T.DEADLINE_PASSED}


def test_restriction_flags():
    t = types(flags(eligible_institutions=["IIT Madras"], eligible_states=["Kerala"], eligible_categories=[Category.SC],
                    min_percentage=60, accepts_renewal=False))
    assert {T.INSTITUTION_SPECIFIC, T.STATE_RESTRICTION, T.CATEGORY_SPECIFIC, T.MINIMUM_MARKS, T.FRESH_ONLY} <= t


def test_all_india_is_not_a_state_restriction():
    assert T.STATE_RESTRICTION not in types(flags(eligible_states=["All India"]))


def test_documents_and_amount():
    assert T.MISSING_DOCUMENTS in types(flags())
    assert T.UNCLEAR_AMOUNT in types(flags())
    fl = flags(required_documents=["Income certificate", "Marksheet"], award_amount=50_000)
    assert T.UNCLEAR_AMOUNT not in types(fl)
    assert any("Income certificate" in f.explanation for f in fl if f.type == T.MISSING_DOCUMENTS)


def test_conflict_and_outdated_and_unverified():
    fl = flags(conflicts=[Conflict(field="deadline", values=["1 Nov", "15 Nov"])], academic_year="2024-25",
               source_reliability=SourceReliability.THIRD_PARTY)
    assert {T.CONFLICTING_INFO, T.OUTDATED_GUIDELINES, T.UNVERIFIED_PROVIDER} <= types(fl)
    assert all(f.severity == Severity.CRITICAL for f in fl if f.type in (T.CONFLICTING_INFO, T.OUTDATED_GUIDELINES, T.UNVERIFIED_PROVIDER))


def test_stale_verification_flag():
    old = NOW - timedelta(days=90)
    fl = flags(deadline=TODAY + timedelta(days=30), retrieved_at=old, last_verified_at=old)
    assert T.STALE_VERIFICATION in types(fl)


def test_flags_sorted_by_severity_and_carry_source():
    fl = flags(deadline=TODAY + timedelta(days=1), max_family_income=900_000)
    assert fl[0].severity == Severity.CRITICAL
    assert all(f.source_url for f in fl)
