"""Stages A-D: hard eligibility, structured scoring, caps on thin evidence, threshold, bounded and grounded stage D;
verification; the transport's retries, breaker, cache and budgets."""
import random

import pytest

import eligibility
from config import DEFAULT, Budget, Weights
from conftest import TODAY, no_sleep, run
from evaluator import LLMBudget, _Check, adjustment, evaluate, validate_checks
from fakes import ASHBY_CLOSED, CLOSED_TEXT, FakeModel, GH_APPLIED, NAUKRI, PAGES, PHD_TEXT, resume
from models import CandidateProfile, Listing, Requirements, Salary, SearchPrefs
from normalize import from_search, with_page
from profile import build_profile
from providers.catalog import build, classify
from resume import load_resume
from scoring import score, title_similarity
from transport import PageResult, Transport
from verify import verify

P = build()
POLICY = DEFAULT.policy


def prof():
    r = run(load_resume(resume()))
    return build_profile(r["text"], resume_hash=r["hash"], today=TODAY), r["text"]


def prefs(**kw):
    base = dict(target_roles=["AI Engineer"], country="India", country_code="IN", cities=["Pune"])
    return SearchPrefs(**{**base, **kw})


def posting(url=GH_APPLIED, title="Job Application for Applied AI Engineer at NimbusAI", page=None, **upd) -> Listing:
    lst, why = from_search({"title": title, "url": url, "snippet": "Pune, India"}, P, "q", TODAY)
    assert lst, why
    if page:
        lst = with_page(lst, page, None, TODAY)
    return lst.model_copy(update=upd) if upd else lst


def matched(lst, p=None, pr=None, sem=0.6):
    p = p or prof()[0]
    pr = pr or prefs()
    rej, con = eligibility.check(lst, p, pr, POLICY)
    return score(lst, p, pr, DEFAULT, related=["Applied AI Engineer", "LLM Engineer"], semantic=sem, rejections=rej, concerns=con)


# ---- stage A

def test_confirmed_conflicts_reject():
    p, _ = prof()
    base = posting(page=PAGES[GH_APPLIED])
    cases = {
        "location": base.model_copy(update={"location_country": "DE", "location_cities": ["Berlin"], "location": "Berlin"}),
        "work_mode": base,  # hybrid, but the person wants remote only
        "experience": base.model_copy(update={"requirements": base.requirements.model_copy(update={"min_years": 9})}),
        "employment_type": base.model_copy(update={"employment_type": "internship"}),
    }
    for reason, lst in cases.items():
        pr = prefs(work_mode="remote_only") if reason == "work_mode" else prefs()
        rej, _ = eligibility.check(lst, p, pr, POLICY)
        assert any(r.startswith(reason) for r in rej), (reason, rej)


def test_a_job_naming_another_city_too_is_not_rejected_on_location():
    p, _ = prof()
    two = posting(page=PAGES[GH_APPLIED]).model_copy(update={"location_country": "GB", "location_cities": ["London", "Pune"],
                                                             "location": "London or Pune"})
    rej, _ = eligibility.check(two, p, prefs(), POLICY)
    assert not any(r.startswith("location") for r in rej)


def test_remote_jobs_based_abroad_are_rejected_unless_open_to_the_country():
    p, _ = prof()
    base = posting(page=PAGES[GH_APPLIED]).model_copy(update={"work_mode": "remote", "remote_region": None})
    us = base.model_copy(update={"location_country": "US", "location_cities": ["New York"], "location": "New York",
                                 "description": "Remote role for our New York team.", "snippet": ""})
    rej, _ = eligibility.check(us, p, prefs(), POLICY)
    assert any(r.startswith("location: remote from United States") for r in rej)
    blurb = us.model_copy(update={"description": "Location: Ireland (Remote-First). We help companies build global teams anywhere."})
    assert any(r.startswith("location") for r in eligibility.check(blurb, p, prefs(), POLICY)[0])
    anywhere = us.model_copy(update={"description": "Fully remote, hiring worldwide."})
    assert not eligibility.check(anywhere, p, prefs(), POLICY)[0]
    india = us.model_copy(update={"description": "Location: Remote - India or US."})
    assert not eligibility.check(india, p, prefs(), POLICY)[0]
    unknown = base.model_copy(update={"location_country": None, "location_cities": [], "location": None,
                                      "description": "We are a remote team.", "snippet": ""})
    rej, con = eligibility.check(unknown, p, prefs(), POLICY)
    assert not rej and any("which countries" in c for c in con)


