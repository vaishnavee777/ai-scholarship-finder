from datetime import date, datetime, timedelta, timezone

import pytest

from models.evaluation import CriterionOutcome as O
from models.evaluation import EligibilityStatus as S
from models.scholarship import ApplicationStatus as A
from models.scholarship import Conflict, SourceReliability
from models.student import Category, EducationLevel, Gender, StudentProfile
from services.eligibility_engine import compute_application_status, evaluate
from tests.conftest import NOW, TODAY, ev, make_scholarship


def run(student, **kw):
    return evaluate(student, make_scholarship(**kw), TODAY, NOW)


def outcome(res, name):
    return next(c.outcome for c in res.criteria if c.criterion == name)


# ---- income thresholds
@pytest.mark.parametrize("income,expected", [(249_999, O.MET), (250_000, O.MET), (250_001, O.NOT_MET)])
def test_income_boundary_inclusive(income, expected):
    r = run(StudentProfile(annual_family_income=income), max_family_income=250_000)
    assert outcome(r, "Family income") == expected


def test_income_boundary_exclusive():
    s = StudentProfile(annual_family_income=250_000)
    r = run(s, max_family_income=250_000, income_limit_inclusive=False)
    assert outcome(r, "Family income") == O.NOT_MET


def test_income_missing_gives_potential_match():
    r = run(StudentProfile(), max_family_income=250_000)
    assert outcome(r, "Family income") == O.UNKNOWN
    assert r.status == S.POTENTIAL_MATCH


def test_income_zero_is_a_real_value():
    r = run(StudentProfile(annual_family_income=0), max_family_income=100)
    assert outcome(r, "Family income") == O.MET


# ---- overall status
def test_all_met_is_eligible(student):
    r = run(student, max_family_income=300_000, eligible_categories=[Category.OBC, Category.SC],
            eligible_states=["Karnataka"], eligible_education_levels=[EducationLevel.UNDERGRADUATE],
            min_percentage=60, eligible_years=[1, 2, 3])
    assert r.status == S.ELIGIBLE and not r.unmet and not r.unknown


def test_one_failure_is_likely_ineligible(student):
    r = run(student, max_family_income=100_000, eligible_categories=[Category.OBC])
    assert r.status == S.LIKELY_INELIGIBLE
    assert "Family income" in r.explanation


def test_failure_beats_unknown(student):
    student.state = None
    r = run(student, max_family_income=100_000, eligible_states=["Kerala"])
    assert r.status == S.LIKELY_INELIGIBLE


def test_unresolved_core_field_prevents_eligible(student):
    r = run(student, max_family_income=300_000, unresolved_fields=["eligible_categories"])
    assert r.status == S.POTENTIAL_MATCH
    assert outcome(r, "Category") == O.UNKNOWN


def test_no_criteria_is_unverified(student):
    assert run(student).status == S.UNVERIFIED


# ---- category
def test_category_restriction(student):
    assert outcome(run(student, eligible_categories=[Category.SC, Category.ST]), "Category") == O.NOT_MET
    assert outcome(run(student, eligible_categories=[Category.OBC]), "Category") == O.MET


def test_category_missing_and_other():
    scs = dict(eligible_categories=[Category.SC])
    assert outcome(run(StudentProfile(), **scs), "Category") == O.UNKNOWN
    assert outcome(run(StudentProfile(category=Category.OTHER), **scs), "Category") == O.UNKNOWN


# ---- state / domicile
def test_state_case_insensitive_and_all_india(student):
    assert outcome(run(student, eligible_states=["karnataka"]), "State / domicile") == O.MET
    assert outcome(run(student, eligible_states=["Kerala"]), "State / domicile") == O.NOT_MET
    assert not any(c.criterion == "State / domicile" for c in run(student, eligible_states=["All India"], max_family_income=1_000_000).criteria)


# ---- course / level / year / marks
def test_course_alias_match(student):
    assert outcome(run(student, eligible_courses=["B.Tech", "B.Sc"]), "Course") == O.MET


