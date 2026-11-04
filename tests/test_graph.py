"""The graph end to end with a fake of the v2 calls (sources mail and page, writes send and draft) and a fake model.
No network. The fake keeps each call's arguments in Gmail's words (recipient_email, body, thread_id, attachment), so
an assertion reads the same whichever contract made the call."""

import json

import base64

import pytest

import agent
import mail
import store
import tailor
import writer
from candidate import Education, Experience, Profile, Project, SkillGroup

LOG: list[str] = []  # call names in order, across calls


class Tool:
    """One fake endpoint: its function answers, its calls are kept."""

    def __init__(self, name, fn):
        self.name, self.fn, self.calls = name, fn, []

    def __call__(self, args):
        self.calls.append(args)
        LOG.append(self.name)
        out = self.fn(args)
        return json.loads(out) if isinstance(out, str) and out[:1] in "{[" else out


class FakeIo:
    """tooling.Gravity's methods over the fake endpoints: web_fetch, gmail_read, gmail_send, gmail_draft."""

    def __init__(self, tools):
        self.t = tools

    def _tool(self, name):
        if name not in self.t:
            pytest.fail(f"{name} was called and this test gave it no answer")
        return self.t[name]

    async def page(self, url):
        got = self._tool("web_fetch")({"urls": [url]})
        rs = (got or {}).get("results") or []
        return (rs[0].get("text") or "") if rs else ""

    async def mail(self, folder):
        """The `mail` source by standard folder; the fake endpoint still sees it as a query ("in:sent")."""
        return mail.messages(self._tool("gmail_read")({"query": f"in:{folder}", "folder": folder}))

    HIS = {"search": "jobs", "page": "pages", "search_more": "jobs_more", "answers": "answers", "similar": "similar"}

    async def read(self, source, **args):
        """The search's reads: the fake endpoint for `source` hands back his FakeWeb, called by his source names. An
        optional source the test set up no fake for is off, as the gateway answers for one the person skipped."""
        if source not in self.t:
            raise RuntimeError(f"this run has no sources.{source}")
        web = self._tool(source)({"source": source})
        return await web(self.HIS[source], **args)

    async def find_email(self, first, last, domain):
        """The Hunter built-ins: {} when the test set none up (the manifest's built-ins off), else its answer."""
        if "hunter" not in self.t:
            return {}
        return self._tool("hunter")({"first_name": first, "last_name": last, "domain": domain}) or {}

    async def send(self, to, subject, text, file=None):
        got = self._tool("gmail_send")({"recipient_email": to, "subject": subject, "body": text, "is_html": False,
                                        **({"attachment": file} if file else {})})
        if isinstance(got, str):
            if "APPROVAL_REJECTED" in got or "APPROVAL_TIMEOUT" in got:
                return {"rejected": True}
            raise RuntimeError(got)
        return {"id": (got or {}).get("id"), "thread": (got or {}).get("threadId")}

    async def track(self, rows):
        """The optional `tracker` write: skipped (None) when the test set up no sheet, the error text when it fails."""
        if "sheet" not in self.t:
            return None
        got = self.t["sheet"]({"rows": rows})
        return got if isinstance(got, str) else None

    async def draft(self, to, subject, text, thread=None, file=None):
        got = self._tool("gmail_draft")({"recipient_email": to, "subject": subject, "body": text, "is_html": False,
                                         **({"thread_id": thread} if thread else {}), **({"attachment": file} if file else {})})
        return {"id": (got or {}).get("id") or "d1"}

    async def send_draft(self, draft_id):
        """The `send_draft` write (message.send_draft): the fake endpoint answers {id, threadId}, a refusal string, or an error."""
        got = self._tool("gmail_send_draft")({"draft_id": draft_id})
        if isinstance(got, str):
            if "APPROVAL_REJECTED" in got or "APPROVAL_TIMEOUT" in got:
                return {"rejected": True}
            raise RuntimeError(got)
        return {"id": (got or {}).get("id"), "thread": (got or {}).get("threadId")}


class FakeModel:
    """Answers each structured call from `canned[schema]`: a value, or a function of the user prompt."""

    def __init__(self, canned):
        self.canned, self.calls, self.notes = canned, [], []

    def with_structured_output(self, schema):
        fake = self

        class Bound:
            async def ainvoke(self, messages):
                fake.calls.append(schema.__name__)
                if schema not in fake.canned:  # the search's own model calls: it treats a failure as "no model"
                    raise RuntimeError(f"no canned answer for {schema.__name__}")
                got = fake.canned[schema]
                return got(messages[-1]["content"]) if callable(got) else got
        return Bound()


@pytest.fixture
def wired(monkeypatch):
    """wired(fake_endpoints, canned) -> (endpoints, model); run(state) invokes the graph."""
    store._LOCAL.clear()
    LOG.clear()

    def wire(fns: dict, canned: dict):
        t = {k: Tool(k, v) for k, v in fns.items()}
        model = FakeModel(canned)
        monkeypatch.setattr(agent, "make_io", lambda: FakeIo(t))
        monkeypatch.setattr(agent, "make_model", lambda: model)
        monkeypatch.setattr(agent, "make_embedder", lambda: None)   # the search's embeddings: lexical fallback in tests
        return t, model
    return wire


async def run(state):
    return await (await agent.graph()).ainvoke(state)


async def test_graph_compiles(wired):
    wired({}, {})
    assert (await agent.graph()) is not None


# ── fixtures for prepare ─────────────────────────────────────────────────────────────────────────────
PROFILE = Profile(
    full_name="Aditya Rao", email="aditya@rao.dev", phone="+91 98765 43210", location="Bengaluru",
    links=["https://github.com/adityarao"], professional_summary="Backend engineer with 3 years in Python",
    skills=[SkillGroup(category="Languages", items=["Python", "Go"])],
    education=[Education(degree="B.Tech Computer Science", institution="RV College of Engineering", year="2021")],
    experiences=[Experience(role="Software Engineer", company="Razorpay", location="Bengaluru", duration="Jul 2021 - Present",
                            achievements=["Cut p95 latency 40% on the payouts API", "Built a reconciliation job in Go"])],
    projects=[Project(title="Ledgerline", description="A double-entry ledger service", technologies=["Go", "Postgres"],
                      achievements=["Handles 2,000 writes a second in tests"])])
RESUME = "Aditya Rao. Software Engineer at Razorpay since Jul 2021. Cut p95 latency 40% on the payouts API. " * 3
POSTING = ("Blacksmith is hiring a Backend Engineer. We run CI jobs on bare-metal gaming CPUs. You will write Go "
           "services that schedule jobs across our fleet. Must have: Go, Postgres. Nice to have: Kafka. " * 12)


def req_for(prompt):
    if "Acme" in prompt or "acme" in prompt:
        return tailor.Requirements(company="Acme", title="Platform Engineer", must_have=["Go", "Kafka"],
                                   company_fact="We run CI jobs on bare-metal gaming CPUs.")
    return tailor.Requirements(company="Blacksmith", title="Backend Engineer", must_have=["Go", "Kubernetes"],
                               nice_to_have=["Kafka"], company_fact="We run CI jobs on bare-metal gaming CPUs.")


