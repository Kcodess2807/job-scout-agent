"""The rules, without a graph: job rows, the facts check, the email checks, reply matching, the cadence and caps."""

from datetime import UTC, datetime, timedelta

import jobs
import store


# ── Task 2: job rows and the seam ────────────────────────────────────────────────────────────────────
def test_a_bare_link_is_a_row_with_only_its_url():
    [row] = jobs.parse_lines("  https://boards.greenhouse.io/acme/jobs/123?gh_src=x  \n\n")
    assert row["url"] == "https://boards.greenhouse.io/acme/jobs/123?gh_src=x"
    assert (row["company"], row["title"], row["hr_email"]) == (None, None, None)


def test_a_four_part_line_fills_company_role_link_and_address():
    [row] = jobs.parse_lines("Acme | Backend Engineer | https://acme.com/careers/be | HR@Acme.com")
    assert (row["company"], row["title"], row["url"], row["hr_email"]) == (
        "Acme", "Backend Engineer", "https://acme.com/careers/be", "hr@acme.com")


def test_the_key_ignores_the_query_and_case_and_falls_back_to_company_and_title():
    a = jobs.job_key({"url": "https://Boards.Greenhouse.io/acme/jobs/123?gh_src=x"})
    b = jobs.job_key({"url": "https://boards.greenhouse.io/acme/jobs/123"})
    assert a == b == "boards.greenhouse.io/acme/jobs/123"
    assert jobs.job_key({"company": "Acme Inc.", "title": "Backend Engineer"}) == "acme-inc:backend-engineer"


def test_a_board_host_is_never_the_companys_domain():
    assert jobs.company_domain({"url": "https://boards.greenhouse.io/acme/jobs/1"}) is None
    assert jobs.company_domain({"url": "https://www.linkedin.com/jobs/view/1"}) is None
    assert jobs.company_domain({"url": "https://careers.acme.com/x"}) == "acme.com"
    assert jobs.company_domain({"url": "https://acme.co.in/careers/x"}) == "acme.co.in"


async def test_add_writes_each_job_once_under_its_key_and_keeps_where_it_came_from():
    store._LOCAL.clear()
    rows = jobs.parse_lines("https://acme.com/careers/be\nhttps://acme.com/careers/be?ref=2")
    added = await jobs.add(rows, "discovery")
    assert len(added) == 1 and added[0]["source"] == "discovery" and added[0]["found_at"]
    assert store.collection("jobs").get("acme.com/careers/be")["domain"] == "acme.com"


# ── Task 3: profile and projects ─────────────────────────────────────────────────────────────────────
import candidate as prof_mod  # noqa: E402

PROFILE = prof_mod.Profile(
    full_name="Aditya Rao", email="aditya@rao.dev", phone="+91 98765 43210", location="Bengaluru",
    links=["https://github.com/adityarao"], professional_summary="Backend engineer with 3 years in Python",
    skills=[prof_mod.SkillGroup(category="Languages", items=["Python", "Go"])],
    education=[prof_mod.Education(degree="B.Tech Computer Science", institution="RV College of Engineering", year="2021")],
    experiences=[prof_mod.Experience(role="Software Engineer", company="Razorpay", location="Bengaluru",
                                     duration="Jul 2021 - Present",
                                     achievements=["Cut p95 latency 40% on the payouts API", "Built a reconciliation job in Go"])],
    projects=[prof_mod.Project(title="Ledgerline", description="A double-entry ledger service",
                               technologies=["Go", "Postgres"], achievements=["Handles 2,000 writes a second in tests"])])


def test_the_master_text_carries_every_fact_the_checks_compare_against():
    m = prof_mod.master_text(PROFILE)
    for fact in ("Razorpay", "Jul 2021 - Present", "Cut p95 latency 40% on the payouts API", "RV College of Engineering",
                 "2021", "Ledgerline", "2,000 writes a second", "+91 98765 43210", "B.Tech Computer Science"):
        assert fact in m


