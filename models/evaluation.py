"""Eligibility evaluation, red flags, Gemini summary and ranked results."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from models.scholarship import ApplicationStatus, Evidence, Scholarship


class EligibilityStatus(str, Enum):
    ELIGIBLE = "Eligible"
    POTENTIAL_MATCH = "Potential match"
    LIKELY_INELIGIBLE = "Likely ineligible"
    UNVERIFIED = "Unverified"


class CriterionOutcome(str, Enum):
    MET = "met"
    NOT_MET = "not_met"
    UNKNOWN = "unknown"


class CriterionResult(BaseModel):
    criterion: str
    outcome: CriterionOutcome
    requirement: str
    student_value: str | None = None
    detail: str
    evidence: list[Evidence] = Field(default_factory=list)


class EligibilityEvaluation(BaseModel):
    scholarship_id: str
    status: EligibilityStatus
    explanation: str
    criteria: list[CriterionResult] = Field(default_factory=list)
    application_status: ApplicationStatus = ApplicationStatus.UNKNOWN
    application_note: str = ""
    evaluated_at: datetime | None = None

    def by_outcome(self, outcome: CriterionOutcome) -> list[CriterionResult]:
        return [c for c in self.criteria if c.outcome == outcome]

    @property
    def met(self) -> list[CriterionResult]:
        return self.by_outcome(CriterionOutcome.MET)

    @property
    def unmet(self) -> list[CriterionResult]:
        return self.by_outcome(CriterionOutcome.NOT_MET)

    @property
    def unknown(self) -> list[CriterionResult]:
        return self.by_outcome(CriterionOutcome.UNKNOWN)


class RedFlagType(str, Enum):
    STRICT_INCOME_LIMIT = "Income limit"
    LIMITED_AWARDS = "Limited awards"
    AWARD_COUNT_UNVERIFIED = "Award count not verified"
    INSTITUTION_SPECIFIC = "Institution-specific"
    STATE_RESTRICTION = "State / domicile restriction"
    CATEGORY_SPECIFIC = "Category-specific"
    MINIMUM_MARKS = "Minimum marks"
    FRESH_ONLY = "Fresh applications only"
    RENEWAL_REQUIREMENT = "Renewal requirement"
    DEADLINE_APPROACHING = "Deadline approaching"
    DEADLINE_PASSED = "Deadline passed"
    MISSING_DOCUMENTS = "Documents not listed"
    CONFLICTING_INFO = "Conflicting information"
    OUTDATED_GUIDELINES = "Outdated guidelines"
    UNVERIFIED_PROVIDER = "Unverified provider"
    UNCLEAR_AMOUNT = "Unclear award amount"
    STALE_VERIFICATION = "Status not recently verified"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "critical": 2}[self.value]


class RedFlag(BaseModel):
    type: RedFlagType
    severity: Severity
    explanation: str
    evidence: str | None = None
    source_url: str | None = None
    confirmed: bool = Field(description="True if stated by a source; False if an inference or an absence of information")


class GeminiSummaryOutput(BaseModel):
    """Schema requested from Gemini. All content must come from the supplied data."""
    summary: str
    eligibility_explanation: str
    confirmed_facts: list[str] = Field(default_factory=list)
    interpretations: list[str] = Field(default_factory=list)
    matching_criteria: list[str] = Field(default_factory=list)
    unmet_criteria: list[str] = Field(default_factory=list)
    unknown_criteria: list[str] = Field(default_factory=list)
    important_restrictions: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)
    application_instructions: list[str] = Field(default_factory=list)
    red_flag_notes: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    evidence_insufficient: bool = False


class GeminiSummary(GeminiSummaryOutput):
    """Validated summary as stored / shown. grounding_warnings is computed locally, never by Gemini."""
    grounding_warnings: list[str] = Field(default_factory=list)


class RankedResult(BaseModel):
    scholarship: Scholarship
    evaluation: EligibilityEvaluation
    red_flags: list[RedFlag] = Field(default_factory=list)
    score: float = 0.0
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