TAILORED = tailor.Tailored(summary="Backend engineer with 3 years in Python.", sections=[tailor.Section(
    heading="Experience", entries=[tailor.Entry(title="Software Engineer", organisation="Razorpay", dates="Jul 2021 - Present",
                                                bullets=["Built a reconciliation job in Go", "Cut p95 latency 40% on the payouts API",
                                                         "Scaled Kafka to 9 million events a day"])])])
LETTER = tailor.Letter(body="\n\n".join([
    "I would like to apply for the Backend Engineer role. We run CI jobs on bare-metal gaming CPUs is the line in the "
    "posting that made me write, because the work it describes is the work i have done for three years.",
    "At Razorpay i cut p95 latency 40% on the payouts API, and i built a reconciliation job in Go that runs every "
    "night. Both are close to scheduling jobs across a fleet, which is what the posting asks for. " * 2,
    "I would start by reading how jobs are placed today and where they wait. Could we talk about the role this week? "
    "I can share more detail on the payouts work and the reconciliation job if that helps."]))
BODY = writer.Body(body="I'm a backend engineer at Razorpay applying for the Backend Engineer role.\n\n"
                        "• Cut p95 latency 40% on the payouts API\n• Built a reconciliation job in Go\n"
                        "• Handles 2,000 writes a second in tests\n\nCould we talk about the role?")


def canned():
    return {Profile: PROFILE, tailor.Requirements: req_for, tailor.Tailored: TAILORED, tailor.Letter: LETTER,
            writer.Body: BODY}


def page_fetch(pages):
    def fetch(args):
        return {"results": [{"url": u, "text": pages.get(u, "")} for u in args["urls"]]}
    return fetch


def gmail_none():
    return {k: (lambda a: pytest.fail("prepare must not touch Gmail")) for k in ("gmail_send", "gmail_draft", "gmail_read")}


TWO_JOBS = ("Blacksmith | Backend Engineer | https://blacksmith.sh/careers/be | hr@blacksmith.sh\n"
            "Acme | Platform Engineer | https://acme.com/jobs/pe | jobs@acme.com")


async def test_prepare_tailors_each_job_from_the_resume_alone_and_touches_no_gmail(wired):
    pages = {"https://blacksmith.sh/careers/be": POSTING, "https://acme.com/jobs/pe": POSTING.replace("Blacksmith", "Acme")}
    t, model = wired({"web_fetch": page_fetch(pages), **gmail_none()}, canned())
    out = await run({"inputs": {"jobs": TWO_JOBS, "resume_text": RESUME}})
    rows = out["result"]["applications"]
    assert [(r["company"], r["role"], r["stage"]) for r in rows] == [
        ("Blacksmith", "Backend Engineer", "ready to send"), ("Acme", "Platform Engineer", "ready to send")]
    b = rows[0]
    assert base64.b64decode(b["pdf_b64"])[:4] == b"%PDF" and b["pdf_name"] == "Aditya_Rao_Blacksmith_Backend_Engineer.pdf"
    assert b["contact"] == "hr@blacksmith.sh (given with the job)"
    assert b["fit"] == "has Go; missing Kubernetes" and b["lines_dropped"] == 1      # the Kafka bullet invented a number
    app = store.collection("applications").get("blacksmith.sh/careers/be")
    assert app["email"]["problems"] == [] and app["email"]["kind"] == "application"
    assert app["email"]["subject"] == "Application for Backend Engineer at Blacksmith"
    assert all("Kafka" not in b for e in app["resume"]["sections"][0]["entries"] for b in e["bullets"])
    assert out["memory"]["last_rows"] == ["blacksmith.sh/careers/be", "acme.com/jobs/pe"]


async def test_the_resume_is_parsed_once_for_the_same_text(wired):
    pages = {"https://blacksmith.sh/careers/be": POSTING}
    t, model = wired({"web_fetch": page_fetch(pages)}, canned())
    first = await run({"inputs": {"jobs": "https://blacksmith.sh/careers/be", "resume_text": RESUME}})
    assert model.calls.count("Profile") == 1
    await run({"inputs": {"jobs": "https://blacksmith.sh/careers/be", "resume_text": RESUME}, "memory": first["memory"]})
    assert model.calls.count("Profile") == 1


async def test_a_login_wall_waits_for_the_posting_text_and_nothing_is_tailored_from_the_title(wired):
    url = "https://www.linkedin.com/jobs/view/123"
    t, model = wired({"web_fetch": page_fetch({url: "Sign in to view this job. Join now to see who is hiring."})}, canned())
    out = await run({"inputs": {"jobs": f"Blacksmith | Backend Engineer | {url}", "resume_text": RESUME}})
    [row] = out["result"]["applications"]
    assert row["stage"] == "needs the posting's text" and "sign-in" in row["note"]
    assert "Tailored" not in model.calls and "Requirements" not in model.calls
    # the candidate pastes it on row 1, and it is prepared
    t, model = wired({"web_fetch": page_fetch({}), "gmail_send": lambda a: pytest.fail("no send")}, canned())
    out = await run({"inputs": {"jobs": f"Blacksmith | Backend Engineer | {url}", "resume_text": RESUME}, "memory": out["memory"],
                     "actions": ["paste_posting"], "action_inputs": {"rows": ["1"], "text": POSTING}})
    [row] = out["result"]["applications"]
    assert row["stage"] == "ready: no address yet" and row["pdf_b64"]   # a LinkedIn link names no company site


async def test_a_page_that_says_the_job_is_closed_is_closed(wired):
    url = "https://acme.com/jobs/old"
    t, model = wired({"web_fetch": page_fetch({url: "This job has expired and is no longer accepting applications. " * 20})}, canned())
    out = await run({"inputs": {"jobs": url, "resume_text": RESUME}})
    assert out["result"]["applications"][0]["stage"] == "closed"


async def test_at_most_five_jobs_a_run_and_the_rest_wait_for_the_next(wired):
    urls = [f"https://acme{i}.com/jobs/x" for i in range(7)]
    t, model = wired({"web_fetch": page_fetch({u: POSTING for u in urls})}, canned())
    out = await run({"inputs": {"jobs": "\n".join(urls), "resume_text": RESUME}})
    stages = [r["stage"] for r in out["result"]["applications"]]
    assert len(stages) == 7 and stages.count("to prepare") == 2          # every job is on the board; two wait their turn
    assert any("2 more jobs wait" in n for n in out["result"]["could_not"])
    out = await run({"inputs": {"jobs": "\n".join(urls), "resume_text": RESUME}, "memory": out["memory"]})
    assert [r["stage"] for r in out["result"]["applications"]].count("to prepare") == 0


async def test_a_declined_job_is_not_prepared_again(wired):
    url = "https://acme.com/jobs/pe"
    t, model = wired({"web_fetch": page_fetch({url: POSTING})}, canned())
    store.collection("declined").put("acme.com/jobs/pe", {"at": "2026-11-01"})
    out = await run({"inputs": {"jobs": url, "resume_text": RESUME}})
    assert out["result"]["applications"] == [] and "Requirements" not in model.calls