async def test_resume_text_reads_a_local_file_value_and_pasted_text_wins_over_an_unreadable_file():
    text, source = await prof_mod.resume_text("Aditya Rao\nSoftware Engineer at Razorpay " * 5, None)
    assert source == "resume text" and "Razorpay" in text
    text, source = await prof_mod.resume_text("nope", "Aditya Rao, Software Engineer at Razorpay since 2021")
    assert source == "pasted text" and "Razorpay" in text


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.urls = routes, []

    async def get(self, url, **_):
        self.urls.append(url)
        status, body = next(((s, b) for k, (s, b) in self.routes.items() if k in url), (404, ""))

        class R:
            status_code = status
            text = body if isinstance(body, str) else ""

            def json(self):
                return body
        return R()


async def test_a_repo_not_pushed_since_the_last_sync_is_not_read_again():
    repos = [{"name": "ledgerline", "description": "ledger", "language": "Go", "topics": [], "stargazers_count": 3,
              "pushed_at": "2026-10-01T00:00:00Z", "html_url": "https://github.com/a/ledgerline", "fork": False},
             {"name": "newthing", "description": "new", "language": "Python", "topics": [], "stargazers_count": 0,
              "pushed_at": "2026-10-30T00:00:00Z", "html_url": "https://github.com/a/newthing", "fork": False}]
    http = FakeHttp({"/users/a/repos": (200, repos), "/readme": (200, "# newthing\nA CLI that does one thing.")})

    class M:
        notes: list = []

        def with_structured_output(self, schema):
            class B:
                async def ainvoke(self, _):
                    return schema(title="Newthing", description="A CLI", tech_stack=["Python"], features=[],
                                  resume_bullets=["Built a CLI"], category="CLI Tool", skills_demonstrated=[])
            return B()
    known = {"ledgerline": {"pushed_at": "2026-10-01T00:00:00Z", "title": "Ledgerline"}}
    got = await prof_mod.github_projects(M(), "a", known, http=http)
    assert [p["name"] for p in got] == ["newthing"]
    assert not any("ledgerline/readme" in u for u in http.urls)


# ── Task 4: tailoring ────────────────────────────────────────────────────────────────────────────────
import tailor  # noqa: E402

MASTER = prof_mod.master_text(PROFILE)


def test_a_number_must_be_the_resumes_own_number():
    assert tailor.facts_ok("Cut p95 latency 40% on the payouts API at Razorpay", MASTER)
    assert not tailor.facts_ok("Cut p95 latency 45% on the payouts API", MASTER)
    assert not tailor.facts_ok("Handled 5,000 writes a second", MASTER)


def test_a_name_or_skill_the_resume_never_mentions_is_not_claimed():
    assert not tailor.facts_ok("Built a reconciliation job in Rust", MASTER)
    assert not tailor.facts_ok("Shipped payouts at Stripe", MASTER)
    assert tailor.facts_ok("Built a reconciliation job in Go", MASTER)


def test_a_line_with_no_numbers_and_no_names_passes_so_a_resume_without_metrics_keeps_its_bullets():
    assert tailor.facts_ok("Wrote the on-call runbook and trained new engineers on it", MASTER)


def test_the_company_and_role_named_in_extra_text_may_appear():
    assert not tailor.facts_ok("I want to join Blacksmith as a Backend Engineer", MASTER)
    assert tailor.facts_ok("I want to join Blacksmith as a Backend Engineer", MASTER, extra="Blacksmith Backend Engineer")


def _doc(bullets):
    return tailor.ResumeDoc(name="Aditya Rao", contact_line="aditya@rao.dev", summary="Backend engineer with 3 years in Python.",
                            sections=[tailor.Section(heading="Experience", entries=[tailor.Entry(
                                title="Software Engineer", organisation="Razorpay", dates="Jul 2021 - Present", bullets=bullets)])],
                            skills_line="Languages: Python, Go")