def test_course_clear_mismatch(student):
    assert outcome(run(student, eligible_courses=["MBBS", "B.Com"]), "Course") == O.NOT_MET


def test_course_unfamiliar_names_not_ruled_out(student):
    assert outcome(run(student, eligible_courses=["Engineering"]), "Course") == O.MET
    assert outcome(run(student, eligible_courses=["Computer Science Engineering"]), "Course") == O.UNKNOWN
    assert outcome(run(student, eligible_courses=["Marine Biotechnology"]), "Course") == O.UNKNOWN


def test_level_and_year(student):
    assert outcome(run(student, eligible_education_levels=[EducationLevel.PHD]), "Education level") == O.NOT_MET
    assert outcome(run(student, eligible_years=[1]), "Year of study") == O.NOT_MET
    assert outcome(run(StudentProfile(), eligible_years=[1]), "Year of study") == O.UNKNOWN


@pytest.mark.parametrize("marks,expected", [(59.99, O.NOT_MET), (60, O.MET), (60.01, O.MET)])
def test_minimum_marks_boundary(marks, expected):
    assert outcome(run(StudentProfile(previous_percentage=marks), min_percentage=60), "Minimum marks") == expected


def test_cgpa_not_converted_to_percentage():
    r = run(StudentProfile(previous_cgpa=9.0), min_percentage=60)
    assert outcome(r, "Minimum marks") == O.UNKNOWN
    assert outcome(run(StudentProfile(previous_cgpa=7.0), min_cgpa=7.0), "Minimum CGPA") == O.MET
    assert outcome(run(StudentProfile(previous_cgpa=6.9), min_cgpa=7.0), "Minimum CGPA") == O.NOT_MET


# ---- institution
def test_institution_restriction(student):
    assert outcome(run(student, eligible_institutions=["RV College of Engineering, Bengaluru"]), "Institution") == O.MET
    assert outcome(run(student, eligible_institutions=["IIT Madras"]), "Institution") == O.NOT_MET
    assert outcome(run(StudentProfile(), eligible_institutions=["IIT Madras"]), "Institution") == O.UNKNOWN


def test_institution_type(student):
    student.institution_type = "Private"
    assert outcome(run(student, eligible_institution_types=["Government"]), "Institution type") == O.NOT_MET


# ---- gender, disability, fresh/renewal
def test_gender_only_when_scheme_requires_it(student):
    assert not any(c.criterion == "Gender" for c in run(student, max_family_income=500_000).criteria)
    assert outcome(run(student, eligible_genders=[Gender.FEMALE]), "Gender") == O.UNKNOWN
    student.gender = Gender.MALE
    assert outcome(run(student, eligible_genders=[Gender.FEMALE]), "Gender") == O.NOT_MET


def test_disability(student):
    assert outcome(run(student, requires_disability=True), "Disability") == O.UNKNOWN
    student.has_disability = False
    assert outcome(run(student, requires_disability=True), "Disability") == O.NOT_MET


def test_fresh_vs_renewal(student):
    assert outcome(run(student, accepts_fresh=False, accepts_renewal=True), "Fresh / renewal") == O.NOT_MET
    student.application_type = "Renewal"
    assert outcome(run(student, accepts_fresh=False, accepts_renewal=True), "Fresh / renewal") == O.MET
    assert outcome(run(StudentProfile(), accepts_renewal=False), "Fresh / renewal") == O.UNKNOWN


# ---- conflicts
def test_conflicting_sources_make_field_unknown(student):
    r = run(student, max_family_income=100_000,
            conflicts=[Conflict(field="max_family_income", values=["₹1 lakh", "₹2.5 lakh"])])
    assert outcome(r, "Family income") == O.UNKNOWN
    assert r.status == S.POTENTIAL_MATCH