async def test_two_roles_at_one_company_share_one_email_naming_both(wired):
    jobs = ("Acme | Platform Engineer | https://acme.com/jobs/pe | jobs@acme.com\n"
            "Acme | SRE | https://acme.com/jobs/sre | jobs@acme.com")
    pages = {"https://acme.com/jobs/pe": POSTING, "https://acme.com/jobs/sre": POSTING}
    t, model = wired({"web_fetch": page_fetch(pages)}, canned())
    await run({"inputs": {"jobs": jobs, "resume_text": RESUME}})
    a, b = (store.collection("applications").get(k) for k in ("acme.com/jobs/pe", "acme.com/jobs/sre"))
    assert a["email"]["subject"] == b["email"]["subject"] == "Application for Platform Engineer / SRE at Acme"
    assert a["group"] == b["group"]


# ── Task 7: send_email, mark_applied, not_applying ──────────────────────────────────────────────────
PAGES = {"https://blacksmith.sh/careers/be": POSTING, "https://acme.com/jobs/pe": POSTING.replace("Blacksmith", "Acme"),
         "https://acme.com/jobs/sre": POSTING.replace("Blacksmith", "Acme")}


async def prepared(wired, jobs=TWO_JOBS, profile=PROFILE):
    c = {**canned(), Profile: profile}
    wired({"web_fetch": page_fetch(PAGES)}, c)
    out = await run({"inputs": {"jobs": jobs, "resume_text": RESUME}})
    return out["memory"], c


def sending(answer):
    return {"web_fetch": page_fetch(PAGES), "gmail_send": answer, "gmail_draft": lambda a: {"id": "d1"}}


async def test_an_approved_application_email_goes_once_with_the_tailored_resume_attached(wired):
    memory, c = await prepared(wired)
    t, model = wired(sending(lambda a: {"id": "m1", "threadId": "t1"}), c)
    out = await run({"inputs": {"jobs": TWO_JOBS, "resume_text": RESUME}, "memory": memory, "actions": ["send_email"],
                     "action_inputs": {"rows": ["1"]}})
    [sent] = t["gmail_send"].calls
    assert sent["recipient_email"] == "hr@blacksmith.sh" and sent["subject"] == "Application for Backend Engineer at Blacksmith"
    assert sent["body"].startswith("Hi there,\n\n") and sent["body"].rstrip().endswith("+91 98765 43210 | https://github.com/adityarao")
    assert sent["attachment"]["name"] == "Aditya_Rao_Blacksmith_Backend_Engineer.pdf"
    assert base64.b64decode(sent["attachment"]["data"])[:4] == b"%PDF"
    app = store.collection("applications").get("blacksmith.sh/careers/be")
    assert (app["stage"], app["thread"], app["sent_by"]) == ("sent", "t1", "gravity") and app["sent_at"]
    assert out["result"]["applications"][0]["update"] == "sent" and model.calls == []      # sending writes nothing new
    assert out["result"]["applications"][0]["stage"] == "emailed"


async def test_a_refused_send_is_kept_as_a_gmail_draft(wired):
    memory, c = await prepared(wired)
    t, _ = wired(sending(lambda a: "APPROVAL_REJECTED: the person said no"), c)
    out = await run({"inputs": {}, "memory": memory, "actions": ["send_email"], "action_inputs": {"rows": ["2"]}})
    [draft] = t["gmail_draft"].calls
    assert draft["recipient_email"] == "jobs@acme.com" and draft["attachment"]["name"].endswith(".pdf")
    assert store.collection("applications").get("acme.com/jobs/pe")["stage"] == "draft_kept"
    assert out["result"]["applications"][1]["update"] == "kept as a draft: not approved"


async def test_two_roles_at_one_company_go_in_one_email_and_a_later_role_there_waits(wired):
    jobs = ("Acme | Platform Engineer | https://acme.com/jobs/pe | jobs@acme.com\n"
            "Acme | SRE | https://acme.com/jobs/sre | jobs@acme.com")
    memory, c = await prepared(wired, jobs)
    t, _ = wired(sending(lambda a: {"id": "m1", "threadId": "t7"}), c)
    await run({"inputs": {}, "memory": memory, "actions": ["send_email"], "action_inputs": {"rows": ["1", "2"]}})
    assert len(t["gmail_send"].calls) == 1
    assert {store.collection("applications").get(k)["thread"] for k in ("acme.com/jobs/pe", "acme.com/jobs/sre")} == {"t7"}
    # a third role at Acme, prepared later, is drafted, not sent: one email per company in 30 days
    t, _ = wired({"web_fetch": page_fetch({"https://acme.com/jobs/data": POSTING})}, c)
    later = await run({"inputs": {"jobs": "Acme | Data Engineer | https://acme.com/jobs/data | jobs@acme.com",
                                  "resume_text": RESUME}, "memory": memory})
    t, _ = wired(sending(lambda a: pytest.fail("a second email to Acme")), c)
    out = await run({"inputs": {}, "memory": later["memory"], "actions": ["send_email"], "action_inputs": {"rows": ["3"]}})
    assert out["result"]["applications"][2]["update"] == "kept as a draft: this company was emailed 0 days ago" and t["gmail_draft"].calls


async def test_an_email_whose_checks_failed_or_with_no_address_is_never_sent(wired):
    bad = writer.Body(body="I am writing to say I scaled Kafka to 9 million events a day!")
    memory, c = await prepared(wired)
    c[writer.Body] = bad
    wired({"web_fetch": page_fetch(PAGES)}, c)
    store._LOCAL.clear()
    memory = (await run({"inputs": {"jobs": TWO_JOBS, "resume_text": RESUME}}))["memory"]
    t, _ = wired(sending(lambda a: pytest.fail("a failing email was sent")), c)
    out = await run({"inputs": {}, "memory": memory, "actions": ["send_email"], "action_inputs": {"rows": ["1"]}})
    update = out["result"]["applications"][0]["update"]
    assert update.startswith("kept as a draft") and "the resume doesn't" in update


async def test_mark_applied_starts_the_clock_and_not_applying_removes_the_job_for_good(wired):
    memory, c = await prepared(wired)
    wired(sending(lambda a: {}), c)
    out = await run({"inputs": {}, "memory": memory, "actions": ["mark_applied"], "action_inputs": {"rows": ["1"]}})
    app = store.collection("applications").get("blacksmith.sh/careers/be")
    assert app["applied_on"] == agent.utcnow().date().isoformat() and out["result"]["applications"][0]["stage"] == "applied"
    out = await run({"inputs": {}, "memory": out["memory"], "actions": ["mark_applied"],
                     "action_inputs": {"rows": ["2"], "applied_on": "2026-10-30"}})
    assert store.collection("applications").get("acme.com/jobs/pe")["applied_on"] == "2026-10-30"
    out = await run({"inputs": {}, "memory": out["memory"], "actions": ["not_applying"], "action_inputs": {"rows": ["2"]}})
    assert store.collection("applications").get("acme.com/jobs/pe") is None
    assert store.collection("declined").get("acme.com/jobs/pe")
    assert [r["company"] for r in out["result"]["applications"]] == ["Blacksmith"]


