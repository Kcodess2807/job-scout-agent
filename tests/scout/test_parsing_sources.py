"""Job text parsing, provider URL classification, normalisation into one schema, query planning, deduplication."""
import datetime as dt

import pytest

import jd
from conftest import TODAY, run
from dedupe import dedupe_all, same_opening
from fakes import (APPLIED_TEXT, CAREERS, GH_APPLIED, LEVER_LLM, LI_APPLIED, NAUKRI, PAGES, resume)
from models import CandidateProfile, SearchPrefs
from normalize import from_search, title_company, with_page
from planner import build_plan, related_roles, seniority_variants
from profile import build_profile
from providers.catalog import build, classify
from resume import load_resume
from security import safe_url

P = build()


def listing(url, title, snippet=""):
    lst, why = from_search({"title": title, "url": url, "snippet": snippet}, P, "q", TODAY)
    assert lst, why
    return lst


# ---- job text

def test_requirements_are_split_from_nice_to_haves_and_about_us():
    r = jd.parse(APPLIED_TEXT, "Applied AI Engineer")
    assert {"python", "langchain", "rag", "fastapi", "docker", "aws"} <= set(r.required_skills)
    assert "kubernetes" in r.preferred_skills and "kubernetes" not in r.required_skills
    assert r.min_years == 2 and r.max_years is None and "2+ years" in r.years_quote


def test_years_ranges_and_freshers():
    assert jd.years_required(["3-5 years of experience in data engineering"])[:2] == (3, 5)
    assert jd.years_required(["Minimum of 4 years of professional experience"])[:2] == (4, None)
    assert jd.years_required(["Freshers welcome; no experience required"])[:2] == (0, 1)
    assert jd.years_required(["We have offices in 3 countries"])[:2] == (None, None)


def test_work_mode_type_and_region():
    assert jd.work_mode("Workplace type: Hybrid") == "hybrid"
    assert jd.work_mode("", "Backend Engineer (Remote)") == "remote"
    assert jd.work_mode("This is not a remote role. Work from office in Pune.") == "onsite"
    assert jd.remote_region("Remote (US only)\n") == "US"
    assert jd.work_mode("Great team. Benefits include remote Fridays and lunch.") == "unknown"
    assert jd.work_mode("This is a fully remote position open across India.") == "remote"
    assert jd.employment_type("", "Data Science Intern") == "internship"
    assert jd.employment_type("Employment type: Contract") == "contract"


def test_salary_is_parsed_only_when_stated():
    s = jd.salary("CTC: ₹12 - 18 LPA")
    assert (s.min, s.max, s.currency, s.period) == (1_200_000, 1_800_000, "INR", "year")
    u = jd.salary("Pay: $120,000 - $150,000 per year")
    assert (u.min, u.max, u.currency) == (120_000, 150_000, "USD")
    assert jd.salary("Competitive salary and benefits") is None


def test_posting_dates_and_closed_wording():
    assert jd.posted("Posted 3 days ago", TODAY)[0] == "2026-10-29"
    assert jd.posted("Date posted: 2026-10-20", TODAY)[0] == "2026-10-20"
    assert jd.posted("Join our team", TODAY) == (None, None)
    assert jd.closed_signal("Sorry! This job has expired.") == "this job has expired"
    assert jd.valid_through('"validThrough": "2026-09-30"') == dt.date(2026, 9, 30)


# ---- providers and normalisation

@pytest.mark.parametrize("url,provider,posting", [
    (GH_APPLIED, "greenhouse", True), ("https://boards.greenhouse.io/nimbusai", "greenhouse", False),
    (LEVER_LLM, "lever", True), (LI_APPLIED, "linkedin", True),
    ("https://www.linkedin.com/jobs/search/?keywords=ai", "linkedin", False), (NAUKRI, "naukri", True),
    ("https://in.indeed.com/viewjob?jk=0123456789abcdef&from=serp", "indeed", True),
    ("https://acme.wd5.myworkdayjobs.com/en-US/External/job/Pune/AI-Engineer_R12345", "workday", True),
    (CAREERS, "careers", True), ("https://www.acme.com/careers", "careers", False),
])
def test_provider_classification(url, provider, posting):
    p = classify(url, P)
    assert p.name == provider and p.is_posting(url) is posting