def test_grounding_drops_only_the_bullet_that_invents_and_counts_it():
    doc, dropped = tailor.grounded(_doc(["Cut p95 latency 40% on the payouts API", "Cut costs 70% with Kubernetes"]), MASTER)
    assert doc.sections[0].entries[0].bullets == ["Cut p95 latency 40% on the payouts API"] and dropped == 1


def test_grounding_drops_an_entry_whose_employer_or_dates_are_not_the_resumes():
    doc = _doc(["Built a reconciliation job in Go"])
    doc.sections[0].entries[0].organisation = "Stripe"
    out, dropped = tailor.grounded(doc, MASTER)
    assert out.sections == [] and dropped == 1


def test_a_login_wall_is_not_a_posting():
    assert tailor.is_login_wall("Sign in to view this job. Join now. " * 3)
    assert tailor.is_login_wall("")
    assert not tailor.is_login_wall("We are hiring a Backend Engineer to own our payouts API. " * 40)


def test_the_skills_line_puts_what_the_posting_asks_for_first_and_adds_nothing():
    req = tailor.Requirements(must_have=["Go", "Kafka"], nice_to_have=[], keywords=[])
    assert tailor.skills_line(PROFILE, req) == "Languages: Go, Python"
    assert tailor.skill_match(req, MASTER) == (["Go"], ["Kafka"])


def test_the_pdf_is_a_pdf_and_its_name_is_first_last_company_role():
    assert tailor.pdf_bytes(_doc(["Built a reconciliation job in Go"]))[:4] == b"%PDF"
    assert tailor.pdf_name(PROFILE, "Blacksmith", "Backend Engineer (Go)") == "Aditya_Rao_Blacksmith_Backend_Engineer_Go.pdf"


def test_the_cover_letter_pdf_renders_text_with_markup_characters_as_text():
    pdf = tailor.letter_pdf(PROFILE, "Acme #1", "I use C# and [brackets] and $5 signs.\n\nSecond paragraph.")
    assert pdf[:4] == b"%PDF"


# ── Task 5: emails and the contact ───────────────────────────────────────────────────────────────────
import outreach  # noqa: E402
import research  # noqa: E402
import writer  # noqa: E402

REQ = tailor.Requirements(company="Blacksmith", title="Backend Engineer", must_have=["Go"],
                          company_fact="We run CI jobs on bare-metal gaming CPUs.")
EXTRA = "Blacksmith Backend Engineer We run CI jobs on bare-metal gaming CPUs."
NOTE = ("I'm a backend engineer at Razorpay. Your post says you run CI jobs on bare-metal gaming CPUs. "
        "I cut p95 latency 40% on the payouts API and built a reconciliation job in Go. "
        "Are you the right person to talk to about the Backend Engineer role?")


def test_a_short_note_with_one_fact_and_a_question_passes():
    assert writer.email_checks(NOTE, MASTER, EXTRA, "note", []) == []


def test_a_note_over_90_words_or_ending_without_a_question_or_mentioning_an_attachment_fails():
    long = NOTE + " More words here about nothing in particular." * 10
    assert any("over 90" in p for p in writer.email_checks(long, MASTER, EXTRA, "note", []))
    flat = NOTE.replace("Are you the right person to talk to about the Backend Engineer role?", "Thanks for reading.")
    assert "ends without a question" in writer.email_checks(flat, MASTER, EXTRA, "note", [])
    assert any("attach" in p for p in writer.email_checks(NOTE + " My resume is attached?", MASTER, EXTRA, "note", []))


def test_an_email_that_claims_what_the_resume_does_not_fails():
    bad = NOTE.replace("40%", "60%")
    assert any("the resume doesn't" in p for p in writer.email_checks(bad, MASTER, EXTRA, "note", []))