async def test_a_personal_mailbox_still_sends_and_the_result_says_so(wired):
    memory, c = await prepared(wired, profile=PROFILE.model_copy(update={"email": "aditya.rao@gmail.com"}))
    t, _ = wired(sending(lambda a: {"id": "m1", "threadId": "t1"}), c)
    out = await run({"inputs": {}, "memory": memory, "actions": ["send_email"], "action_inputs": {"rows": ["1"]}})
    assert t["gmail_send"].calls and "personal address" in out["result"]["applications"][0]["update"]


# ── Task 8: check_replies ────────────────────────────────────────────────────────────────────────────
import replies  # noqa: E402


async def sent_and_applied(wired):
    """Blacksmith emailed (thread t1), Acme applied to by form."""
    memory, c = await prepared(wired)
    wired(sending(lambda a: {"id": "m1", "threadId": "t1"}), c)
    out = await run({"inputs": {}, "memory": memory, "actions": ["send_email"], "action_inputs": {"rows": ["1"]}})
    out = await run({"inputs": {}, "memory": out["memory"], "actions": ["mark_applied"], "action_inputs": {"rows": ["2"]}})
    return out["memory"], c


def event(*msgs):
    return {"event": {"emails": [{"id": f"r{i}", "date": "2026-11-02T10:00:00Z", **m} for i, m in enumerate(msgs)]}}


def reading(kind, words, question=""):
    return replies.Reading(kind=kind, confidence=0.9, their_words=words, question=question)


def inbox_tools():
    return {"gmail_draft": lambda a: {"id": "d9"}, "web_fetch": lambda a: pytest.fail("a reply run never researches"),
            "gmail_send": lambda a: pytest.fail("replies are drafted, never sent")}


async def test_an_interview_invite_comes_first_with_a_reply_drafted_in_its_thread(wired):
    memory, c = await sent_and_applied(wired)
    words = "Can we talk on Friday about the Backend Engineer role?"
    c.update({replies.Reading: reading("interview", words),
              replies.Reply: replies.Reply(body="Thank you for writing. I would be glad to talk. Could you share two or three times that suit you?")})
    t, model = wired(inbox_tools(), c)
    out = await run({"memory": memory, "inputs": event({"thread_id": "t1", "from": "Priya <hr@blacksmith.sh>", "subject": "Re: Application",
                                      "text": words})})
    assert out["result"]["needs_you"][0].startswith("Blacksmith, Backend Engineer: interview")
    [d] = t["gmail_draft"].calls
    assert d["thread_id"] == "t1" and d["recipient_email"] == "hr@blacksmith.sh" and d["body"].startswith("Hi Priya,")
    assert store.collection("applications").get("blacksmith.sh/careers/be")["reply_kind"] == "interview"
    assert "Requirements" not in model.calls


async def test_an_opt_out_closes_the_application_and_the_company_is_never_written_to_again(wired):
    memory, c = await sent_and_applied(wired)
    t, _ = wired(inbox_tools(), c)
    await run({"memory": memory, "inputs": event({"thread_id": "t1", "from": "hr@blacksmith.sh", "subject": "Re: Application",
                                "text": "Please remove me from your list."})})
    assert store.collection("suppressed").get("blacksmith.sh")
    assert store.collection("applications").get("blacksmith.sh/careers/be")["stage"] == "done"
    assert t["gmail_draft"].calls == []


async def test_a_request_for_the_cv_is_answered_from_the_bank_with_the_tailored_resume_attached(wired):
    memory, c = await sent_and_applied(wired)
    c[replies.Reading] = reading("question", "Could you send your CV?", "Could you send your CV?")
    t, _ = wired(inbox_tools(), c)
    out = await run({"memory": memory, "inputs": {**event({"thread_id": "t1", "from": "hr@blacksmith.sh", "subject": "Re: Application",
                                         "text": "Thanks. Could you send your CV?"}), "notice_days": 30}})
    [d] = t["gmail_draft"].calls
    assert d["attachment"]["name"] == "Aditya_Rao_Blacksmith_Backend_Engineer.pdf" and "attached my resume" in d["body"]
    assert "question" in out["result"]["needs_you"][0]


async def test_an_ats_rejection_for_a_job_applied_by_form_is_matched_and_closed(wired):
    memory, c = await sent_and_applied(wired)
    words = "Unfortunately we will not be moving forward with your application to Acme."
    c.update({replies.Reading: reading("rejection", words),
              replies.Reply: replies.Reply(body="Thank you for letting me know. Would you keep me in mind, or point me to another team that is hiring?")})
    t, _ = wired(inbox_tools(), c)
    await run({"memory": memory, "inputs": event({"thread_id": "x1", "from": "no-reply@us.greenhouse-mail.io",
                                "subject": "Your application to Acme", "text": words})})
    assert store.collection("applications").get("acme.com/jobs/pe")["reply_kind"] == "rejection"
    assert t["gmail_draft"].calls == []       # a no-reply address gets no thanks; the application is closed
    assert store.collection("applications").get("acme.com/jobs/pe")["stage"] == "done"


async def test_mail_from_nobody_the_candidate_applied_to_is_ignored(wired):
    memory, c = await sent_and_applied(wired)
    t, model = wired(inbox_tools(), c)
    out = await run({"memory": memory, "inputs": event({"thread_id": "zz", "from": "news@somewhere.com", "subject": "Weekly digest",
                                      "text": "Ten things about Acme and Blacksmith."})})
    assert t["gmail_draft"].calls == [] and model.calls == [] and not out["result"].get("needs_you")


# ── Task 9: follow_ups and report ────────────────────────────────────────────────────────────────────
from datetime import timedelta  # noqa: E402

FOLLOW = writer.Body(body="Following up on my note about the Backend Engineer role. I built a reconciliation job in Go, "
                          "close to the scheduling work in the posting. Is the role still open?")


def put_app(key, **a):
    store.collection("applications").put(key, {"key": key, "company": "Blacksmith", "title": "Backend Engineer",
                                               "domain": "blacksmith.sh", "order": key, "follow_ups": 0, "req": {
                                                   "company": "Blacksmith", "title": "Backend Engineer"}, **a})


def ago(days):
    return (agent.utcnow() - timedelta(days=days)).isoformat()


def fu_tools(thread_msgs=None, sent=None):
    inbox = [m for msgs in (thread_msgs or {}).values() for m in msgs]
    return {"gmail_read": lambda a: {"messages": (sent or []) if "in:sent" in a["query"] else inbox},
            "gmail_draft": lambda a: {"id": "d1"},
            "gmail_send": lambda a: pytest.fail("a scheduled run never sends")}


MEMORY = {"profile": PROFILE.model_dump()}
NOTE_EMAIL = {"kind": "note", "to": "priya@blacksmith.sh", "to_name": "Priya Nair", "subject": "Application for Backend Engineer at Blacksmith"}


