"""Typed contracts shared by every stage: the candidate profile, the search preferences, a normalised listing, its
verification and match, and the output contract. Graph state holds these as plain dicts (model_dump(mode="json"))
because the platform checkpoints state after every step; rebuild with Model.model_validate(d)."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Provenance = Literal["explicit", "inferred", "user", "missing", "conflict", "default"]


class Strength(str, Enum):
    """How well the resume shows a skill. Ordered strongest first; `value` of each is its matching weight."""
    demonstrated = "demonstrated"   # used in a job in the experience section
    listed = "listed"               # in the skills section only
    project = "project"             # used in a project only
    claimed = "claimed"             # in the summary/objective only
    inferred = "inferred"           # implied by another skill (FastAPI -> Python)


STRENGTH_WEIGHT = {Strength.demonstrated: 1.0, Strength.listed: 0.7, Strength.project: 0.65,
                   Strength.claimed: 0.5, Strength.inferred: 0.45}


class SkillEvidence(BaseModel):
    skill: str
    strength: Strength
    mentions: int = 1
    sections: list[str] = Field(default_factory=list)
    quote: str | None = None          # a short line of the resume showing it


class Field_(BaseModel):
    """A profile value with where it came from."""
    value: str | float | int | list[str] | None = None
    provenance: Provenance = "missing"
    note: str | None = None


class Role(BaseModel):
    title: str
    company: str | None = None
    start: str | None = None          # YYYY-MM
    end: str | None = None            # YYYY-MM or "present"
    months: int | None = None
    internship: bool = False


class CandidateProfile(BaseModel):
    """What the matcher needs about the person, and nothing else: no name, email, phone or address."""
    skills: list[SkillEvidence] = Field(default_factory=list)
    primary_skills: list[str] = Field(default_factory=list)
    secondary_skills: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)            # most recent first
    history: list[Role] = Field(default_factory=list)
    years_experience: Field_ = Field(default_factory=Field_)   # professional, internships excluded
    internship_months: int = 0
    seniority: Field_ = Field(default_factory=Field_)          # entry | junior | mid | senior | lead
    education: list[str] = Field(default_factory=list)
    highest_degree: str | None = None                          # none | diploma | bachelor | master | phd
    certifications: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    preferred_roles: list[str] = Field(default_factory=list)   # stated in the resume
    location: Field_ = Field(default_factory=Field_)
    evidence_lines: list[str] = Field(default_factory=list)    # experience/project lines for semantic matching
    resume_present: bool = False
    resume_hash: str | None = None
    notes: list[str] = Field(default_factory=list)             # what could not be read, conflicts

    def skill_map(self) -> dict[str, SkillEvidence]:
        return {s.skill: s for s in self.skills}

    def years(self) -> float | None:
        v = self.years_experience.value
        return float(v) if isinstance(v, (int, float)) else None


WorkMode = Literal["remote", "hybrid", "onsite", "unknown"]
EmploymentType = Literal["full_time", "part_time", "contract", "internship", "temporary", "unknown"]


class SearchPrefs(BaseModel):
    """The person's constraints (hard) and preferences (soft), normalised. Hard ones reject; soft ones rank."""
    target_roles: list[str]
    role_provenance: Provenance = "user"
    internships: bool = False
    country: str = ""
    country_code: str | None = None
    cities: list[str] = Field(default_factory=list)            # acceptable, most preferred first
    work_mode: Literal["either", "prefer_remote", "remote_only", "no_remote"] = "either"
    employment_type: Literal["any", "full_time", "part_time", "contract"] = "any"
    count: int = 10
    count_note: str | None = None
    min_salary: float | None = None                            # annual, in the country's currency
    min_score: int = 85
    include_likely_active: bool = True
    assumptions: list[str] = Field(default_factory=list)       # defaults applied, said to the person

    @field_validator("target_roles")
    @classmethod
    def _roles(cls, v: list[str]) -> list[str]:
        out = [" ".join(r.split()) for r in v if r and r.strip()]
        if not out:
            raise ValueError("at least one target role is needed")
        return out[:6]


class Salary(BaseModel):
    min: float | None = None
    max: float | None = None
    currency: str | None = None
    period: Literal["year", "month", "hour", "unknown"] = "unknown"
    text: str


class Requirements(BaseModel):
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    alternatives: list[list[str]] = Field(default_factory=list)  # "Power BI or Tableau": one requirement, either skill
    required_lines: list[str] = Field(default_factory=list)
    preferred_lines: list[str] = Field(default_factory=list)
    responsibility_lines: list[str] = Field(default_factory=list)
    min_years: float | None = None
    max_years: float | None = None
    years_quote: str | None = None
    stated_level: int | None = None         # 3 = the text asks for a senior/staff/lead person, 1 = "no new grads"
    degree_required: str | None = None      # bachelor | master | phd, only when stated as required
    degree_quote: str | None = None
    authorization: list[str] = Field(default_factory=list)  # e.g. "must be authorized to work in the US"
    domains: list[str] = Field(default_factory=list)


VerificationStatus = Literal["verified_active", "likely_active", "unverified", "closed"]


class Verification(BaseModel):
    status: VerificationStatus = "unverified"
    checked_at: str | None = None           # ISO time the page was read; None = never read
    evidence: list[str] = Field(default_factory=list)
    application_url_checked: bool = False


