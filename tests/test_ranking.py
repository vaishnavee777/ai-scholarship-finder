from datetime import timedelta

from models.evaluation import EligibilityStatus as S
from models.scholarship import ApplicationStatus, ProviderType, SourceReliability
from models.student import Category, ScholarshipKind, SearchPreferences, StudentProfile
from services.ranking_service import build_results, filter_results, matches_preferences
from services.eligibility_engine import evaluate
from tests.conftest import NOW, TODAY, ev, make_scholarship


def build(student, items, **kw):
    return build_results(student, items, TODAY, NOW, **kw)


def test_eligible_open_outranks_ineligible(student):
    good = make_scholarship(id="g", name="Good", max_family_income=300_000, deadline=TODAY + timedelta(days=30))
    bad = make_scholarship(id="b", name="Bad", max_family_income=100_000, deadline=TODAY + timedelta(days=30))
    res = build(student, [bad, good])
    assert [r.scholarship.id for r in res] == ["g", "b"]
    assert res[0].score > res[1].score


def test_closed_ranks_below_open_with_same_eligibility(student):
    a = make_scholarship(id="a", name="A", max_family_income=300_000, deadline=TODAY + timedelta(days=30))
    b = make_scholarship(id="b", name="B", max_family_income=300_000, deadline=TODAY - timedelta(days=30))
    assert [r.scholarship.id for r in build(student, [b, a])] == ["a", "b"]


def test_amount_breaks_ties(student):
    a = make_scholarship(id="a", name="A", max_family_income=300_000, award_amount=10_000)
    b = make_scholarship(id="b", name="B", max_family_income=300_000, award_amount=100_000)
    assert build(student, [a, b])[0].scholarship.id == "b"


def test_every_result_explains_itself_and_score_is_bounded(student):
    r = build(student, [make_scholarship(max_family_income=300_000)])[0]
    assert r.reasons and abs(sum(r.score_breakdown.values()) - r.score) < 0.2
    assert 0 <= r.score <= 100
    assert "probability" not in " ".join(r.reasons).lower()


def test_unofficial_source_ranks_lower(student):
    off = make_scholarship(id="o", name="O", max_family_income=300_000)
    unoff = make_scholarship(id="u", name="U", max_family_income=300_000, source_reliability=SourceReliability.UNVERIFIED)
    res = build(student, [unoff, off])
    assert res[0].scholarship.id == "o"
    assert res[1].evaluation.status == S.UNVERIFIED


def test_application_status_copied_onto_record(student):
    r = build(student, [make_scholarship(max_family_income=1, deadline=TODAY + timedelta(days=3))])[0]
    assert r.scholarship.application_status == ApplicationStatus.OPEN


def test_preferences(student):
    gov = make_scholarship(id="g", name="G", provider_type=ProviderType.CENTRAL_GOVERNMENT, max_family_income=300_000)
    corp = make_scholarship(id="c", name="C", provider_type=ProviderType.CORPORATE, max_family_income=300_000)
    student.preferences = SearchPreferences(kinds=[ScholarshipKind.CORPORATE])
    assert [r.scholarship.id for r in build(student, [gov, corp])] == ["c"]
    student.preferences = SearchPreferences(min_amount=50_000)
    assert build(student, [make_scholarship(max_family_income=300_000)]) == []  # unpublished amount cannot be confirmed


def test_filters(student):
    items = [
        make_scholarship(id="a", name="A", max_family_income=300_000, award_amount=60_000, deadline=TODAY + timedelta(days=10), provider="Alpha"),
        make_scholarship(id="b", name="B", max_family_income=1, award_amount=5_000, deadline=TODAY + timedelta(days=90), provider="Beta"),
    ]
    res = build(student, items)
    assert [r.scholarship.id for r in filter_results(res, statuses={S.ELIGIBLE})] == ["a"]
    assert [r.scholarship.id for r in filter_results(res, min_amount=50_000)] == ["a"]
    assert [r.scholarship.id for r in filter_results(res, deadline_before=TODAY + timedelta(days=30))] == ["a"]
    assert [r.scholarship.id for r in filter_results(res, provider_query="beta")] == ["b"]
    assert len(filter_results(res, open_only=True)) == 2