def test_unknowns_are_concerns_not_rejections():
    p, _ = prof()
    bare = posting().model_copy(update={"location_country": None, "location_cities": [], "location": None,
                                        "work_mode": "unknown", "requirements": Requirements()})
    rej, con = eligibility.check(bare, p, prefs(work_mode="remote_only", min_salary=1_500_000), POLICY)
    assert rej == [] and any("remote" in c for c in con) and any("salary" in c.lower() for c in con)


def test_semantically_close_but_missing_a_mandatory_degree_is_rejected():
    p, _ = prof()
    lst = posting("https://boards.greenhouse.io/visionary/jobs/5033333", "Job Application for AI Engineer (Computer Vision) at Visionary",
                  page={"title": "AI Engineer (Computer Vision)", "text": PHD_TEXT + " filler" * 80})
    m = matched(lst, p, sem=0.95)
    assert not m.eligible and any(r.startswith("qualification") for r in m.rejections)


def test_salary_floor_applies_only_with_same_currency_numbers():
    p, _ = prof()
    low = posting(page=PAGES[GH_APPLIED]).model_copy(update={"salary": Salary(min=600000, max=800000, currency="INR",
                                                                               period="year", text="₹6-8 LPA")})
    rej, _ = eligibility.check(low, p, prefs(min_salary=1_500_000), POLICY)
    assert any(r.startswith("salary") for r in rej)
    usd = low.model_copy(update={"salary": Salary(min=9e4, max=1e5, currency="USD", period="year", text="$90k-100k")})
    rej, con = eligibility.check(usd, p, prefs(min_salary=1_500_000), POLICY)
    assert not rej and any("USD" in c for c in con)


# ---- stage B and caps

def test_strong_match_passes_and_weak_one_does_not():
    good = matched(posting(page=PAGES[GH_APPLIED]))
    assert good.eligible and good.score >= 85 and good.confidence == "high" and not good.missing_skills
    other = posting("https://boards.greenhouse.io/acme/jobs/7777777", "Job Application for Senior Accountant at Acme",
                    page={"title": "Senior Accountant", "text": "Requirements:\n- 5+ years of accounting, Tally, GAAP\nApply for this job\n" + "x " * 300})
    weak = matched(other)
    assert weak.score < 60


def test_snippet_only_and_low_confidence_cannot_pass_the_threshold():
    from scoring import caps
    snippet_cap, low_cap = caps(DEFAULT)
    assert snippet_cap < POLICY.min_score and low_cap < POLICY.min_score   # whatever min_score is set to
    snippet_only = matched(posting())
    assert not posting().full_text and snippet_only.score <= snippet_cap
    m = matched(posting(page=PAGES[GH_APPLIED]), p=CandidateProfile())
    assert m.confidence == "low" and m.score <= low_cap


def test_requirements_under_preferred_only_are_the_requirements():
    import jd
    text = ("# AI Engineer\n\n- Build GenAI apps with LLMs and RAG\n\nPreferred Qualifications:\n\n"
            "- Experience with Python, Spark or SQL\n- Docker or Kubernetes\n" + "x " * 200)
    r = jd.parse(text, "AI Engineer")
    assert {"python", "llm", "rag"} <= set(r.required_skills)
    eeo = "All qualified applicants will be considered without regard to race, citizenship, age."
    assert jd.parse(eeo + "\n" + text, "AI Engineer").authorization == []