def test_not_job_sites_and_unsafe_urls_are_dropped():
    assert classify("https://medium.com/@x/ai-jobs", P) is None
    for bad in ("http://localhost/jobs/1", "http://10.0.0.5/jobs/1", "file:///etc/passwd", "https://user:pw@x.com/j/1",
                "https://metadata.google.internal/x", "javascript:alert(1)"):
        assert safe_url(bad) is None
    lst, why = from_search({"title": "x", "url": "http://127.0.0.1/careers/123456"}, P, "q", TODAY)
    assert lst is None


def test_company_and_title_from_each_source_shape():
    assert title_company("Job Application for AI Engineer at NimbusAI", "Nimbusai") == ("AI Engineer", "NimbusAI")
    assert title_company("NimbusAI hiring Applied AI Engineer in Pune, Maharashtra, India | LinkedIn", None) == \
        ("Applied AI Engineer", "NimbusAI")
    assert title_company("QuantumLeaf - LLM Engineer", "Quantumleaf") == ("LLM Engineer", "QuantumLeaf")
    assert title_company("AI Backend Engineer | Zentrix Careers", "Zentrix") == ("AI Backend Engineer", "Zentrix")


def test_listing_from_search_and_page_is_one_schema():
    lst = listing(LI_APPLIED, "NimbusAI hiring Applied AI Engineer in Pune, Maharashtra, India | LinkedIn")
    assert lst.key == "linkedin:3998877665" and lst.canonical_url.endswith("3998877665") and "trk" not in lst.canonical_url
    assert lst.company == "NimbusAI" and "Pune" in lst.location_cities and lst.location_country == "IN"
    gh = with_page(listing(GH_APPLIED, "Job Application for Applied AI Engineer at NimbusAI"), PAGES[GH_APPLIED], None, TODAY)
    assert gh.full_text and gh.work_mode == "hybrid" and gh.employment_type == "full_time" and gh.requirements.min_years == 2


# ---- planning

def profile():
    r = run(load_resume(resume()))
    return build_profile(r["text"], resume_hash=r["hash"], today=TODAY)


def test_related_roles_are_backed_by_skills_and_stay_in_family():
    prefs = SearchPrefs(target_roles=["AI Engineer"], country="India", country_code="IN")
    rel = related_roles(prefs, profile())
    assert "Generative AI Engineer" in rel or "LLM Engineer" in rel
    assert not any(r in rel for r in ("Data Analyst", "Frontend Engineer", "DevOps Engineer"))
    thin = CandidateProfile(resume_present=True)
    assert all(r == "AI Engineer" or "AI" in r for r in related_roles(prefs, thin))  # no skills: only ungated titles


def test_seniority_variants_and_queries_are_unique_and_source_aware():
    p = profile()
    prefs = SearchPrefs(target_roles=["AI Engineer"], country="India", country_code="IN", cities=["Pune"])
    plan = build_plan(prefs, p, build())
    queries = [i.query for i in plan.items]
    assert len(queries) == len(set(q.lower() for q in queries))
    assert any("site:job-boards.greenhouse.io" in q and "Pune" in q for q in queries)
    assert any(i.provider == "naukri" for i in plan.items)          # India-only board, used for India
    assert plan.items[0].level == 0 and plan.items[-1].level >= 2
    us = SearchPrefs(target_roles=["AI Engineer"], country="United States", country_code="US")
    assert not any(i.provider == "naukri" for i in build_plan(us, p, build()).items)
    junior = CandidateProfile(resume_present=True, years_experience={"value": 0.5, "provenance": "explicit"})
    assert seniority_variants(prefs, junior) == ["Junior AI Engineer", "Associate AI Engineer", "AI Engineer fresher"]


def test_remote_only_searches_remote_places():
    prefs = SearchPrefs(target_roles=["AI Engineer"], country="India", country_code="IN", work_mode="remote_only")
    plan = build_plan(prefs, profile(), build())
    assert all("remote" in i.where for i in plan.items)


# ---- dedupe

def test_board_copy_merges_into_the_employers_posting():
    gh = with_page(listing(GH_APPLIED, "Job Application for Applied AI Engineer at NimbusAI", "Pune, India"),
                   PAGES[GH_APPLIED], None, TODAY)
    li = listing(LI_APPLIED, "NimbusAI hiring Applied AI Engineer in Pune, Maharashtra, India | LinkedIn")
    kept, dups = dedupe_all([li, gh])
    assert dups == 1 and len(kept) == 1 and kept[0].source == "greenhouse" and LI_APPLIED in kept[0].also_seen_at