async def test_a_day_4_note_is_drafted_in_the_thread_and_nothing_is_sent(wired):
    t, _ = wired(fu_tools(), {writer.Body: FOLLOW})
    put_app("a", stage="sent", thread="t1", sent_at=ago(4), email=NOTE_EMAIL,
            contact={"email": "priya@blacksmith.sh", "name": "Priya Nair"})
    out = await run({"memory": MEMORY, "inputs": {}, "actions": ["follow_ups"]})
    [d] = t["gmail_draft"].calls
    assert d["thread_id"] == "t1" and d["recipient_email"] == "priya@blacksmith.sh"
    assert d["subject"] == "Re: Application for Backend Engineer at Blacksmith" and d["body"].startswith("Hi Priya,")
    assert store.collection("applications").get("a")["follow_ups"] == 1
    assert out["result"]["applications"][0]["update"] == "follow-up 1 drafted, in the thread: send it from the row, or from your mailbox"
    row = out["result"]["applications"][0]
    assert row["draft_kind"] == "follow-up 1" and row["draft"].startswith("Hi Priya")   # the draft waits on the row too


async def test_a_reply_already_in_the_thread_stops_the_follow_up_and_asks_for_you(wired):
    reply = {"messageId": "x", "threadId": "t1", "sender": "Priya <priya@blacksmith.sh>", "messageText": "Thanks, let's talk.",
             "messageTimestamp": agent.utcnow().isoformat()}
    t, _ = wired(fu_tools({"t1": [reply]}), {writer.Body: FOLLOW})
    put_app("a", stage="sent", thread="t1", sent_at=ago(4), email=NOTE_EMAIL, contact={"email": "priya@blacksmith.sh"})
    out = await run({"memory": MEMORY, "inputs": {}, "actions": ["follow_ups"]})
    assert t["gmail_draft"].calls == [] and "Thanks, let's talk." in out["result"]["needs_you"][0]
    assert store.collection("applications").get("a")["stage"] == "replied"


async def test_a_kept_draft_the_candidate_sent_by_hand_is_found_in_sent(wired):
    sent = [{"messageId": "s1", "threadId": "t5", "sender": "me@rao.dev", "to": "jobs@acme.com",
             "messageText": "Hi there, my edited note", "messageTimestamp": ago(1)}]
    t, _ = wired(fu_tools(sent=sent), {writer.Body: FOLLOW})
    put_app("b", company="Acme", domain="acme.com", stage="draft_kept", draft_text="Hi there, my note",
            email={"kind": "application", "to": "jobs@acme.com", "subject": "Application for SRE at Acme"})
    out = await run({"memory": MEMORY, "inputs": {}, "actions": ["follow_ups"]})
    app = store.collection("applications").get("b")
    assert (app["stage"], app["thread"], app["sent_by"]) == ("sent", "t5", "you") and app["edit_ratio"] > 0
    assert "you sent it yourself" in out["result"]["applications"][0]["update"]


async def test_day_21_closes_quietly_and_a_form_application_gets_one_note_or_a_line_for_you(wired):
    t, _ = wired(fu_tools(), {writer.Body: FOLLOW})
    put_app("a", stage="sent", thread="t1", sent_at=ago(21), follow_ups=2, email=NOTE_EMAIL, contact={"email": "priya@blacksmith.sh"})
    put_app("c", company="Nimbus", domain="nimbus.io", stage="applied", applied_on=ago(7)[:10],
            contact={"email": "hr@nimbus.io", "kind": "application"},
            email={"kind": "application", "to": "hr@nimbus.io", "subject": "Application for Backend Engineer at Nimbus"})
    put_app("d", company="Orbit", domain="orbit.dev", stage="applied", applied_on=ago(8)[:10], contact={})
    out = await run({"memory": MEMORY, "inputs": {}, "actions": ["follow_ups"]})
    assert store.collection("applications").get("a")["stage"] == "done"
    [d] = t["gmail_draft"].calls
    assert d["recipient_email"] == "hr@nimbus.io" and "thread_id" not in d
    assert any("no address" in x for x in out["result"]["needs_you"] if x.startswith("Orbit"))


async def test_the_weekly_report_counts_by_stage_and_by_reply(wired):
    wired(fu_tools(), {})
    put_app("a", stage="sent", sent_at=ago(2), email={"kind": "note"})
    put_app("b", stage="replied", reply_kind="interview", sent_at=ago(5), replied_at=ago(1), email={"kind": "application"},
            follow_up_drafts=[{"n": 1, "at": ago(3), "text": "x", "sent_at": ago(2)}])
    put_app("c", stage="done", reply_kind="rejection", sent_at=ago(20), replied_at=ago(10), email={"kind": "application"})
    out = await run({"memory": MEMORY, "inputs": {}, "actions": ["report"]})
    r = out["result"]
    assert r["sent_this_week"] == 2 and r["replies_this_week"] == 1
    assert r["by_stage"] == {"emailed": 1, "replied: interview": 1, "closed": 1}
    assert r["by_email_kind"] == {"application": {"sent": 2, "replied": 2, "median_days_to_reply": 7},
                                  "note": {"sent": 1, "replied": 0, "median_days_to_reply": None}}
    assert r["follow_ups"]["replies_after_one"] == 1 and "2 of 2 application emails (about 7 days to a reply)" in r["summary"]


async def test_a_status_update_from_the_ats_is_recorded_and_asks_nothing_of_you(wired):
    memory, c = await sent_and_applied(wired)
    t, model = wired(inbox_tools(), c)
    out = await run({"memory": memory, "inputs": event({"from": "Acme Careers <no-reply@greenhouse-mail.io>", "subject": "Your application at Acme",
                                                        "text": "Hello Aditya, your application at Acme has been reviewed by the hiring team and moved to the next stage."})})
    assert not out["result"].get("needs_you") and t["gmail_draft"].calls == [] and model.calls == []
    app = store.collection("applications").get("acme.com/jobs/pe")
    assert app["acknowledged"] and app["stage"] == "applied" and "moved to the next stage" in app["last_reply"]
    assert [r["update"] for r in out["result"]["applications"] if r["company"] == "Acme"] == ["status update"]


async def test_a_kept_draft_the_candidate_already_sent_from_gmail_is_not_sent_again(wired):
    memory, c = await prepared(wired)
    wired(sending(lambda a: "APPROVAL_REJECTED: the person said no"), c)
    out = await run({"inputs": {}, "memory": memory, "actions": ["send_email"], "action_inputs": {"rows": ["1"]}})
    sent = [{"messageId": "s1", "threadId": "t7", "sender": "aditya@rao.dev", "to": "hr@blacksmith.sh",
             "messageText": "Hi there, I applied with a few edits", "messageTimestamp": agent.utcnow().isoformat()}]
    t, _ = wired({**sending(lambda a: pytest.fail("already sent by hand: must not go again")),
                  "gmail_read": lambda a: {"messages": sent if "in:sent" in a["query"] else []}}, c)
    out = await run({"inputs": {}, "memory": out["memory"], "actions": ["send_email"], "action_inputs": {"rows": ["1"]}})
    app = store.collection("applications").get("blacksmith.sh/careers/be")
    assert (app["stage"], app["sent_by"], app["thread"]) == ("sent", "you", "t7")
    assert out["result"]["applications"][0]["update"].startswith("skipped: not sent again: you sent it yourself")
    assert t["gmail_draft"].calls == []


# ── final review fixes ───────────────────────────────────────────────────────────────────────────────
ACME_TWO = ("Acme | Platform Engineer | https://acme.com/jobs/pe | jobs@acme.com\n"
            "Acme | SRE | https://acme.com/jobs/sre | jobs@acme.com")


