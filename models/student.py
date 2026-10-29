"""Student profile models. Nothing here is an identifier: no name, Aadhaar, or bank details."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Category(str, Enum):
    GENERAL = "General"
    EWS = "EWS"
    SC = "SC"
    ST = "ST"
    OBC = "OBC"
    OTHER = "Other"


class EducationLevel(str, Enum):
    SCHOOL = "School"
    DIPLOMA = "Diploma"
    UNDERGRADUATE = "Undergraduate"
    POSTGRADUATE = "Postgraduate"
    PHD = "PhD"


class Gender(str, Enum):
    FEMALE = "Female"
    MALE = "Male"
    OTHER = "Other"


class ApplicationType(str, Enum):
    FRESH = "Fresh"
    RENEWAL = "Renewal"


class ScholarshipKind(str, Enum):
    """Search-preference tags: who offers it."""
    GOVERNMENT = "Government (central)"
    STATE_GOVERNMENT = "State government"
    PRIVATE_FOUNDATION = "Private foundation"
    CORPORATE = "Corporate"


class ScholarshipBasis(str, Enum):
    """Search-preference tags: how it is awarded."""
    MERIT = "Merit-based"
    NEED = "Need-based"
    RESEARCH = "Research"
    COURSE_OR_INSTITUTION = "Course / institution specific"


class SearchPreferences(BaseModel):
    kinds: list[ScholarshipKind] = Field(default_factory=list)  # empty = all
    bases: list[ScholarshipBasis] = Field(default_factory=list)  # empty = all
    open_only: bool = False
    min_amount: int | None = Field(default=None, ge=0)


class StudentProfile(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    state: str | None = None
    category: Category | None = None
    annual_family_income: int | None = Field(default=None, ge=0, description="INR per year")
    income_range_low: int | None = Field(default=None, ge=0, description="Used only when no exact income is given")
    income_range_high: int | None = Field(default=None, ge=0, description="None with a low bound means open-ended")
    gender: Gender | None = None
    has_disability: bool | None = None
    education_level: EducationLevel | None = None
    course: str | None = None
    current_year: int | None = Field(default=None, ge=1, le=10)
    institution_name: str | None = None
    institution_type: str | None = None
    previous_percentage: float | None = Field(default=None, ge=0, le=100)
    previous_cgpa: float | None = Field(default=None, ge=0, le=10, description="On a 10-point scale")
    application_type: ApplicationType | None = None
    preferences: SearchPreferences = Field(default_factory=SearchPreferences)

    def missing_fields(self) -> list[str]:
        """Human-readable names of eligibility inputs the student left empty."""
        labels = {
            "state": "State of domicile",
            "category": "Category",
            "annual_family_income": "Annual family income",
            "education_level": "Education level",
            "course": "Degree / course",
            "current_year": "Current academic year",
            "institution_name": "College / university",
            "previous_percentage": "Previous marks (percentage)",
            "application_type": "Fresh or renewal",
        }
        missing = [label for key, label in labels.items() if getattr(self, key) in (None, "")]
        if self.annual_family_income is None and self.income_range_low is not None and "Annual family income" in missing:
            missing.remove("Annual family income")
        if self.previous_cgpa is None and self.previous_percentage is None:
            missing.append("Previous marks (CGPA)")
        return missing