def test_same_title_different_openings_are_kept_apart():
    a = listing("https://boards.greenhouse.io/acme/jobs/1111111", "Job Application for AI Engineer at Acme", "Pune")
    b = listing("https://boards.greenhouse.io/acme/jobs/2222222", "Job Application for AI Engineer at Acme", "Pune")
    c = listing("https://boards.greenhouse.io/acme/jobs/3333333", "Job Application for AI Engineer at Acme", "Bengaluru")
    assert not same_opening(a, b) and dedupe_all([a, b, c])[1] == 0


def test_careers_page_with_greenhouse_id_is_the_same_job():
    gh = listing("https://boards.greenhouse.io/acme/jobs/4444444", "Job Application for AI Engineer at Acme")
    site = listing("https://www.acme.com/careers/open-roles?gh_jid=4444444", "AI Engineer | Acme")
    assert gh.key == site.key == "greenhouse:4444444" and dedupe_all([site, gh])[1] == 1


def test_years_and_seniority_in_real_posting_phrasings():
    cases = {
        "**About you:**\n- 6+ years of experience\n- BA/BS in computer science": 6,
        "Experience\n**4+ years**\n- Strong backend skills in Python": 4,
        "Requirements\n- 2+ years of Python and SQL in production": 2,
        "Requirements\n- 2+ years in ML/NLP/LLM systems, or 3+ years in software engineering": 2,
        "Requirements\n- Experience: 1-2 Years\n- Python": 1,
        "We were founded 12 years ago.\nRequirements\n- Python and LLMs": None,
    }
    for text, want in cases.items():
        assert jd.parse(text + "\n" + "x " * 50, "AI Engineer").min_years == want, text
    senior = jd.parse("We're looking for a product-minded senior software engineer. We're currently not looking for new grads.", "AI Engineer")
    assert senior.stated_level == 3 and senior.min_years is None
    assert jd.parse("Location: Pune. We are not looking for new grads.", "AI Engineer").stated_level == 1


def test_job_copy_sites_are_never_taken_for_an_employer():
    for title, url in (("Zensar Technologies is hiring for AI Engineer", "https://jobfound.org/job/zensar-technologies-is-hiring-for-ai-engineer-pune"),
                       ("AI Engineer (LLM) Jobs in Bengaluru", "https://myinternships.in/jobs/captain-fresh-ai-engineer-llm-bengaluru-18e8c8"),
                       ("AI Engineer Job 2026 | Some Site", "https://somecopysite.com/jobs/acme-ai-engineer-123456")):
        lst, _ = from_search({"title": title, "url": url, "snippet": ""}, P, "q", TODAY)
        assert lst is None or lst.source_kind == "aggregator", url
    real = listing("https://careers.zentrix.ai/jobs/ai-backend-engineer-2231", "AI Backend Engineer | Zentrix Careers")
    assert real.source_kind == "careers"


def test_copy_services_on_an_ats_are_not_employers():
    lst, _ = from_search({"title": "Jobgether - AI Agent Observability Engineer",
                          "url": "https://jobs.lever.co/jobgether/1a70c265-f9d8-4089-9896-52f1e6345cbd"}, P, "q", TODAY)
    assert lst is not None and lst.source_kind == "aggregator"


def test_careers_pages_that_list_another_companys_job_are_copies():
    from normalize import copy_of_another_employer
    assert copy_of_another_employer("Aimates", "AI Engineer at Capco | AIMates Jobs",
                                    "Official employer application. Continue to the official Capco careers page")
    assert copy_of_another_employer("Jobaaj", "Apply to Machine Learning Engineer Job at Bengaluru / Bangalore, "
                                    "Karnataka location in Capco, required 0 to 1 years of experience",
                                    "An AI agent that applies to roles like this on employers' own sites.")
    # real employers name only themselves (or a place)
    assert not copy_of_another_employer("Vodafone", "AI / Generative AI Engineer - VOIS Job Details | Vodafone",
                                        "At Vodafone, we're working hard to build a better future.")
    assert not copy_of_another_employer("Zunderdog", "AI & Automation Developer (Fresher), Pune | Zunderdog",
                                        "We build those AI agents and automations.")
    assert not copy_of_another_employer("Zentrix", "AI Backend Engineer at Zentrix", "Location: Pune, India")
    assert not copy_of_another_employer("Msci", "AI Engineer in Pune", "")
    lst, _ = from_search({"title": "AI Engineer at Capco | AIMates Jobs",
                          "url": "https://www.aimates.org/jobs/capco-ai-engineer-7625868"}, P, "q", TODAY)
    assert lst.source_kind == "aggregator"
