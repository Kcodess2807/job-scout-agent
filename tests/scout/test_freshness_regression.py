"""The first website run (2026-11): LinkedIn postings shown as open that had stopped taking applications, and a
LinkedIn title read as company "Fresher". Each test is one root cause."""
from __future__ import annotations

import datetime as dt

import jd
from conftest import TODAY
from config import DEFAULT
from normalize import from_search, title_company, with_page
from providers.catalog import build, classify
from transport import PageResult
from verify import verify

P = build()
POLICY = DEFAULT.policy
LI = "https://in.linkedin.com/jobs/view/software-engineer-at-truscalar-ai-4454193413"
LI_TITLE = "Software Engineer – Fresher at TruScalar AI — Hyderabad | LinkedIn Jobs"


def li_listing(title=LI_TITLE, url=LI, published=None):
    lst, why = from_search({"title": title, "url": url, "snippet": "", **({"published": published} if published else {})},
                           P, "q", TODAY)
    assert lst, why
    return lst


def li_page(ago: str, extra: str = "") -> dict:
    text = (f"{LI_TITLE}\n\n# Software Engineer – Fresher\n\n TruScalar AI Hyderabad \n\n {ago} \n\n Over 200 applicants \n\n"
            "We are looking for a Software Engineer. Python, SQL, REST APIs. 0-1 years. " * 3 + extra)
    return {"url": LI, "title": LI_TITLE, "text": text}


def check(lst, page):
    lst = with_page(lst, page, classify(LI, P), TODAY)
    return lst, verify(lst, PageResult("ok", page, read_at=1.7e9), classify(LI, P), POLICY, TODAY)


# ---- company, title, location
def test_the_current_linkedin_title_names_the_company_not_fresher():
    lst = li_listing()
    assert (lst.title, lst.company) == ("Software Engineer – Fresher", "TruScalar AI")
    assert lst.location == "Hyderabad" and lst.key == "linkedin:4454193413"


def test_linkedin_title_shapes():
    cases = {
        "AI Engineer at Cornerstone OnDemand — Hyderabad, Telangana, India | LinkedIn Jobs": ("AI Engineer", "Cornerstone OnDemand"),
        "Senior Backend Engineer, Payments at Acme Health Labs — Remote | LinkedIn Jobs": ("Senior Backend Engineer, Payments", "Acme Health Labs"),
        "Machine Learning Intern at X.ai — Bengaluru | LinkedIn": ("Machine Learning Intern", "X.ai"),
        "Full Stack Developer (React/Node) at Foo, Inc. — Pune | LinkedIn Jobs": ("Full Stack Developer (React/Node)", "Foo, Inc."),
        "Acme hiring Backend Engineer in Pune | LinkedIn": ("Backend Engineer", "Acme"),
    }
    for raw, want in cases.items():
        assert title_company(raw, None) == want, raw


def test_a_word_that_is_never_a_company_is_not_taken_for_one():
    assert title_company("Software Engineer - Fresher | Remote", None) == ("Software Engineer", None)
    assert title_company("AI Engineer at Capco | AIMates Jobs", "Aimates")[1] == "AIMates"   # another site's name stays the URL's


def test_the_url_slug_names_the_company_only_when_the_title_does_not():
    prov = classify(LI, P)
    assert prov.company(LI, "") == "Truscalar Ai"
    assert prov.company(LI, LI_TITLE) == "TruScalar AI"
    assert prov.company("https://www.linkedin.com/jobs/view/4454193413", "") is None


def test_regional_linkedin_hosts_are_one_job():
    a = li_listing(url=LI + "?trk=public_jobs")
    b = li_listing(url="https://www.linkedin.com/jobs/view/4454193413/")
    assert a.key == b.key == "linkedin:4454193413"


# ---- freshness
def test_a_linkedin_posting_two_months_old_is_not_shown_as_open():
    """Root cause: "2 months ago" was 60 days, and boards counted as likely open up to 60 days."""
    _, v = check(li_listing(), li_page("2 months ago"))
    assert v.status == "unverified"


def test_relative_ages_are_read_at_their_oldest():
    assert jd.age_upper_days(3, "weeks") == 27 and jd.age_upper_days(1, "month") == 59 and jd.age_upper_days(5, "day") == 5
    assert check(li_listing(), li_page("3 weeks ago"))[1].status == "unverified"     # 21 to 27 days: past 21
    assert check(li_listing(), li_page("2 weeks ago"))[1].status == "likely_active"  # 14 to 20 days
    assert check(li_listing(), li_page("1 day ago"))[1].status == "likely_active"


def test_a_cached_page_saying_1_day_ago_does_not_make_an_old_posting_new():
    """The page reader can return a copy crawled when the job was new; the search's own date stays."""
    old = (TODAY - dt.timedelta(days=50)).isoformat()
    lst, v = check(li_listing(published=old), li_page("1 day ago"))
    assert lst.posted_at == old and v.status == "unverified"


def test_no_date_is_not_proof_of_anything():
    assert check(li_listing(), li_page(""))[1].status == "unverified"


# ---- closed
def test_closed_wording_closes_even_a_recent_posting_with_a_future_deadline():
    page = li_page("1 day ago", "\nNo longer accepting applications\nApplication deadline: 2026-12-31")
    assert check(li_listing(), page)[1].status == "closed"


def test_each_closed_phrasing_is_read():
    for phrase in ("Applications closed", "This job is no longer available", "Position has been filled", "Job expired",
                   "Posting removed", "Vacancy closed", "Not accepting applications"):
        assert jd.closed_signal(f"Backend Engineer. {phrase}. About us: we build things."), phrase


def test_a_closed_phrase_about_something_else_does_not_close_the_job():
    assert jd.closed_signal("We are not accepting applications from agencies. Apply now with your resume.") is None
    assert jd.closed_signal("Similar jobs: Data Engineer, applications closed last week. Backend Engineer, apply now.") is None


def test_a_passed_deadline_closes_and_a_future_one_alone_proves_nothing():
    _, v = check(li_listing(), li_page("1 day ago", "\nApplication deadline: 2026-10-01"))
    assert v.status == "closed"
    _, v = check(li_listing(), li_page("", "\nApplication deadline: 2026-12-31"))
    assert v.status == "unverified"