class Listing(BaseModel):
    """One job posting, in one schema whichever source found it."""
    key: str                                # stable id: provider:posting-id or a url hash
    title: str
    company: str | None = None
    company_domain: str | None = None
    location: str | None = None
    location_cities: list[str] = Field(default_factory=list)
    location_country: str | None = None
    remote_region: str | None = None        # "US" for "Remote (US only)"
    work_mode: WorkMode = "unknown"
    employment_type: EmploymentType = "unknown"
    salary: Salary | None = None
    posted_at: str | None = None            # ISO date, only when the source states it
    posted_text: str | None = None
    source: str                             # provider name
    source_kind: Literal["ats", "board", "careers", "aggregator"] = "board"
    authority: float = 0.5
    source_url: str
    canonical_url: str
    application_url: str | None = None
    also_seen_at: list[str] = Field(default_factory=list)
    snippet: str = ""
    description: str = ""                   # full text when a page read succeeded
    full_text: bool = False
    requirements: Requirements = Field(default_factory=Requirements)
    verification: Verification = Field(default_factory=Verification)
    found_by: str = ""                      # the query that found it
    closed_elsewhere: str | None = None     # another copy of this job (LinkedIn, a board, a search result) says
                                            # it no longer takes applications: then it is closed, whatever else says
    copy_checked: bool = False              # one LinkedIn/board copy was opened to look for a closed notice


class Match(BaseModel):
    eligible: bool = True
    rejections: list[str] = Field(default_factory=list)       # hard-constraint violations
    concerns: list[str] = Field(default_factory=list)         # unknowns, not rejections
    score: int = 0
    raw_score: float = 0.0
    breakdown: dict[str, float] = Field(default_factory=dict)
    cap: int | None = None
    cap_reason: str | None = None
    confidence: Literal["high", "medium", "low"] = "low"
    matched_skills: list[str] = Field(default_factory=list)
    transferable_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    matched_preferred: list[str] = Field(default_factory=list)
    experience_note: str | None = None
    llm_adjustment: int = 0
    llm_evidence: list[dict] = Field(default_factory=list)
    reason: str | None = None


# ---- the output contract (section 13 of the brief): what result["jobs"] holds, one per returned job

class ExperienceRequired(BaseModel):
    min_years: float | None = None
    max_years: float | None = None


class JobOut(BaseModel):
    rank: int
    job_id: str
    title: str
    company: str | None
    company_domain: str | None
    location: str | None
    work_mode: WorkMode
    employment_type: EmploymentType
    experience_required: ExperienceRequired | None
    salary: Salary | None
    required_skills: list[str]
    preferred_skills: list[str]
    matched_skills: list[str]
    transferable_skills: list[str]
    missing_skills: list[str]
    match_score: int
    score_breakdown: dict[str, float]
    match_confidence: Literal["high", "medium", "low"]
    match_reason: str
    eligibility_concerns: list[str]
    source: str
    source_url: str
    canonical_url: str
    application_url: str | None
    application_url_checked: bool
    also_seen_at: list[str]
    posted_at: str | None
    verified_at: str | None
    verification_status: VerificationStatus
    verification_evidence: list[str]
    new: bool = True


class SearchSummary(BaseModel):
    search_calls: int = 0
    page_calls: int = 0
    llm_calls: int = 0
    rounds: int = 0
    total_candidates_discovered: int = 0
    not_job_postings: int = 0
    duplicates_removed: int = 0
    inactive_jobs_rejected: int = 0
    unverified_not_counted: int = 0
    eligibility_rejections: int = 0
    below_threshold_rejections: int = 0
    prefilter_rejections: int = 0
    qualified: int = 0
    elapsed_seconds: float = 0.0
    rejected_before_reading: int = 0    # rejected from the search result alone, no page read spent
    skipped_known: int = 0              # closed or unfit in a recent run: not read again
    task_errors: int = 0
    channel_calls: dict[str, int] = Field(default_factory=dict)  # answer / similar / resolve searches
    stop_reason: str = ""
    rejection_reasons: dict[str, int] = Field(default_factory=dict)


SearchStatus = Literal["complete", "partial", "no_results", "cancelled", "failed"]


class SourceReport(BaseModel):
    source: str
    queries: int = 0
    results: int = 0
    postings: int = 0
    qualified: int = 0
    failures: int = 0
    status: Literal["ok", "degraded", "unavailable", "not_used"] = "not_used"
    reason: str | None = None


class ScoutOutcome(BaseModel):
    """What engine.scout returns; output.py turns it into `result` and `corpus`."""
    requested_count: int
    minimum_match_score: int
    search_status: SearchStatus
    jobs: list[Listing] = Field(default_factory=list)
    matches: dict[str, Match] = Field(default_factory=dict)   # listing key -> match
    near_misses: list[str] = Field(default_factory=list)      # keys of the best jobs that did not qualify
    searched_sources: list[str] = Field(default_factory=list)
    sources: list[SourceReport] = Field(default_factory=list)
    summary: SearchSummary = Field(default_factory=SearchSummary)
    warnings: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    remember: dict[str, dict] = Field(default_factory=dict)   # key -> {"why": closed|ineligible, "fp"}: for the next run