def test_an_application_email_has_applyzers_shape_three_bullets_under_150_words():
    good = ("I'm a backend engineer with 3 years in Python, applying for the Backend Engineer role at Blacksmith.\n\n"
            "• Cut p95 latency 40% on the payouts API\n• Built a reconciliation job in Go\n"
            "• Handles 2,000 writes a second in tests (Ledgerline)\n\nI'd welcome a conversation about the role.")
    assert writer.email_checks(good, MASTER, EXTRA, "application", []) == []
    two = good.replace("• Built a reconciliation job in Go\n", "")
    assert "needs three bullets" in writer.email_checks(two, MASTER, EXTRA, "application", [])


def test_a_link_must_be_the_candidates_own():
    body = NOTE.replace("in Go.", "in Go (https://github.com/adityarao/ledgerline).")
    assert writer.email_checks(body, MASTER, EXTRA, "note", ["https://github.com/adityarao"]) == []
    assert any("isn't yours" in p for p in writer.email_checks(body.replace("adityarao", "someone"), MASTER, EXTRA, "note",
                                                               ["https://github.com/adityarao"]))


def test_the_subject_names_every_role_at_the_company():
    assert outreach.subject(["Backend Engineer"], "Acme") == "Application for Backend Engineer at Acme"
    assert outreach.subject(["Backend Engineer", "SRE"], "Acme") == "Application for Backend Engineer / SRE at Acme"


def _call(log):
    """A page reader that reads nothing and notes what was asked."""
    async def page(url):
        log.append(url)
        return ""
    return page


async def test_the_address_given_with_the_job_comes_first_and_nothing_is_looked_up():
    log = []
    c = await outreach.find_contact(_call(log), None, {"hr_email": "hr@acme.com", "domain": "acme.com"}, REQ)
    assert (c["email"], c["how"], c["kind"]) == ("hr@acme.com", "given with the job", "application") and log == []


async def test_an_address_on_the_posting_is_used_but_never_a_noreply(monkeypatch):
    log = []
    req = REQ.model_copy(update={"emails": ["noreply@acme.com", "Jobs@Acme.com"]})
    c = await outreach.find_contact(_call(log), None, {"domain": "acme.com"}, req)
    assert (c["email"], c["how"], c["kind"]) == ("jobs@acme.com", "on the posting", "application")

    async def team(page, model, d, personas, company=""):
        return {"name": "Priya Nair", "title": "Talent Partner", "email": "priya@acme.com", "tier": "verified",
                "email_source": "provider", "sendable": True}
    monkeypatch.setattr(research, "contact", team)
    c = await outreach.find_contact(_call(log), None, {"domain": "acme.com"}, REQ.model_copy(update={"emails": ["noreply@acme.com"]}))
    assert (c["email"], c["kind"], c["verified"]) == ("priya@acme.com", "note", True) and "Priya Nair" in c["name"]


async def test_no_company_domain_and_no_address_means_no_contact(monkeypatch):
    async def nobody(*a, **k):
        return {"note": "nobody named"}
    monkeypatch.setattr(research, "contact", nobody)
    c = await outreach.find_contact(_call([]), None, {"domain": None}, REQ)
    assert c["email"] is None and c["how"] == "none"


# ── Task 7: the send gate ────────────────────────────────────────────────────────────────────────────
import stages  # noqa: E402

NOW = datetime(2026, 11, 2, 9, tzinfo=UTC)


def test_the_twenty_first_send_of_the_day_waits():
    assert stages.can_send(sent_today=19, company_sent_at=None, now=NOW) is None
    assert stages.can_send(sent_today=20, company_sent_at=None, now=NOW) == "today's 20 sends are used; send it tomorrow"


def test_one_email_per_company_in_thirty_days():
    assert stages.can_send(0, (NOW - timedelta(days=10)).isoformat(), NOW) == "this company was emailed 10 days ago"
    assert stages.can_send(0, (NOW - timedelta(days=31)).isoformat(), NOW) is None


# ── Task 8: replies ──────────────────────────────────────────────────────────────────────────────────
import replies  # noqa: E402

