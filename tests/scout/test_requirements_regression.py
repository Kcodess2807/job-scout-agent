"""Regressions from live runs: real postings (read 2026-11-01) whose experience, seniority or hiring location was
missed, plus the model requirement check that backs the parser up for phrasings not seen yet.

Every case in fixtures/real_postings.json was checked by hand against the live posting. If a parser change makes
one fail, a job the candidate does not qualify for would be shown again: fix the parser, never the expectation."""
import json
from pathlib import Path

import pytest

import eligibility
import jd
from config import DEFAULT
from conftest import TODAY, no_sleep, run
from engine import scout
from evaluator import LLMBudget, audit
from fakes import FakeModel, FakeWeb, payload_ids
from models import CandidateProfile, Field_, Listing, Requirements, SearchPrefs

CASES = json.loads((Path(__file__).parent / "fixtures" / "real_postings.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["url"].split("/")[2] + ":" + c["url"][-14:] for c in CASES])
def test_real_postings_are_read_correctly(case):
    r = jd.parse(case["text"], case["title"])
    exp = case["expect"]
    assert r.min_years == exp["min_years"], (case["url"], r.years_quote)
    if "stated_level" in exp:
        assert r.stated_level == exp["stated_level"]
    if "degree_required" in exp:
        assert r.degree_required == exp["degree_required"]


def one_year():
    return CandidateProfile(resume_present=True, years_experience=Field_(value=1.0, provenance="user"),
                            seniority=Field_(value="junior", provenance="inferred"))


def listing_of(case) -> Listing:
    url = case["url"]
    lst = Listing(key="k:" + url[-12:], title=case["title"], source="greenhouse", source_kind="ats", source_url=url,
                  canonical_url=url, description=case["text"], full_text=True, location_country="IN",
                  requirements=jd.parse(case["text"], case["title"]))
    return lst


@pytest.mark.parametrize("case", [c for c in CASES if (c["expect"]["min_years"] or 0) > 1 or c["expect"].get("stated_level") == 3],
                         ids=lambda c: c["url"][-20:])
def test_one_year_candidate_is_never_shown_jobs_above_their_level(case):
    prefs = SearchPrefs(target_roles=["AI Engineer"], country="India", country_code="IN")
    rej, _ = eligibility.check(listing_of(case), one_year(), prefs, DEFAULT.policy)
    assert any(r.startswith(("experience", "seniority")) for r in rej), case["url"]


# ---- the model requirement check (layer 2): grounded quotes, numbers read by code

POSTING = ("AI Engineer\nLocation: Pune\nWhat we want\n- You have shipped LLM products for three or more years "
           "(minimum 3 years) in a fast team.\n- Python, FastAPI\nApply for this job\n" + "About the team. " * 40)


def test_audit_keeps_only_quotes_that_are_in_the_posting():
    lst = Listing(key="a", title="AI Engineer", source="greenhouse", source_url="https://x.co/j/1",
                  canonical_url="https://x.co/j/1", description=POSTING, full_text=True)
    model = FakeModel({"_Audits": {"jobs": [{"id": "a", "experience_quote": "(minimum 3 years)",
                                             "degree_quote": "Master's degree required"}]}})   # invented: dropped
    found = run(audit(model, [lst], LLMBudget(5)))
    assert found["a"]["experience"] == "(minimum 3 years)" and found["a"]["degree"] is None
    req, notes = jd.apply_audit(Requirements(), found["a"])
    assert req.min_years == 3 and notes                       # code read the number from the quote


def test_years_written_in_words():
    for text, want in (("three-plus years building APIs", 3), ("five or more years of experience", 5),
                       ("two to four years of experience in ML", 2), ("minimum of seven years in industry", 7)):
        assert jd.years_required([text])[0] == want, text


def test_audit_cannot_invent_a_number():
    req, _ = jd.apply_audit(Requirements(), {"experience": "Strong experience with Python"})
    assert req.min_years is None


def _parser_blind_to(word):
    """The parser, made blind to one phrasing: stands for the next phrasing nobody has seen yet."""
    real = jd.years_required

    def blind(lines):
        if any(word in ln for ln in lines) and len(lines) > 1:
            lines = [ln for ln in lines if word not in ln]
        return real(lines)
    return blind


def test_engine_rechecks_unknown_requirements_before_counting(monkeypatch):
    """A posting the parser cannot read years from is checked by the model before it counts; a requirement
    found that way rejects it."""
    from fakes import GH_APPLIED, PAGES, RESULTS
    import copy
    pages = copy.deepcopy(PAGES)
    pages[GH_APPLIED]["text"] = pages[GH_APPLIED]["text"].replace(
        "- 2+ years of professional experience with Python",
        "- You have shipped Python services for three or more years (minimum 3 years)")
    # the parser's catch-all still reads "minimum 3 years"; hide it so only the re-check can find it
    pages[GH_APPLIED]["text"] = pages[GH_APPLIED]["text"].replace("(minimum 3 years)", "(three-plus yrs, at minimum)")
    monkeypatch.setattr(jd, "years_required", _parser_blind_to("three-plus"))

    def answer(schema, messages):
        ids = payload_ids(messages)
        return {"jobs": [{"id": i, "experience_quote": "three-plus yrs, at minimum" if "4012345" in i else None}
                         for i in ids]}

    model = FakeModel({"_Audits": answer, "_Evals": {"jobs": []}})
    prefs = SearchPrefs(target_roles=["AI Engineer"], country="India", country_code="IN", cities=["Pune"], count=10,
                        min_score=70)
    from resume import load_resume
    from profile import build_profile
    from fakes import resume
    r = run(load_resume(resume()))
    p = build_profile(r["text"], today=TODAY).model_copy(update={"years_experience": Field_(value=1.0, provenance="user"),
                                                                  "seniority": Field_(value="junior")})
    out = run(scout(p, prefs, read=FakeWeb(results=RESULTS, pages=pages), resume_text=r["text"], model=model,
                    today=TODAY, sleep=no_sleep))
    returned = [j for j in out.jobs if j.key not in out.near_misses]
    assert all("4012345" not in j.key for j in returned)
    assert "_Audits" in model.calls