async def test_c1_a_page_the_model_finds_no_requirements_in_waits_for_the_text(wired):
    url = "https://acme.com/jobs/x"
    c = {**canned(), tailor.Requirements: tailor.Requirements(company="Acme", title="")}
    t, model = wired({"web_fetch": page_fetch({url: POSTING})}, c)
    out = await run({"inputs": {"jobs": url, "resume_text": RESUME}})
    assert out["result"]["applications"][0]["stage"] == "needs the posting's text" and "Tailored" not in model.calls


async def test_i1_a_bounce_in_a_shared_thread_closes_both_roles(wired):
    memory, c = await prepared(wired, ACME_TWO)
    wired(sending(lambda a: {"id": "m1", "threadId": "t7"}), c)
    out = await run({"inputs": {}, "memory": memory, "actions": ["send_email"], "action_inputs": {"rows": ["1"]}})
    wired(inbox_tools(), c)
    await run({"memory": out["memory"], "inputs": event({"thread_id": "t7", "from": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
                                                          "subject": "Delivery Status Notification (Failure)", "text": "Address not found"})})
    assert {store.collection("applications").get(k)["stage"] for k in ("acme.com/jobs/pe", "acme.com/jobs/sre")} == {"done"}


async def test_i2_dropping_one_of_two_grouped_roles_rewrites_the_other_roles_email(wired):
    memory, c = await prepared(wired, ACME_TWO)
    wired({"web_fetch": page_fetch(PAGES), "gmail_send": lambda a: pytest.fail("no send")}, c)
    out = await run({"inputs": {}, "memory": memory, "actions": ["not_applying"], "action_inputs": {"rows": ["2"]}})
    left = store.collection("applications").get("acme.com/jobs/pe")
    assert not left.get("email") and out["result"]["applications"][0]["stage"] == "email needs rewriting"
    out = await run({"inputs": {"jobs": ACME_TWO, "resume_text": RESUME}, "memory": out["memory"]})
    assert store.collection("applications").get("acme.com/jobs/pe")["email"]["subject"] == "Application for Platform Engineer at Acme"


async def test_i3_an_old_email_to_the_same_address_is_not_taken_for_the_kept_draft(wired):
    old = [{"messageId": "s0", "threadId": "t0", "sender": "me@rao.dev", "to": "jobs@acme.com",
            "messageText": "an email from last month", "messageTimestamp": ago(20)}]
    t, _ = wired(fu_tools(sent=old), {writer.Body: FOLLOW})
    put_app("b", company="Acme", domain="acme.com", stage="draft_kept", draft_text="Hi there, my note", draft_kept_at=ago(2),
            email={"kind": "application", "to": "jobs@acme.com", "subject": "Application for SRE at Acme"})
    await run({"memory": MEMORY, "inputs": {}, "actions": ["follow_ups"]})
    assert store.collection("applications").get("b")["stage"] == "draft_kept"


async def test_every_result_tells_the_page_how_to_draw_its_table(wired):
    """`_layout` names the one table and draws each row as a card; the platform's page honours it, older pages ignore it."""
    wired(fu_tools(), {})
    out = await run({"memory": MEMORY, "inputs": {}, "actions": ["report"]})
    [layout] = out["result"]["_layout"]
    assert (layout["type"], layout["key"], layout["title"]) == ("cards", "applications", "company")
    assert {s["key"] for s in layout["sections"]} <= set(agent.board({}, {"k": {"key": "k", "stage": "docs_ready", "order": "1"}})[0][0])
    out = await run({"memory": {}, "inputs": {"jobs": "https://acme.com/jobs/1"}})   # no resume: the early return
    assert out["result"]["_layout"] == agent.LAYOUT


async def test_i5_every_result_has_one_table(wired):
    memory, c = await prepared(wired)
    wired(sending(lambda a: {"id": "m1", "threadId": "t1"}), c)
    out = await run({"inputs": {}, "memory": memory, "actions": ["send_email"], "action_inputs": {"rows": ["1"]}})
    tables = [k for k, v in out["result"].items() if isinstance(v, list) and v and isinstance(v[0], dict) and not k.startswith("_")]
    assert tables == ["applications"]   # _layout says how to draw it; it is not a table
    assert "letter_problems" in out["result"]["applications"][0]   # what the letter check found, beside the letter


async def test_i6_the_day_7_note_goes_only_to_an_address_that_came_with_the_job(wired):
    t, _ = wired(fu_tools(), {writer.Body: FOLLOW})
    put_app("e", company="Nimbus", domain="nimbus.io", stage="applied", applied_on=ago(7)[:10],
            contact={"email": "priya@nimbus.io", "kind": "note", "name": "Priya Nair"})
    out = await run({"memory": MEMORY, "inputs": {}, "actions": ["follow_ups"]})
    assert t["gmail_draft"].calls == [] and any("Nimbus" in x for x in out["result"]["needs_you"])


async def test_i7_a_reply_from_anyone_else_in_the_thread_stops_the_follow_up(wired):
    ours = {"messageId": "o", "threadId": "t1", "sender": "Aditya <me@rao.dev>", "messageText": "my note", "messageTimestamp": ago(4)}
    cc = {"messageId": "x", "threadId": "t1", "sender": "Sam <sam@othermail.com>", "messageText": "Looping in the team.",
          "messageTimestamp": ago(1)}
    t, _ = wired(fu_tools({"t1": [ours, cc]}), {writer.Body: FOLLOW})
    put_app("a", domain=None, stage="sent", thread="t1", sent_at=ago(4), email=NOTE_EMAIL, contact={"email": "priya@blacksmith.sh"})
    await run({"memory": MEMORY, "inputs": {}, "actions": ["follow_ups"]})
    assert t["gmail_draft"].calls == [] and store.collection("applications").get("a")["stage"] == "replied"


# ── the Google Sheet tracker (APPLYZER kept one through the Sheets API) ─────────────────────────────────
async def test_every_change_lands_in_the_sheet_one_row_per_application_keyed_by_its_link(wired):
    memory, c = await prepared(wired)
    t, _ = wired({**sending(lambda a: {"id": "m1", "threadId": "t1"}), "sheet": lambda a: {"ok": True}}, c)
    store._LOCAL.clear()
    out = await run({"inputs": {"jobs": TWO_JOBS, "resume_text": RESUME}})
    first = t["sheet"].calls[-1]["rows"]
    assert [list(r)[0] for r in first] == ["Link", "Link"]                       # the key column comes first
    assert [(r["Company"], r["Stage"]) for r in first] == [("Blacksmith", "ready to send"), ("Acme", "ready to send")]
    out = await run({"inputs": {}, "memory": out["memory"], "actions": ["send_email"], "action_inputs": {"rows": ["1"]}})
    [row] = t["sheet"].calls[-1]["rows"]
    assert (row["Link"], row["Stage"], row["Thread"], row["Sent to"]) == (
        "https://blacksmith.sh/careers/be", "emailed", "t1", "hr@blacksmith.sh")
    await run({"inputs": {}, "memory": out["memory"], "actions": ["not_applying"], "action_inputs": {"rows": ["2"]}})
    assert t["sheet"].calls[-1]["rows"][0]["Stage"] == "not applying"