APPS = [{"key": "a", "company": "Blacksmith", "domain": "blacksmith.sh", "thread": "t1", "stage": "sent"},
        {"key": "b", "company": "Acme", "domain": "acme.com", "stage": "applied", "applied_on": "2026-10-30"},
        {"key": "c", "company": "Nimbus", "domain": "nimbus.io", "stage": "docs_ready"}]


def _msg(frm, subject="", text="", thread="x9"):
    return {"id": "m", "thread": thread, "from": frm, "to": "", "subject": subject, "text": text, "when": None, "labels": []}


def test_a_reply_in_the_thread_matches_its_application():
    assert replies.match(_msg("hr@blacksmith.sh", thread="t1"), APPS)["key"] == "a"


def test_ats_mail_naming_the_company_matches_only_a_job_the_candidate_applied_to():
    ats = _msg("no-reply@us.greenhouse-mail.io", "Your application to Acme", "Thank you for applying to Acme.")
    assert replies.match(ats, APPS)["key"] == "b"
    nimbus = _msg("no-reply@greenhouse.io", "Your application to Nimbus", "Thanks for applying to Nimbus.")
    assert replies.match(nimbus, APPS) is None                      # never applied: ignored
    assert replies.match(_msg("talent@acme.com", "Next steps"), APPS)["key"] == "b"
    assert replies.match(_msg("friend@gmail.com", "Acme"), APPS) is None


def test_rules_sort_an_acknowledgement_a_bounce_and_an_opt_out_before_any_model():
    assert replies.rule_kind(_msg("no-reply@greenhouse.io", "Thank you for applying", "We have received your application.")) == "auto_ack"
    assert replies.rule_kind(_msg("mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)", "Address not found")) == "bounce"
    assert replies.rule_kind(_msg("hr@acme.com", "Re: x", "Please remove me from your list")) == "opt_out"
    assert replies.rule_kind(_msg("hr@acme.com", "Re: x", "Can we talk on Friday?")) is None


def test_the_answer_bank_answers_what_it_holds_and_nothing_else():
    bank = {"notice_days": 30, "expected_ctc": "28 LPA", "work_auth": "can work there", "links": "linkedin.com/in/aditya"}
    assert "30 days" in replies.answer_from_bank("What is your notice period?", bank)["text"]
    assert "28 LPA" in replies.answer_from_bank("What are your salary expectations?", bank)["text"]
    assert replies.answer_from_bank("Could you send your CV?", bank)["attach"] is True
    assert replies.answer_from_bank("Are you free for a call on Friday?", bank) is None
    assert replies.answer_from_bank("What is your current CTC?", bank) is None      # not given: not invented


# ── Task 9: the follow-up cadence ────────────────────────────────────────────────────────────────────
def _sent(kind, days, follow_ups=0, **more):
    return {"stage": "sent", "thread": "t1", "sent_at": (NOW - timedelta(days=days)).isoformat(), "follow_ups": follow_ups,
            "email": {"kind": kind}, **more}


def test_a_short_note_is_followed_up_on_day_4_and_day_10_and_closed_on_day_21():
    assert stages.due(_sent("note", 3), NOW) is None
    assert stages.due(_sent("note", 4), NOW) == "follow_up_1"
    assert stages.due(_sent("note", 9, 1), NOW) is None
    assert stages.due(_sent("note", 10, 1), NOW) == "follow_up_2"
    assert stages.due(_sent("note", 21, 2), NOW) == "close"


def test_an_application_email_follows_applyzer_day_7_and_day_14():
    assert stages.due(_sent("application", 4), NOW) is None
    assert stages.due(_sent("application", 7), NOW) == "follow_up_1"
    assert stages.due(_sent("application", 14, 1), NOW) == "follow_up_2"


