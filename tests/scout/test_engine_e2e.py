"""End-to-end over the fake web: the twelve scenarios of the brief, plus the never-fabricate invariants."""
from dataclasses import replace

from clarify import decide
from config import DEFAULT
from conftest import TODAY, no_sleep, run
from engine import CancelToken, scout
from fakes import ASHBY_CLOSED, GH_PHD, LI_APPLIED, PAGES, RESULTS, FakeWeb, resume
from models import CandidateProfile
from prefs import read_inputs
from profile import build_profile
from resume import load_resume

FRESHER = """Aarav Mehta
Aspiring AI Engineer
Pune, India
EDUCATION
B.Tech in Computer Science, MIT World Peace University, 2022 - 2026
Final year student
PROJECTS
- Built a RAG chatbot with LangChain, FastAPI and pgvector
- Fine-tuned a small model with Hugging Face for sentiment analysis
SKILLS
Python, SQL, LangChain, FastAPI, Docker, Git
""" + "Coursework: machine learning, data structures and algorithms, databases.\n" * 4

SENIOR = resume().replace("Jan 2024 - Present", "Jan 2015 - Present").replace("Jul 2022 - Dec 2023", "Jul 2012 - Dec 2014")


def setup(inputs, text=None, ask=None):
    text = resume() if text is None else text
    r = run(load_resume(text))
    p = build_profile(r["text"], resume_hash=r["hash"], today=TODAY)
    prefs, p, info = decide(read_inputs(inputs), p, ask=ask)
    return prefs, p, r["text"]


def go(inputs, web=None, text=None, config=DEFAULT, **kw):
    prefs, p, rtext = setup(inputs, text)
    web = web or FakeWeb()
    out = run(scout(p, prefs, read=web, resume_text=rtext, today=TODAY, sleep=no_sleep, config=config, **kw))
    return out, web, prefs


def returned(out):
    return [j for j in out.jobs if j.key not in out.near_misses]


KNOWN_URLS = {r["url"] for _, r in RESULTS} | set(PAGES)


def assert_honest(out):
    """Nothing fabricated: every job came from the fake web, passed the threshold, and is verified (or, by default,
    likely) open."""
    for j in returned(out):
        m = out.matches[j.key]
        assert j.source_url in KNOWN_URLS or j.source_url.split("?")[0] in KNOWN_URLS
        assert m.eligible and m.score >= out.minimum_match_score and j.verification.status in ("verified_active", "likely_active")
        assert j.verification.checked_at and j.application_url
    assert len(returned(out)) <= out.requested_count


BASE = {"role": "AI Engineer", "country": "India", "city": "Pune", "remote": "either"}
VERIFIED_ONLY = {**BASE, "include_likely_active": False}   # the older default, kept for the "verified only" checks


def test_1_complete_inputs_return_ranked_verified_jobs():
    out, web, _ = go({**BASE, "count": 3})
    jobs = returned(out)
    assert out.search_status == "complete" and len(jobs) == 3
    scores = [out.matches[j.key].score for j in jobs]
    assert scores == sorted(scores, reverse=True)
    assert_honest(out)
    assert out.summary.stop_reason == "target_met"


def test_2_missing_role_and_location_are_filled_from_the_resume():
    out, _, prefs = go({"country": "India"})
    assert prefs.role_provenance == "inferred" and prefs.cities == ["Pune"]
    assert returned(out) and all(j.location_country in (None, "IN") for j in returned(out))
    assert_honest(out)


def test_3_no_experience_candidate_gets_no_jobs_beyond_reach():
    out, _, prefs = go({**BASE, "count": 5}, text=FRESHER)
    years = 0.0
    for j in returned(out):
        assert (j.requirements.min_years or 0) - years <= 1.0
    rejected = out.summary.rejection_reasons
    assert rejected.get("experience", 0) + rejected.get("seniority", 0) >= 1


def test_4_several_years_of_experience_unlocks_senior_titles():
    prefs, p, _ = setup(BASE, text=SENIOR)
    assert p.years() >= 10 and p.seniority.value in ("senior", "lead")
    out, _, _ = go(BASE, text=SENIOR)
    assert "seniority" not in out.summary.rejection_reasons   # the staff title is not out of reach any more


def test_5_remote_only_returns_no_onsite_or_hybrid_jobs():
    out, _, _ = go({**BASE, "remote": "remote"})
    jobs = returned(out)
    assert jobs and all(j.work_mode == "remote" for j in jobs)
    assert out.summary.rejection_reasons.get("work_mode", 0) >= 1


def test_6_ten_requested_four_qualify_returns_four_and_says_why():
    out, _, _ = go({**VERIFIED_ONLY, "count": 10})
    assert out.search_status == "partial" and len(returned(out)) == 4
    assert any("Found 4 of 10" in w and "Nothing was lowered" in w for w in out.warnings)
    assert_honest(out)