async def test_a_sheet_that_fails_is_named_in_the_result_and_one_never_set_up_is_skipped(wired):
    t, _ = wired({"web_fetch": page_fetch({"https://blacksmith.sh/careers/be": POSTING}),
                  "sheet": lambda a: "the spreadsheet was deleted"}, canned())
    out = await run({"inputs": {"jobs": "https://blacksmith.sh/careers/be", "resume_text": RESUME}})
    assert any("Google Sheet" in n for n in out["result"]["could_not"])
    t, _ = wired({"web_fetch": page_fetch({"https://blacksmith.sh/careers/be": POSTING})}, canned())
    store._LOCAL.clear()
    out = await run({"inputs": {"jobs": "https://blacksmith.sh/careers/be", "resume_text": RESUME}})
    assert not any("Sheet" in n for n in out["result"]["could_not"])


# ── follow-up drafts the candidate sent, and how much they changed them (the number behind "switch to auto-send") ──
async def test_a_follow_up_draft_sent_by_hand_is_noticed_with_how_much_it_was_changed(wired):
    drafted = "Hi Priya,\n\nFollowing up on my note about the Backend Engineer role. Is the role still open?\n\nBest,\nAditya Rao"
    sent = [{"messageId": "s9", "threadId": "t1", "sender": "me@rao.dev", "to": "priya@blacksmith.sh",
             "messageText": drafted.replace("Is the role still open?", "Is the role still open this month?"), "messageTimestamp": ago(1)}]
    t, _ = wired(fu_tools(sent=sent), {writer.Body: FOLLOW})
    put_app("a", stage="sent", thread="t1", sent_at=ago(6), follow_ups=1, last_follow_up_at=ago(2), email=NOTE_EMAIL,
            contact={"email": "priya@blacksmith.sh", "name": "Priya Nair"},
            follow_up_drafts=[{"n": 1, "at": ago(2), "text": drafted}])
    await run({"memory": MEMORY, "inputs": {}, "actions": ["follow_ups"]})
    app = store.collection("applications").get("a")
    [d] = app["follow_up_drafts"]
    assert d["sent_at"] and 0 < d["edit_ratio"] < 0.2 and app["last_touch_at"] == sent[0]["messageTimestamp"]
    assert t["gmail_draft"].calls == []                                  # nothing new is due


async def test_the_weekly_report_counts_follow_ups_drafted_sent_and_unchanged(wired):
    wired(fu_tools(), {})
    put_app("a", stage="sent", sent_at=ago(9), follow_ups=2,
            follow_up_drafts=[{"n": 1, "at": ago(5), "text": "x", "sent_at": ago(4), "edit_ratio": 0.0},
                              {"n": 2, "at": ago(1), "text": "y"}])
    put_app("b", company="Acme", stage="sent", sent_at=ago(6), follow_ups=1,
            follow_up_drafts=[{"n": 1, "at": ago(2), "text": "z", "sent_at": ago(1), "edit_ratio": 0.3}])
    out = await run({"memory": MEMORY, "inputs": {}, "actions": ["report"]})
    assert out["result"]["follow_ups"] == {"drafted": 3, "sent_by_you": 2, "sent_unchanged": 1, "replies_after_one": 0}
    assert "2 of 3 follow-up drafts sent" in out["result"]["summary"]


# ── discover: Sid's search inside this graph, through the seam ──────────────────────────────────────
import sys as _sys  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_sys.path.insert(0, str(_Path(__file__).resolve().parent / "scout"))
import fakes as scout_fakes  # noqa: E402  (his FakeWeb and fixture resume, from tests/scout)

SEARCH_INPUTS = {"resume_text": scout_fakes.resume(), "role": "AI engineer", "country": "India", "city": "Pune",
                 "remote": "either", "count": "3", "looking_for": "jobs", "employment_type": "any"}


def search_tools():
    web = scout_fakes.FakeWeb()
    return {"search": lambda a: web, "page": lambda a: web, "gmail_send": lambda a: pytest.fail("a search never sends")}


async def test_a_first_run_with_a_resume_and_no_job_links_searches_and_shows_what_it_found(wired):
    t, model = wired(search_tools(), canned())
    out = await run({"inputs": SEARCH_INPUTS})
    r = out["result"]
    found = [row for row in r["applications"] if row["stage"] == "found"]
    assert 1 <= len(found) <= 3 and r["summary"].startswith(f"{len(found)} job")
    row = found[0]
    assert row["company"] and row["role"] and row["apply_link"].startswith("http") and row["fit"].startswith("fit ")
    key = out["memory"]["last_rows"][0]
    job = store.collection("jobs").get(key)
    assert job["source"] == "discovery" and job["description"] and not job.get("picked")
    assert "Tailored" not in model.calls                               # nothing is prepared until the person picks
    assert out["memory"]["scout"]["seen"]                                # his notebook, under its own key
    assert out["memory"].get("profile") is None or "resume_hash" in out["memory"]  # ours is untouched


async def test_picking_a_found_job_prepares_it_and_the_rest_stay_found(wired):
    t, model = wired(search_tools(), canned())
    out = await run({"inputs": SEARCH_INPUTS})
    n = len(out["result"]["applications"])
    t, model = wired({**search_tools(), **gmail_none()}, canned())
    out = await run({"inputs": SEARCH_INPUTS, "memory": out["memory"], "actions": ["pick"], "action_inputs": {"rows": ["1"]}})
    rows = out["result"]["applications"]
    assert len(rows) == n and rows[0]["stage"] in ("ready to send", "ready: no address yet") and rows[0]["pdf_name"]
    assert all(row["stage"] == "found" for row in rows[1:])
    assert "Tailored" in model.calls


async def test_a_run_with_job_links_still_prepares_without_searching(wired):
    t, model = wired({"web_fetch": page_fetch({"https://blacksmith.sh/careers/be": POSTING}), **search_tools()}, canned())
    out = await run({"inputs": {**SEARCH_INPUTS, "jobs": "Blacksmith | Backend Engineer | https://blacksmith.sh/careers/be | hr@blacksmith.sh",
                                "resume_text": RESUME}})
    assert out["result"]["applications"][0]["stage"] == "ready to send" and not t["search"].calls


async def test_an_action_run_without_the_resume_uses_the_profile_already_known(wired):
    """Found on the first real run: `paste_posting` carries no resume text (an action run), and Prepare refused with
    "no resume" although the profile from the first run was in the notebook."""
    memory, c = await prepared(wired, jobs="Blacksmith | Backend Engineer | https://www.linkedin.com/jobs/view/1 | hr@blacksmith.sh")
    t, _ = wired({"web_fetch": page_fetch(PAGES), **gmail_none()}, c)
    out = await run({"inputs": {}, "memory": memory, "actions": ["paste_posting"],
                     "action_inputs": {"rows": ["1"], "text": POSTING}})
    assert out["result"]["applications"][0]["stage"] == "ready to send"
    assert not any("no resume" in x for x in out["result"].get("could_not") or [])