def test_a_form_application_gets_one_note_on_day_7_and_a_thread_keeps_its_own_clock():
    applied = {"stage": "applied", "applied_on": (NOW - timedelta(days=7)).date().isoformat(), "follow_ups": 0}
    assert stages.due(applied, NOW) == "applied"
    assert stages.due({**applied, "follow_ups": 1}, NOW) is None
    assert stages.due(_sent("note", 4, applied_on=(NOW - timedelta(days=7)).date().isoformat()), NOW) == "follow_up_1"


def test_nothing_is_due_after_a_reply_or_a_close():
    assert stages.due({**_sent("note", 4), "stage": "replied"}, NOW) is None
    assert stages.due({**_sent("note", 30, 2), "stage": "done"}, NOW) is None


# ── final review fixes ───────────────────────────────────────────────────────────────────────────────
WALL = ("Sign in to view more jobs. Join now. LinkedIn. User Agreement. Privacy Policy. Cookie Policy. Copyright Policy. "
        "Email or phone. Password. Show. Forgot password? Sign in. New to LinkedIn? Join now. Agree & Join LinkedIn. "
        "By clicking Continue to join or sign in, you agree to LinkedIn's User Agreement, Privacy Policy, and Cookie Policy. "
        "Similar jobs. People also viewed. Explore collaborative articles. ") * 4


def test_c1_a_long_linkedin_wall_is_a_wall_and_a_long_posting_mentioning_sign_in_once_is_not():
    assert len(WALL) > 1500 and tailor.is_login_wall(WALL)
    posting = "We are hiring a Backend Engineer to own our payouts API. " * 60 + "Sign in to apply."
    assert not tailor.is_login_wall(posting)


def test_c2a_an_entry_whose_dates_are_written_another_way_keeps_its_bullets():
    for dates in ("July 2021 - Present", "2021-Present", "Jul 2021 – Present"):
        doc = _doc(["Built a reconciliation job in Go"])
        doc.sections[0].entries[0].dates = dates
        out, dropped = tailor.grounded(doc, MASTER)
        assert dropped == 0 and out.sections[0].entries[0].bullets == ["Built a reconciliation job in Go"], dates


def test_c2b_common_acronyms_and_spelled_out_names_do_not_drop_a_bullet():
    source = MASTER + "\nDeployed services on Amazon Web Services with PostgreSQL"
    assert tailor.facts_ok("Built REST APIs in Go and returned JSON", source)
    assert tailor.facts_ok("Wrote SQL queries for the payouts API", source)
    assert tailor.facts_ok("Deployed the payouts API to AWS", source)
    assert not tailor.facts_ok("Deployed the payouts API to GCP", source)


def test_c2c_a_lowercased_skill_from_the_posting_or_a_magnitude_word_is_still_checked():
    terms = ["Rust", "Kubernetes", "Go"]
    assert not tailor.facts_ok("built a service in rust with kubernetes", MASTER, terms=terms)
    assert tailor.facts_ok("built a reconciliation job in go", MASTER, terms=terms)
    assert not tailor.facts_ok("served three million users", MASTER)


def test_i4_ats_mail_matches_a_job_applied_to_by_email():
    apps = [{"key": "s", "company": "Blacksmith", "domain": "blacksmith.sh", "stage": "sent", "thread": "t1",
             "sent_at": "2026-10-30T09:00:00+00:00", "email": {"kind": "application"}}]
    ats = _msg("no-reply@greenhouse.io", "Your application to Blacksmith", "We will not be moving forward.", thread="new")
    assert replies.match(ats, apps)["key"] == "s"
    other = _msg("cto@blacksmith.sh", "About your application", "Can we talk?", thread="new2")
    assert replies.match(other, apps)["key"] == "s"


TEAM = ("Our team. Priya Nair, Talent Partner, runs hiring for engineering at Acme. Sam Lee, Founder, started Acme. "
        "Write to us at priya@acme.com or hello@acme.com. ") * 3