def test_7_the_same_job_on_two_sources_is_one_result():
    out, _, _ = go({**BASE, "count": 10})
    nimbus = [j for j in out.jobs if "NimbusAI" in (j.company or "")]
    assert len(nimbus) == 1 and nimbus[0].source == "greenhouse" and LI_APPLIED in nimbus[0].also_seen_at
    assert out.summary.duplicates_removed >= 1


def test_8_expired_jobs_are_rejected_not_returned():
    out, _, _ = go({**BASE, "count": 10})
    assert out.summary.inactive_jobs_rejected >= 1
    assert all(j.source_url != ASHBY_CLOSED for j in out.jobs)


def test_9a_page_reader_unavailable_degrades_honestly():
    out, web, _ = go({**BASE, "count": 5}, web=FakeWeb(fail_pages="CallFailed: sources.pages: NEEDS_RECONNECT"))
    assert returned(out) == [] and out.search_status == "no_results"
    assert any("could not be opened" in w for w in out.warnings)
    assert any(s.status == "unavailable" for s in out.sources)
    assert web.count("pages") <= 3   # the circuit opened; it stopped trying


def test_9b_search_source_down_fails_without_crashing():
    out, web, _ = go(BASE, web=FakeWeb(fail_search="CallFailed: sources.jobs: TOOL_FAILED 503 unavailable"))
    assert out.search_status == "failed" and out.jobs == []
    assert out.summary.stop_reason == "search_source_unavailable"
    assert web.count("jobs") <= 3 * (DEFAULT.budget.retries + 1) + DEFAULT.budget.concurrency * 2 * 3


def test_10_semantically_similar_but_missing_a_mandatory_requirement():
    out, _, _ = go({**BASE, "count": 10})
    assert all(j.source_url != GH_PHD for j in returned(out))
    assert out.summary.rejection_reasons.get("qualification", 0) == 1


def test_11_cancellation_stops_the_search_and_keeps_what_it_has():
    token = CancelToken()

    def cancel_after_three(web):
        if len(web.calls) >= 3:
            token.cancel()

    web = FakeWeb(on_call=cancel_after_three)
    out, _, _ = go({**BASE, "count": 10}, web=web, cancel=token)
    assert out.search_status == "cancelled" and out.summary.stop_reason == "cancelled"
    # nothing new starts after the cancel: at most the calls already in flight (one pool) finish
    assert len(web.calls) <= 3 + DEFAULT.budget.concurrency


def test_12a_time_budget_is_enforced():
    t = {"now": 0.0}

    def clock():
        t["now"] += 40.0      # every check costs 40 s
        return t["now"]

    prefs, p, rtext = setup({**BASE, "count": 10})
    out = run(scout(p, prefs, read=FakeWeb(), resume_text=rtext, today=TODAY, sleep=no_sleep, clock=clock))
    assert out.summary.stop_reason == "time_budget"


def test_12b_search_call_budget_is_enforced():
    cfg = replace(DEFAULT, budget=replace(DEFAULT.budget, max_search_calls=3, max_page_calls=2))
    out, web, _ = go({**BASE, "count": 10}, config=cfg)
    assert web.count("jobs") <= 3 and out.summary.page_calls <= 2
    assert out.summary.stop_reason == "search_budget"


def test_likely_active_counts_only_when_asked():
    def at(min_score, likely):
        prefs, p, rtext = setup({**BASE, "count": 10, "include_likely_active": likely})
        prefs = prefs.model_copy(update={"min_score": min_score})
        return run(scout(p, prefs, read=FakeWeb(), resume_text=rtext, today=TODAY, sleep=no_sleep))
    strict, loose = at(80, False), at(80, True)
    assert not any(j.verification.status == "likely_active" for j in returned(strict))
    assert any(j.verification.status == "likely_active" for j in returned(loose))
    assert len(returned(loose)) > len(returned(strict))


def test_stricter_threshold_returns_a_subset():
    std, _, _ = go({**BASE, "count": 10})
    strict, _, _ = go({**BASE, "count": 10, "strictness": "strict (90+)"})
    assert strict.minimum_match_score == 90
    assert {j.key for j in returned(strict)} <= {j.key for j in returned(std)}


def test_board_index_pages_are_harvested_for_more_postings():
    out, _, _ = go({**BASE, "count": 10})
    assert any("orbitworks/jobs/5044444" in j.source_url for j in out.jobs)


def test_runs_are_deterministic():
    a, _, _ = go({**BASE, "count": 10})
    b, _, _ = go({**BASE, "count": 10})
    assert [j.key for j in a.jobs] == [j.key for j in b.jobs]
    assert [a.matches[j.key].score for j in a.jobs] == [b.matches[j.key].score for j in b.jobs]


def test_no_resume_returns_no_qualified_jobs_only_leads():
    prefs, _, _ = setup(BASE, text="")
    out = run(scout(CandidateProfile(), prefs, read=FakeWeb(), today=TODAY, sleep=no_sleep))
    assert returned(out) == [] and all(out.matches[j.key].confidence == "low" for j in out.jobs)