# ---- unverified sources
def test_unofficial_source_is_unverified_even_if_criteria_fail(student):
    r = run(student, max_family_income=1, source_reliability=SourceReliability.UNVERIFIED)
    assert r.status == S.UNVERIFIED
    assert r.unmet  # criteria are still computed for display


def test_outdated_academic_year_is_unverified(student):
    assert run(student, max_family_income=300_000, academic_year="2024-25").status == S.UNVERIFIED


def test_stale_retrieval_is_unverified(student):
    old = NOW - timedelta(days=400)
    r = run(student, max_family_income=300_000, retrieved_at=old, last_verified_at=None)
    assert r.status == S.UNVERIFIED


def test_academic_year_rolls_over_in_june(student):
    s = make_scholarship(max_family_income=300_000, academic_year="2025-26")
    assert evaluate(student, s, date(2026, 5, 31), NOW).status != S.UNVERIFIED
    assert evaluate(student, s, date(2026, 6, 1), NOW).status == S.UNVERIFIED


# ---- application status / deadlines (independent of eligibility)
def test_deadline_boundaries():
    fresh = dict(retrieved_at=NOW, last_verified_at=NOW)
    on_day = make_scholarship(deadline=TODAY, **fresh)
    assert compute_application_status(on_day, TODAY, NOW)[0] == A.OPEN  # last day counts as open
    yesterday = make_scholarship(deadline=TODAY - timedelta(days=1), **fresh)
    assert compute_application_status(yesterday, TODAY, NOW)[0] == A.CLOSED


def test_open_requires_recent_verification():
    s = make_scholarship(deadline=TODAY + timedelta(days=30), last_verified_at=NOW - timedelta(days=60), retrieved_at=NOW - timedelta(days=60))
    assert compute_application_status(s, TODAY, NOW)[0] == A.UNKNOWN


def test_upcoming_and_no_deadline():
    s = make_scholarship(opening_date=TODAY + timedelta(days=5), deadline=TODAY + timedelta(days=40))
    assert compute_application_status(s, TODAY, NOW)[0] == A.UPCOMING
    assert compute_application_status(make_scholarship(), TODAY, NOW)[0] == A.UNKNOWN


def test_eligible_but_closed_stays_eligible(student):
    r = run(student, max_family_income=300_000, deadline=TODAY - timedelta(days=10),
            eligible_categories=[Category.OBC], eligible_states=["Karnataka"],
            eligible_education_levels=[EducationLevel.UNDERGRADUATE], min_percentage=50)
    assert r.status == S.ELIGIBLE
    assert r.application_status == A.CLOSED


def test_naive_datetimes_do_not_crash(student):
    naive = datetime(2026, 10, 27, 9, 0)
    r = run(student, max_family_income=300_000, retrieved_at=naive, last_verified_at=naive, deadline=TODAY + timedelta(days=5))
    assert r.application_status == A.OPEN


def test_criteria_carry_evidence(student):
    r = run(student, max_family_income=100_000, evidence=[ev("max_family_income", "income not above Rs 1 lakh")])
    assert r.criteria[0].evidence[0].quote.startswith("income not above")


# ---- income ranges (used when no exact income is given)
@pytest.mark.parametrize("lo,hi,expected", [
    (0, 100_000, O.MET), (100_001, 250_000, O.MET), (250_001, 500_000, O.NOT_MET),
    (100_001, 500_000, O.UNKNOWN), (800_001, None, O.NOT_MET),
])
def test_income_range(lo, hi, expected):
    r = run(StudentProfile(income_range_low=lo, income_range_high=hi), max_family_income=250_000)
    assert outcome(r, "Family income") == expected


def test_exact_income_wins_over_range():
    s = StudentProfile(annual_family_income=100_000, income_range_low=800_001)
    assert outcome(run(s, max_family_income=250_000), "Family income") == O.MET


def test_range_with_exclusive_limit():
    s = StudentProfile(income_range_low=250_000, income_range_high=250_000)
    assert outcome(run(s, max_family_income=250_000, income_limit_inclusive=False), "Family income") == O.NOT_MET