class _People:
    notes: list = []

    def with_structured_output(self, schema):
        class B:
            async def ainvoke(self, _):
                return research.People(people=[research.Person(
                    name="Priya Nair", title="Talent Partner", url="https://acme.com/team",
                    quote="Priya Nair, Talent Partner, runs hiring for engineering at Acme")])
        return B()


async def test_without_hunter_a_contact_needs_an_address_printed_on_the_companys_own_pages():
    async def page(url):
        return TEAM if url == "https://acme.com/team" else ""
    c = await research.contact(page, _People(), "acme.com", outreach.PERSONAS, "Acme")
    assert (c["name"], c["email"], c["email_source"]) == ("Priya Nair", "priya@acme.com", "published")

    async def no_address(url):
        return TEAM.replace("priya@acme.com or ", "") if url == "https://acme.com/team" else ""
    c = await research.contact(no_address, _People(), "acme.com", outreach.PERSONAS, "Acme")
    assert not c.get("email") and c["name"] == "Priya Nair" and "no address" in c["note"]


# ── the resume looks like APPLYZER's LaTeX template (resume.cls) ────────────────────────────────────────
def _pdf_text(data: bytes) -> str:
    import io

    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)


def test_the_resume_pdf_follows_applyzers_template():
    doc = tailor.ResumeDoc(name="Aditya Rao", contact_line="aditya@rao.dev | +91 98765 43210 | Bengaluru",
                           summary="Backend engineer with 3 years in Python.",
                           sections=[tailor.Section(heading="Education", entries=[tailor.Entry(
                                         title="B.Tech Computer Science", organisation="RV College of Engineering", dates="2021")]),
                                     tailor.Section(heading="Experience", entries=[tailor.Entry(
                                         title="Software Engineer", organisation="Razorpay", dates="Jul 2021 - Present",
                                         location="Bengaluru", bullets=["Cut p95 latency 40% on the payouts API"])]),
                                     tailor.Section(heading="Projects", entries=[tailor.Entry(
                                         title="Ledgerline", tech=["Go", "Postgres"], bullets=["Handles 2,000 writes a second in tests"])])],
                           skills_line="Languages: Go, Python. Tools: Postgres, Docker")
    text = _pdf_text(tailor.pdf_bytes(doc))
    assert "ADITYA RAO" in text and "OBJECTIVE" in text                      # the name and headings in capitals
    assert text.index("EDUCATION") < text.index("SKILLS") < text.index("EXPERIENCE")  # skills follow education
    assert "Bengaluru" in text and "Ledgerline." in text and "(Go, Postgres)" in text
    assert "Languages" in text and "Tools" in text


def test_a_location_or_tech_the_resume_does_not_have_drops_the_entry():
    doc = _doc(["Built a reconciliation job in Go"])
    doc.sections[0].entries[0].location = "Singapore"
    assert tailor.grounded(doc, MASTER)[0].sections == []
    doc = _doc(["Built a reconciliation job in Go"])
    doc.sections[0].entries[0].tech = ["Go", "Rust"]
    assert tailor.grounded(doc, MASTER)[0].sections[0].entries[0].tech == ["Go"]      # an invented tool is dropped


# ── the seam: a found job's domain is the company's, not its careers host ─────────────────────────
def test_a_found_jobs_domain_is_the_companys_registrable_domain():
    import discover
    from models import Listing, Match, ScoutOutcome  # his, from src/scout
    lst = Listing(key="careers:1", title="AI Backend Engineer", company="Zentrix", company_domain="careers.zentrix.ai",
                  source="careers", source_url="https://careers.zentrix.ai/jobs/1", canonical_url="https://careers.zentrix.ai/jobs/1")
    out = ScoutOutcome(requested_count=1, minimum_match_score=70, search_status="complete", jobs=[lst],
                       matches={"careers:1": Match(score=80)})
    [row] = discover.rows_of(out)
    assert row["domain"] == "zentrix.ai" and row["url"] == "https://careers.zentrix.ai/jobs/1"