def test_project_only_skill_counts_less_than_job_experience():
    p, _ = prof()
    req = Requirements(required_skills=["kubernetes"], required_lines=["Kubernetes in production"])
    k8s = posting(page=PAGES[GH_APPLIED]).model_copy(update={"requirements": req})
    req2 = Requirements(required_skills=["fastapi"], required_lines=["FastAPI in production"])
    fast = posting(page=PAGES[GH_APPLIED]).model_copy(update={"requirements": req2})
    assert matched(k8s, p).breakdown["required_skills"] < matched(fast, p).breakdown["required_skills"]


def test_weights_are_configurable_and_validated():
    with pytest.raises(ValueError):
        Weights(required_skills=0.9)
    assert sum(DEFAULT.weights.as_dict().values()) == pytest.approx(1)


def test_role_similarity_distinguishes_families():
    assert title_similarity("Senior Data Engineer", "Data Analyst") < 0.4
    assert title_similarity("Gen AI Developer", "Generative AI Engineer") > 0.9


# ---- stage D

def test_stage_d_drops_ungrounded_claims_and_is_bounded():
    p, text = prof()
    lst = posting(page=PAGES[GH_APPLIED])
    checks = validate_checks([
        _Check(requirement="Experience building REST APIs with FastAPI", status="met",
               resume_quote="Built retrieval-augmented generation (RAG) services with LangChain and FastAPI"),
        _Check(requirement="Experience with LangChain, RAG and vector databases", status="met",
               resume_quote="Led a 20-person LLM research lab"),                         # not in the resume
        _Check(requirement="10 years of Rust", status="missing"),                         # not in the posting
    ], lst.description, text)
    assert [c["status"] for c in checks] == ["met", "unclear"]
    m = matched(lst, p)
    many = checks * 3
    assert abs(adjustment(many, m, POLICY.llm_adjust_max)) <= POLICY.llm_adjust_max


def test_stage_d_respects_the_call_budget_and_survives_failures():
    p, text = prof()
    lst = posting(page=PAGES[GH_APPLIED])
    m = matched(lst, p)
    budget = LLMBudget(1)
    model = FakeModel({"_Evals": RuntimeError("model down")})
    assert run(evaluate(model, [(lst, m)] * 8, p, text, budget, 8)) == {} and budget.calls == 1


# ---- verification

def test_verification_levels():
    gh = posting(page=PAGES[GH_APPLIED])
    ok = verify(gh, PageResult("ok", {"url": GH_APPLIED, **PAGES[GH_APPLIED]}, read_at=1.7e9), classify(GH_APPLIED, P), POLICY, TODAY)
    assert ok.status == "verified_active" and ok.checked_at and ok.application_url_checked
    closed = verify(posting(ASHBY_CLOSED, "Generative AI Engineer @ BrightLayer"),
                    PageResult("ok", {"url": ASHBY_CLOSED, "title": "x", "text": CLOSED_TEXT}, read_at=1.7e9),
                    classify(ASHBY_CLOSED, P), POLICY, TODAY)
    assert closed.status == "closed" and "no longer accepting" in closed.evidence[0]
    gone = verify(gh, PageResult("missing", error="404", read_at=1.7e9), classify(GH_APPLIED, P), POLICY, TODAY)
    assert gone.status == "closed"
    unread = verify(gh, None, classify(GH_APPLIED, P), POLICY, TODAY)
    assert unread.status == "unverified" and unread.checked_at is None and not unread.application_url_checked


