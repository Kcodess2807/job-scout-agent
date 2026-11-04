"""Every tunable number of the job scout, in one place. Nothing here is a measured optimum: the weights and
thresholds are initial design parameters until they are calibrated on labelled data (tests/eval_dataset.json).

The engine takes a ScoutConfig, so a caller (the graph, a teammate's component, a test) can change any of these
without touching the matching or search code."""
from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Weights:
    """Stage B dimensions; they must sum to 1."""
    required_skills: float = 0.30
    experience: float = 0.20
    role: float = 0.20
    preferred: float = 0.10
    location: float = 0.10
    semantic: float = 0.10

    def as_dict(self) -> dict[str, float]:
        return {"required_skills": self.required_skills, "experience": self.experience, "role": self.role,
                "preferred": self.preferred, "location": self.location, "semantic": self.semantic}

    def __post_init__(self):
        total = sum(self.as_dict().values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1, got {total:.3f}")


@dataclass(frozen=True)
class Budget:
    """Hard limits on one search session. The controller stops at the first one reached; nothing loops unbounded."""
    max_seconds: float = 1320.0         # keep searching up to 22 min; the manifest gives the run 1500 s in all
    max_search_calls: int = 360         # web_result.search reads (each returns at most 10 results); a hosted run of
                                        # 140 searches spent them all in under 6 minutes, so time is the real limit
    max_page_calls: int = 420           # web_page.get reads (verification, full job text, LinkedIn closed checks)
    max_candidates: int = 4000          # normalised listings considered in one session
    max_rounds: int = 40                # search rounds (one expansion level may span several)
    max_llm_calls: int = 20             # requirement checks + stage-D evaluation + explanations
    max_llm_tokens: int = 2500          # max_tokens on each model call
    max_board_pages: int = 2            # ATS board index pages read to harvest postings (incremental retrieval)
    max_resolve: int = 80               # board finds looked up on the employer's own site (was 30: since board copies
                                        # need a 21-day date to count, the employer's page is how most of them get confirmed)
    max_rescue: int = 40                # of those, strong matches whose board copy couldn't be confirmed, looked up first
    max_similar: int = 20               # "more like this" searches from qualified jobs
    concurrency: int = 8                # reads in flight at once
    retries: int = 2                    # extra attempts on a transient failure
    backoff_base: float = 0.6           # seconds; doubled each attempt, plus jitter
    breaker_threshold: int = 3          # consecutive failures that open a source's circuit
    min_interval: float = 0.15          # seconds between two calls on one source (rate limit)
    embed_batch: int = 64


@dataclass(frozen=True)
class Policy:
    min_score: int = 70                 # a heuristic cut-off, not a probability of being hired
    strict_score: int = 90
    count_likely_active: bool = True    # likely-open postings (recent, on a job board) count toward the target too
    pool_factor: float = 1.2            # gather this many times `count` qualified jobs when budget allows, then rerank
    max_per_company: int = 2            # diversity in the final list (relaxed only to reach the count)
    snippet_only_cap: int = 72          # no full posting text read (always kept below min_score, see scoring.caps)
    low_confidence_cap: int = 84        # low evidence (always kept below min_score, see scoring.caps)
    llm_adjust_max: int = 8             # stage D may move a score by at most this many points
    borderline_below: int = 9           # stage D looks at scores within [min-9, min+4]
    borderline_above: int = 4
    experience_at_most: bool = True     # a job is kept only if its experience ask never goes above the person's
                                        # years: "1+" or "1-2" years is out for 1 year; "0-2" and freshers stay in
    experience_tolerance: float = 0.0   # years below a stated minimum still accepted (with a penalty)
    experience_tolerance_share: float = 0.0
    stale_after_days: int = 21          # a job board's copy (LinkedIn, Naukri, Indeed, ...) older than this is not counted as
                                        # likely active; a provider's own `fresh_days` wins. Was 60: a LinkedIn "2 months ago"
                                        # posting counted as open, and most of those had stopped taking applications
    careers_fresh_days: int = 30        # a careers page that shows the job but no apply control: older than this, unverified
    prefilter_min_title: float = 0.30   # title relevance needed before a posting is read in full


@dataclass(frozen=True)
class ScoutConfig:
    weights: Weights = field(default_factory=Weights)
    budget: Budget = field(default_factory=Budget)
    policy: Policy = field(default_factory=Policy)

    def with_threshold(self, score: int) -> "ScoutConfig":
        return replace(self, policy=replace(self.policy, min_score=int(score)))

    def counting_likely_active(self, on: bool) -> "ScoutConfig":
        return replace(self, policy=replace(self.policy, count_likely_active=bool(on)))


DEFAULT = ScoutConfig()

# Models, through GRAVITY_LLM_URL only (Gravity.agent.md section 7).
CHAT_MODEL = "openai/gpt-6-luna"
EMBED_MODEL = "voyageai/voyage-4-lite"