# ---- the wider search: other channels, employer look-ups, memory between runs

class WideWeb(FakeWeb):
    """The fake web with the optional channels: an AI web search that cites a posting no plain search finds, and
    a similar-postings search."""
    CITED = "https://boards.greenhouse.io/orbitworks/jobs/5044444"

    async def __call__(self, source, **args):
        if source == "answers":
            self.calls.append((source, args))
            return {"records": [{"text": "Open roles...", "citations": [self.CITED]}], "complete": True}
        if source == "similar":
            self.calls.append((source, args))
            return {"records": [], "complete": True}
        if source == "jobs_more":
            self.calls.append((source, args))
            raise RuntimeError("CallFailed: sources.jobs_more: No connected account found for toolkit exa")
        return await super().__call__(source, **args)


def test_other_channels_add_postings_and_a_missing_second_app_costs_one_call():
    results = [r for r in RESULTS if "orbitworks" not in r[1]["url"]]   # only the AI search can find OrbitWorks
    web = WideWeb(results=results)
    out, _, _ = go({**BASE, "count": 10}, web=web)
    assert any("orbitworks/jobs/5044444" in j.source_url for j in out.jobs)
    assert web.count("answers") >= 1 and web.count("jobs_more") <= DEFAULT.budget.concurrency
    assert all(s.status != "unavailable" for s in out.sources if s.source == "second web search")


def test_board_finds_are_looked_up_on_the_employers_site():
    out, web, _ = go({**BASE, "count": 10})
    assert any(q.startswith("resolve:") for q in out.queries)


def test_known_closed_and_unfit_postings_are_skipped_next_time():
    first, web1, _ = go({**BASE, "count": 10})
    assert first.remember and any(v["why"] == "closed" for v in first.remember.values())
    second, web2, _ = go({**BASE, "count": 10}, known=first.remember)
    assert second.summary.skipped_known >= 1 and second.summary.page_calls < first.summary.page_calls
    assert [j.key for j in second.jobs if j.key not in second.near_misses] == \
           [j.key for j in first.jobs if j.key not in first.near_misses]   # same answer, less work


def test_snippet_years_reject_before_any_page_is_read():
    web = FakeWeb()
    out, _, _ = go({**BASE, "count": 10}, web=web, text=FRESHER)
    assert out.summary.rejected_before_reading >= 1


def test_core_searches_go_to_both_search_apps_when_two_are_set_up():
    class TwoEngines(FakeWeb):
        async def __call__(self, source, **args):
            if source == "jobs_more":
                out = await super().__call__("jobs", **args)    # a second engine that answers like the first
                self.calls[-1] = ("jobs_more", args)
                return out
            return await super().__call__(source, **args)
    web = TwoEngines()
    out, _, _ = go({**BASE, "count": 10}, web=web)
    assert web.count("jobs_more") >= 1 and web.count("jobs") >= 1
    assert len([j for j in out.jobs if j.key not in out.near_misses]) == 5   # same answer (4 verified + 1 likely), no duplicates


def test_a_job_closed_on_linkedin_is_rejected_even_when_the_employer_page_is_still_up():
    """Real case (Capco, 2026-11-02): Greenhouse still showed the job, LinkedIn said "No longer accepting
    applications". A closed notice on any copy closes the job."""
    pages = dict(PAGES)
    li = LI_APPLIED.split("?")[0]
    pages[li] = {**pages[li], "text": "Applied AI Engineer\nNimbusAI Pune\n3 weeks ago\n"
                                      "No longer accepting applications\n" + pages[li]["text"]}
    out, web, _ = go({**BASE, "count": 10}, web=FakeWeb(pages=pages))
    shown = [j for j in returned(out)]
    assert not any(j.company == "NimbusAI" and "Applied AI" in j.title for j in shown)
    assert any(u.split("?")[0] == li for _, a in web.calls if (u := a.get("args", {}).get("url")))
    assert out.summary.rejection_reasons.get("closed", 0) >= 1
    assert all(j.verification.status in ("verified_active", "likely_active") for j in shown)


def test_a_search_result_that_says_closed_is_never_counted():
    import normalize
    from providers.catalog import build
    rec = {"title": "AI Engineer at Mastercard | LinkedIn",
           "url": "https://in.linkedin.com/jobs/view/ai-engineer-at-mastercard-4461040749",
           "snippet": "Mastercard Pune, Maharashtra, India 1 month ago Be among the first 25 applicants "
                      "No longer accepting applications. Design and build AI solutions."}
    listing, _ = normalize.from_search(rec, build(), "q", TODAY)
    assert listing.closed_elsewhere and "no longer accepting applications" in listing.closed_elsewhere
    from verify import verify
    assert verify(listing, None, None, DEFAULT.policy, TODAY).status == "closed"