def test_board_copy_is_at_most_likely_active_and_needs_a_recent_date():
    nk = posting(NAUKRI, "Python AI Developer - Cognisoft - Pune - Naukri.com", page=PAGES[NAUKRI])
    v = verify(nk, PageResult("ok", {"url": NAUKRI, **PAGES[NAUKRI]}, read_at=1.7e9), classify(NAUKRI, P), POLICY, TODAY)
    assert v.status == "likely_active"
    old = nk.model_copy(update={"posted_at": "2026-06-01"})
    assert verify(old, PageResult("ok", {"url": NAUKRI, **PAGES[NAUKRI]}, read_at=1.7e9), classify(NAUKRI, P), POLICY,
                  TODAY).status == "unverified"


def test_redirect_to_board_index_means_closed():
    gh = posting(page=PAGES[GH_APPLIED])
    page = PageResult("ok", {"url": "https://boards.greenhouse.io/nimbusai?error=true", "title": "Jobs at NimbusAI",
                             "text": "Applied AI Engineer\nCurrent openings at NimbusAI\n" + "x " * 100}, read_at=1.7e9)
    assert verify(gh, page, classify(GH_APPLIED, P), POLICY, TODAY).status == "closed"


# ---- transport

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_transient_errors_retry_with_backoff_then_succeed():
    attempts, sleeps = [], []

    async def read(source, **args):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("TOOL_FAILED: 429 Too Many Requests")
        return {"records": [{"title": "x", "url": "https://x.co/careers/123456"}]}

    async def sleep(s):
        sleeps.append(s)

    t = Transport(read, Budget(retries=2, min_interval=0), sleep=sleep, clock=Clock(), rng=random.Random(1))
    assert len(run(t.search("q"))) == 1 and len(attempts) == 3
    assert sleeps[1] > sleeps[0] > 0      # exponential, with jitter


def test_permanent_refusal_opens_the_circuit_and_names_it():
    async def read(source, **args):
        raise RuntimeError("CallFailed: sources.jobs: NEEDS_RECONNECT")

    t = Transport(read, Budget(min_interval=0), sleep=no_sleep, clock=Clock())
    assert run(t.search("a")) == [] and run(t.search("b")) is None
    assert t.health["jobs"].open and "NEEDS_RECONNECT" in t.report()["jobs"]["reason"]


def test_cache_and_budgets():
    calls = []

    async def read(source, **args):
        calls.append(args)
        return {"records": []}

    t = Transport(read, Budget(max_search_calls=2, max_page_calls=1, min_interval=0), sleep=no_sleep, clock=Clock())

    async def go():
        await t.search("AI  engineer")
        await t.search("ai engineer")          # same query: cached
        await t.search("ml engineer")
        assert await t.search("third") is None  # budget
        await t.page("https://x.co/careers/1")
        return await t.page("https://x.co/careers/2")

    assert run(go()).status == "skipped" and len([c for c in calls if "text" in c]) == 2


def test_a_search_with_no_results_is_empty_not_a_failure():
    async def read(source, **args):
        raise RuntimeError("TOOL_FAILED: SerpAPI error: DuckDuckGo hasn't returned any results for this query.")

    t = Transport(read, Budget(min_interval=0, breaker_threshold=1), sleep=no_sleep, clock=Clock())
    assert run(t.search("a")) == [] and run(t.search("b")) == [] and not t.health["jobs"].open


def test_jobs_asking_for_more_years_than_the_person_has_are_always_rejected():
    from models import Field_
    p, _ = prof()
    p = p.model_copy(update={"years_experience": Field_(value=1.0, provenance="user")})
    base = posting(page=PAGES[GH_APPLIED])
    # (min, max) asked -> rejected for a person with exactly 1 year ("at most my years")
    for (lo, hi), rejected in (((0, None), False), ((0, 1), False), ((0, 2), False), ((1, 1), False),
                               ((1, None), True), ((1, 2), True), ((1.5, None), True), ((2, None), True), ((5, 8), True)):
        lst = base.model_copy(update={"requirements": base.requirements.model_copy(update={"min_years": lo, "max_years": hi})})
        rej, _ = eligibility.check(lst, p, prefs(), POLICY)
        assert any(r.startswith("experience") for r in rej) is rejected, (lo, hi)