async def test_an_optional_search_source_that_is_not_connected_is_asked_once(wired, monkeypatch):
    """Seen on the first real search: Exa (search_more, optional) wasn't connected, and the engine asked the gateway
    for it eight times, five seconds each. The first refusal stands for the run."""
    calls = []

    class Io(FakeIo):
        async def read(self, source, **args):
            calls.append(source)
            if source == "search_more":
                raise RuntimeError("NEEDS_RECONNECT: Exa isn't connected for this consumer")
            return await super().read(source, **args)
    t, model = wired(search_tools(), canned())
    monkeypatch.setattr(agent, "make_io", lambda: Io(t))
    out = await run({"inputs": SEARCH_INPUTS, "memory": {}, "actions": ["discover"]})
    assert calls.count("search_more") <= 1
    assert out["result"]["applications"]


# ── Send the draft: a kept draft goes out from the row, after approval ──────────────────────────────
def draft_tools(answer, drafts=None):
    return {"gmail_draft": lambda a: {"id": f"d{len(drafts) + 1 if drafts is not None else 1}"},
            "gmail_send_draft": answer, "gmail_read": lambda a: {"messages": []},
            "gmail_send": lambda a: pytest.fail("Send the draft never sends a new email")}


async def test_the_application_kept_as_a_draft_is_sent_from_the_row_after_approval(wired):
    memory, c = await prepared(wired)
    wired(sending(lambda a: "APPROVAL_REJECTED: the person said no"), c)
    out = await run({"inputs": {}, "memory": memory, "actions": ["send_email"], "action_inputs": {"rows": ["1"]}})
    row = out["result"]["applications"][0]
    assert row["stage"] == "draft kept" and row["draft_kind"] == "the application" and row["draft"].startswith("Hi there")
    t, _ = wired(draft_tools(lambda a: {"id": "m9", "threadId": "t9"}), c)
    out = await run({"inputs": {}, "memory": out["memory"], "actions": ["send_draft"], "action_inputs": {"rows": ["1"]}})
    assert t["gmail_send_draft"].calls == [{"draft_id": "d1"}]
    app = store.collection("applications").get("blacksmith.sh/careers/be")
    assert (app["stage"], app["sent_by"], app["thread"], app["follow_ups"]) == ("sent", "gravity", "t9", 0)
    assert out["result"]["applications"][0]["update"] == "the application sent" and out["result"]["summary"] == "1 draft sent."


async def test_a_follow_up_draft_is_sent_from_the_row_and_counts_as_sent_unchanged(wired):
    t, _ = wired(fu_tools(), {writer.Body: FOLLOW})
    t["gmail_draft"] = Tool("gmail_draft", lambda a: {"id": "fu1"})
    put_app("a", stage="sent", thread="t1", sent_at=ago(4), email=NOTE_EMAIL, contact={"email": "priya@blacksmith.sh"})
    out = await run({"memory": MEMORY, "inputs": {}, "actions": ["follow_ups"]})
    assert store.collection("applications").get("a")["follow_up_drafts"][0]["id"] == "fu1"
    t, _ = wired(draft_tools(lambda a: {"id": "m2", "threadId": "t1"}), {})
    out = await run({"memory": out["memory"], "inputs": {}, "actions": ["send_draft"], "action_inputs": {"rows": ["1"]}})
    d = store.collection("applications").get("a")["follow_up_drafts"][0]
    assert d["sent_at"] and d["edit_ratio"] == 0.0 and t["gmail_send_draft"].calls == [{"draft_id": "fu1"}]
    assert out["result"]["applications"][0]["update"] == "follow-up 1 sent"
    assert out["result"]["applications"][0]["draft"] == ""                      # nothing waits any more


async def test_a_drafted_reply_waits_on_the_row_and_goes_out_from_it(wired):
    memory, c = await sent_and_applied(wired)
    words = "Can we talk on Friday about the Backend Engineer role?"
    c.update({replies.Reading: reading("interview", words),
              replies.Reply: replies.Reply(body="Thank you for writing. I would be glad to talk. Could you share two or three times that suit you?")})
    t, _ = wired({**inbox_tools(), "gmail_draft": lambda a: {"id": "r1"}}, c)
    out = await run({"memory": memory, "inputs": event({"thread_id": "t1", "from": "Priya <hr@blacksmith.sh>", "subject": "Re: Application", "text": words})})
    row = out["result"]["applications"][0]
    assert row["draft_kind"] == "reply" and "glad to talk" in row["draft"] and row["update"] == "interview: reply drafted"
    assert "send it from the row" in out["result"]["needs_you"][0]
    t, _ = wired(draft_tools(lambda a: {"id": "m3", "threadId": "t1"}), c)
    out = await run({"memory": out["memory"], "inputs": {}, "actions": ["send_draft"], "action_inputs": {"rows": ["1"]}})
    assert t["gmail_send_draft"].calls == [{"draft_id": "r1"}]
    assert store.collection("applications").get("blacksmith.sh/careers/be")["reply_draft"]["sent_at"]
    assert out["result"]["applications"][0]["update"] == "reply sent" and out["result"]["applications"][0]["draft"] == ""


async def test_send_the_draft_refused_or_gone_says_so_and_changes_nothing(wired):
    memory, c = await prepared(wired)
    wired(sending(lambda a: "APPROVAL_REJECTED: no"), c)
    out = await run({"inputs": {}, "memory": memory, "actions": ["send_email"], "action_inputs": {"rows": ["1"]}})
    wired(draft_tools(lambda a: "APPROVAL_REJECTED: the person said no"), c)
    out = await run({"inputs": {}, "memory": out["memory"], "actions": ["send_draft"], "action_inputs": {"rows": ["1"]}})
    assert out["result"]["applications"][0]["update"] == "the application: not approved; it stays in your mailbox"
    assert store.collection("applications").get("blacksmith.sh/careers/be")["stage"] == "draft_kept"
    wired(draft_tools(lambda a: "message.send_draft on gmail: draft not found"), c)
    out = await run({"inputs": {}, "memory": out["memory"], "actions": ["send_draft"], "action_inputs": {"rows": ["1"]}})
    assert out["result"]["applications"][0]["update"].startswith("couldn't send the application")
    assert "probably sent or deleted there" in out["result"]["needs_you"][0]
    out = await run({"inputs": {}, "memory": out["memory"], "actions": ["send_draft"], "action_inputs": {"rows": ["2"]}})
    assert out["result"]["applications"][1]["update"] == "nothing drafted for this one"


async def test_no_memory_read_asks_for_more_rows_than_the_platform_answers(wired, monkeypatch):
    """The first two website runs failed at __start__ with a MemoryError: the gateway caps a find at 500 rows and
    refuses a bigger ask, and route() asked for 5000 before any node ran."""
    asked = []
    real = store._Local.find

    def find(self, where=None, limit=500):
        asked.append(limit)
        return real(self, where, limit)
    monkeypatch.setattr(store._Local, "find", find)
    memory, c = await prepared(wired)
    wired(fu_tools(), c)
    for action in ("report", "follow_ups", "check_replies"):
        await run({"inputs": {}, "memory": memory, "actions": [action]})
    assert asked and max(asked) <= 500
