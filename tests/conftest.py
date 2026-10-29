from datetime import date, datetime, timezone

import pytest

from models.scholarship import Evidence, Scholarship, SourceReliability
from models.student import StudentProfile

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 28)


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def today():
    return TODAY


def make_scholarship(**kw) -> Scholarship:
    """An official, fresh, current-year record with no criteria unless supplied."""
    base = dict(
        id="s1", name="Test Scholarship", provider="Ministry of Test",
        academic_year="2026-27", source_reliability=SourceReliability.OFFICIAL_GOVERNMENT,
        retrieved_at=NOW, last_verified_at=NOW, source_urls=["https://scholarships.gov.in/x"],
        guideline_url="https://scholarships.gov.in/x",
    )
    base.update(kw)
    return Scholarship(**base)


def ev(field: str, quote: str = "quote") -> Evidence:
    return Evidence(field=field, quote=quote, source_url="https://scholarships.gov.in/x", retrieved_at=NOW)


@pytest.fixture
def student() -> StudentProfile:
    return StudentProfile(
        state="Karnataka", category="OBC", annual_family_income=200000,
        education_level="Undergraduate", course="B.E.", current_year=2,
        institution_name="RV College of Engineering", previous_percentage=75.0,
        application_type="Fresh",
    )
