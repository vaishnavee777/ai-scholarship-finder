"""Scholarship records with field-level evidence."""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

from models.student import Category, EducationLevel, Gender


class ProviderType(str, Enum):
    CENTRAL_GOVERNMENT = "Central government"
    STATE_GOVERNMENT = "State government"
    INSTITUTION = "College / university"
    PRIVATE_FOUNDATION = "Private foundation"
    CORPORATE = "Corporate"
    OTHER = "Other"
    UNKNOWN = "Unknown"


class SourceReliability(str, Enum):
    OFFICIAL_GOVERNMENT = "Official (government)"
    OFFICIAL_INSTITUTION = "Official (institution)"
    OFFICIAL_PROVIDER = "Official (provider, allow-listed)"
    THIRD_PARTY = "Third-party aggregator"
    UNVERIFIED = "Unverified source"
    DEMO = "Demo record"

    @property
    def is_authoritative(self) -> bool:
        return self in (
            SourceReliability.OFFICIAL_GOVERNMENT,
            SourceReliability.OFFICIAL_INSTITUTION,
            SourceReliability.OFFICIAL_PROVIDER,
        )


class ApplicationStatus(str, Enum):
    OPEN = "Open"
    UPCOMING = "Upcoming"
    CLOSED = "Closed"
    UNKNOWN = "Unknown"


class AwardCountScope(str, Enum):
    NATIONAL = "National"
    STATE = "State"
    CATEGORY = "Category"
    INSTITUTION = "Institution"
    PREVIOUS_YEAR = "Previous year's selections"
    UNKNOWN = "Unspecified"


class Evidence(BaseModel):
    """A verbatim quote supporting one extracted field."""
    field: str
    quote: str
    source_url: str
    retrieved_at: datetime | None = None


class Conflict(BaseModel):
    field: str
    values: list[str]
    source_urls: list[str] = Field(default_factory=list)


class Scholarship(BaseModel):
    id: str
    name: str
    provider: str | None = None
    provider_type: ProviderType = ProviderType.UNKNOWN
    description: str | None = None
    academic_year: str | None = None

    eligible_courses: list[str] = Field(default_factory=list)
    eligible_education_levels: list[EducationLevel] = Field(default_factory=list)
    eligible_years: list[int] = Field(default_factory=list)
    eligible_categories: list[Category] = Field(default_factory=list)
    min_percentage: float | None = None
    min_cgpa: float | None = None
    max_family_income: int | None = None  # INR per year
    income_limit_inclusive: bool = True  # "up to" (True) vs "less than" (False)
    eligible_states: list[str] = Field(default_factory=list)
    eligible_institutions: list[str] = Field(default_factory=list)
    eligible_institution_types: list[str] = Field(default_factory=list)
    eligible_genders: list[Gender] = Field(default_factory=list)
    requires_disability: bool | None = None
    accepts_fresh: bool | None = None
    accepts_renewal: bool | None = None

    award_amount: int | None = None
    award_amount_text: str | None = None
    currency: str = "INR"
    award_count: int | None = None
    award_count_scope: AwardCountScope = AwardCountScope.UNKNOWN

    opening_date: date | None = None
    deadline: date | None = None
    application_status: ApplicationStatus = ApplicationStatus.UNKNOWN

    required_documents: list[str] = Field(default_factory=list)
    application_url: str | None = None
    guideline_url: str | None = None
    tags: list[str] = Field(default_factory=list)  # e.g. "merit", "need", "research"

    evidence: list[Evidence] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    source_reliability: SourceReliability = SourceReliability.UNVERIFIED
    retrieved_at: datetime | None = None
    last_verified_at: datetime | None = None
    extraction_confidence: float = Field(default=0.0, ge=0, le=1)
    unresolved_fields: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    is_demo: bool = False

    def evidence_for(self, field: str) -> list[Evidence]:
        return [e for e in self.evidence if e.field == field]

    def has_conflict(self, field: str) -> bool:
        return any(c.field == field for c in self.conflicts)

    @property
    def primary_source_url(self) -> str | None:
        return self.guideline_url or self.application_url or (self.source_urls[0] if self.source_urls else None)


def make_scholarship_id(name: str, provider: str | None, academic_year: str | None) -> str:
    """Stable id so a re-crawl of the same scheme updates the same record."""
    key = "|".join((s or "").strip().lower() for s in (name, provider, academic_year))
    return hashlib.sha256(key.encode()).hexdigest()[:16]
