# applyzer version one: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline). Steps use checkbox syntax.
> This folder is not a git repository (same as signal-leads): there are no commit steps. Nothing is built or pushed
> with `gravity build`/`gravity push`; `gravity validate` and `gravity run` only.

**Goal:** The apply-and-track agent of `docs/spec.md`: tailored documents per job, approved outreach, replies and
follow-ups tracked from Gmail, with a one-function seam for the discovery code that arrives later.

**Architecture:** One LangGraph graph (`src/agent.py`) routed by action or by a mail-trigger event. Pure rules in
small modules; models read pages into fields and write prose; code decides, checks facts and sends. Memory through
`store`; tools and the model through `tooling`. Five modules are copied from signal-leads unchanged.

**Tech stack:** Python 3.12, LangGraph, pydantic, httpx, rapidfuzz, typst, pypdf, python-docx, pytest (asyncio auto).

**Spec:** `docs/spec.md`

## Global constraints

- Contract v1; `async def graph()` returns a compiled graph; environment read inside it.
- Every tool called is in `permissions.tools`; `gmail.send` in `approvals.required_for`; sends only in person-started
  runs; scheduled runs draft only.
- No claim without a source in the master resume: every digit token, employer, title, institution and date in a
  generated bullet must exist in the master text, else the bullet is dropped.
- Outreach under 90 words, one ask, no attachment. Caps: 20 sends a day; one cold email per company in 30 days.
- Replies match by `thread_id`, or for an applied job by the company domain or a known ATS sender naming the company;
  nothing else is classified.
- A collection item is under 64 KB: resume JSON is stored, PDF bytes never.
- `prepare` handles at most 5 jobs a run. Few memory calls a run: one `find()` per collection, `has_many` for keys.
- Tests first: failing test, watch it fail, make it pass, run the suite.

## Review focus

1. A posting page whose text is a login wall (LinkedIn) must become `needs_text`, never a resume tailored from the
   title. (Task 6)
2. A master resume with no numbers at all: the facts check must not drop every bullet; a bullet with no digits and
   known names passes. (Task 4)
3. Two jobs at the same company prepared in one run must produce one email naming both roles, one send, one thread.
   (Task 7)
4. An ATS rejection from `no-reply@greenhouse.io` for a job marked applied must be matched and classified; the same
   mail for a job never applied to must be ignored. (Task 8)
5. A candidate who sent a kept draft by hand must not get a day-4 follow-up drafted on top of a thread that already
   has a reply. (Task 9)

---

## Task 1: Scaffold, copied modules, manifest

**Files:** `manifest.yaml`, `requirements.txt`, `pytest.ini`, `src/{tooling,store,mail,writer,evidence}.py` (copied
from `D:\PROJECTS\AI AGENTS\LEAD GENERATION AGENT\src`), `src/research.py` (copied, keep only `domain`, `results`,
`hits`, `read_pages`, `is_company_host`, `profile_person`, `contact` and what they import), `tests/conftest.py`
(copied), `tests/test_graph.py` (copied `Tool`, `FakeModel`, `wired` fixture; drop signal-leads' tests).

- [x] Copy the files; `python -c "import src.research, src.writer"` from the folder imports clean.
- [x] Write `manifest.yaml` from spec sections 3, 6, 7: name `applyzer`, version 0.1.0, kind interactive, timeout
      900. Inputs: `jobs` (textarea, required), `resume_file` (file: pdf, docx, txt), `resume_text` (textarea),
      `github_username` (text), `notice_days` (number), `current_ctc` (text), `expected_ctc` (text), `work_auth`
      (static-dropdown: "can work there", "needs sponsorship", "not sure"; default "can work there"), `relocate`
      (static-dropdown: yes, no, "remote only"), `links` (text). `choose_one: [resume_file, resume_text]`. Tools:
      gmail.get_profile, gmail.read, gmail.fetch_message_by_thread_id, gmail.draft, gmail.send, web.fetch, web.search,
      exa.search, hunter.find_email, hunter.verify_email. `approvals.required_for: [gmail.send]`. Triggers: manual, and
      `email: {query: "in:inbox is:unread -category:promotions", consumer_can_change: true}`. Actions: `prepare`
      (default), `paste_posting` (per_row, input `text` textarea), `send_email` (per_row), `mark_applied` (per_row,
      input `applied_on` date), `not_applying` (per_row), `check_replies`, `follow_ups` (schedule `0 9 * * *`),
      `report` (schedule `0 9 * * 1`). Memory: notebook 64 KB; collections jobs, projects, applications, declined,
      suppressed.
- [x] `requirements.txt`: langgraph, langchain-openai, langchain-mcp-adapters, httpx, pydantic, rapidfuzz, typst,
      pypdf, python-docx. Create `.venv`, install plus pytest and pytest-asyncio.
- [x] Test: `test_graph_compiles` in `tests/test_graph.py` builds `agent.graph()` with the fakes. Run: fails (no
      `src/agent.py`). Write `src/agent.py` with `State`, `route()`, and one `prepare` node that returns an empty
      result. Run: passes. `gravity validate` passes.

## Task 2: Job rows and the seam

**Files:** `src/jobs.py`, `tests/test_logic.py`

**Produces:** `parse_lines(text: str) -> list[dict]` (one row per non-blank line: a bare URL, or `Company | Title |
url | hr@x` with missing parts None); `job_key(row: dict) -> str` (URL host+path lowercased without query, else
`company:title` slug); `company_domain(row) -> str | None` (the URL host for a company site; for an ATS host
(greenhouse, lever, ashby, workday, smartrecruiters, icims, linkedin, indeed) None, filled later from the page);
`async add(rows: list[dict], source: str) -> list[dict]` upserting into the `jobs` collection with `found_at`, the
function the discovery code calls.

- [x] Tests: a bare link row; a four-part row; the key ignores `?utm=`; `boards.greenhouse.io/acme/jobs/1` has no
      company domain; `acme.com/careers/x` has `acme.com`; `add()` writes each row once under its key.
- [x] Run: fail. Implement. Run: pass.

## Task 3: Profile and projects

**Files:** `src/profile.py`, `tests/test_logic.py`, `tests/test_graph.py`

**Produces:** `async resume_text(call, file_value: str | None, pasted: str | None) -> tuple[str, str]` (text,
source; pdf via pypdf, docx via python-docx, a file link fetched with `web.fetch`); `Profile` pydantic schema (name,
email, phone, location, links, summary, experience_years, skills: list[str], education: list[{degree, institution,
year}], experience: list[{title, organisation, dates, bullets}], projects: list[{title, bullets, tech}]);
`PARSER_SYSTEM` (APPLYZER `app/services/resume_parser_service.py:74-125`, rules kept, output as `Profile`);
`async parse(model, text) -> Profile | None`; `async github_projects(call, username: str, known: dict) -> list[dict]`
(public API `users/{u}/repos?sort=pushed&per_page=20`, README to 3,000 chars, APPLYZER's enrichment prompt
`app/services/project_service.py:121-140` plus the line "Use only what the README states. No metric, user count or
result that is not written there."; a repo whose `pushed_at` equals `known[name]["pushed_at"]` is skipped);
`master_text(profile: Profile) -> str` (every fact flattened, the text the facts check runs against).

- [x] Tests (logic): `master_text` contains every bullet, employer and date; a repo with unchanged `pushed_at` is
      not fetched (fake `call` records URLs).
- [x] Test (graph): a `prepare` run parses the pasted resume once and stores `profile` and `resume_hash` in the
      notebook; a second run with the same text never calls the parser (FakeModel call count).
- [x] Run: fail. Implement. Run: pass.

## Task 4: Tailoring: requirements, facts check, resume, letter, PDF

**Files:** `src/tailor.py`, `tests/test_logic.py`

**Produces:** `Requirements` schema (must_have, nice_to_have, keywords, seniority, location, apply_url, emails:
list[str], closed: bool); `async requirements(model, page_text) -> Requirements | None`; `is_login_wall(page_text)
-> bool` (under 400 usable characters, or "sign in" / "join now" / "verify you are human" dominating); `ResumeDoc`
schema (name, contact_line, summary, sections: list[{heading, entries: list[{title, organisation, dates, bullets}]}],
skills_line; same shape as job-scout `writer.ResumeRewrite`); `facts_ok(line: str, master: str) -> bool` (every
token containing a digit appears in the master, normalised like `evidence.normalise`; every capitalised run of two
or more words appears in the master); `grounded(doc: ResumeDoc, master: str) -> tuple[ResumeDoc, int]` (bullets and
summary sentences failing `facts_ok` dropped, count returned); `async tailor_resume(model, profile, req, projects)
-> ResumeDoc | None` with the system prompt from job-scout `writer.RESUME_SYSTEM` plus "Reorder and select for the
posting below; use its words where the original shows the skill in use"; `pdf_bytes(doc: ResumeDoc) -> bytes` (the
Typst template from `core/agents/job-scout/src/resume_pdf.py` verbatim); `pdf_name(profile, company, title) -> str`
(`First_Last_Company_Role.pdf`, non-alphanumerics dropped); `async cover_letter(model, profile, req, company) -> str
| None` (180 to 250 words, three paragraphs, `DATA_NOTE`), passed through `grounded` line by line.

- [x] Tests: `facts_ok("Cut p95 latency 40% at Acme", master_with_40)` True; with `45%` False; a bullet with no
      digits and no proper nouns passes (review focus 2); `grounded` drops only the bad bullet and counts 1;
      `is_login_wall` on a LinkedIn "Sign in to view" stub True and on a 2,000-character posting False; `pdf_bytes`
      starts with `%PDF`; `pdf_name` for "Aditya Rao", "Blacksmith", "Backend Engineer (Go)" is
      `Aditya_Rao_Blacksmith_Backend_Engineer_Go.pdf`.
- [x] Run: fail. Implement. Run: pass.

## Task 5: Outreach email and contact

**Files:** `src/outreach.py`, `src/writer.py` (add `APPLY_SYSTEM`, `email_checks`), `tests/test_logic.py`

**Produces:** `APPLY_SYSTEM`: APPLYZER's cold-email structure (`app/services/cold_dm_generator.py:63-88`) cut to:
one line who you are, one specific fact from the posting or company page, two short lines of proof from the profile,
one ask, under 90 words, plain text, no subject, no sign-off, `DATA_NOTE`; `email_checks(body: str, master: str,
roles: list[str]) -> list[str]` (signal-leads `writer.checks` plus: over 90 words; no question or ask in the last
line; a fact failing `facts_ok`; the word "attached"); `async first_email(model, profile, req, company, roles,
page_fact) -> tuple[str, list[str]]` (one rewrite on failures, as signal-leads); `subject(roles, company) -> str`
(`Application for {role} at {company}`; two roles joined with " / "); `async find_contact(call, model, row, req,
company) -> dict` in the order of spec 7.6: row `hr_email` → `req.emails` (first that is not noreply) → signal-leads
`research.contact` with personas `["recruiter", "talent", "people", "hiring manager", "founder"]` → `{}`; the result
carries `email, name, how` (`given | on posting | team page + hunter | none`), `verified`.

- [x] Tests: a 95-word body fails with "over 90 words"; a body ending without an ask fails; "resume attached" fails;
      `subject(["Backend Engineer", "SRE"], "Acme")` is `Application for Backend Engineer / SRE at Acme`;
      `find_contact` returns the row's address first and never calls Hunter then; with a `noreply@` on the page it
      falls through to the team page.
- [x] Run: fail. Implement. Run: pass.

## Task 6: The `prepare` node and `paste_posting`

**Files:** `src/agent.py`, `tests/test_graph.py`

**Consumes:** Tasks 2 to 5. **Produces:** `applications` rows `{key, company, title, url, domain, stage, contact,
resume: ResumeDoc dict, letter, email: {subject, body, problems}, projects: [names], matched: [skills], gaps:
[skills], prepared_at}`; result `{prepared: [rows for the UI with pdf_b64 under 400 KB, pdf_name, contact line,
projects, matched, gaps, email, answer_sheet], needs_text: [...], closed: [...], could_not: [...]}`, where
`answer_sheet` is the bank (notice, CTC, work auth, relocation, links) plus the posting's apply link, for the form;
locally each PDF is also written to `.gravity/<pdf_name>`.

- [x] Tests: two pasted links → two `applications` rows at `docs_ready`, one `gmail`-free run (no gmail tool
      called), each with a PDF and an email that passes checks; a login-wall page → `needs_text`, no model tailoring
      call for that row (review focus 1); a 404 → `closed`; 7 links → 5 prepared and `could_not` says why; a row
      already prepared is skipped; a row in `declined` is skipped; `paste_posting` on a `needs_text` row with
      `text` prepares it.
- [x] Run: fail. Implement `prepare` as: load profile (Task 3) → for each job row without an application, up to 5:
      fetch → requirements → projects pick (APPLYZER `app/services/ai_service.py:103-133`, order kept) → tailor →
      grounded → PDF → letter → email → contact → put. Degradations into `could_not` with the row named.
- [x] Run: pass. Full suite green.

## Task 7: `send_email`, `mark_applied`, `not_applying`

**Files:** `src/agent.py`, `src/stages.py` (new: `DAILY_CAP = 20`, `COMPANY_DAYS = 30`, `can_send(app, apps_today,
same_company_sent_at, now) -> str | None` returning the block reason or None), `tests/test_graph.py`,
`tests/test_logic.py`

- [x] Tests (logic): the 21st send of the day is blocked "daily cap"; a company emailed 10 days ago blocks "emailed
      this company 10 days ago"; 31 days ago does not.
- [x] Tests (graph): `send_email` on row 1 with approval `y` calls `gmail_send` once with the subject, body and no
      attachment, stage `sent` with `thread_id` and `sent_at`; a refused approval makes a `gmail_draft` and stage
      `draft_kept`; a resume of the run after approval never calls the model again (Tool LOG); two prepared rows at
      the same company sent together → one `gmail_send` whose subject names both roles and both rows share the
      `thread_id` (review focus 3); a later third role at that company → draft only with the reason; `mark_applied`
      sets `applied_on` (default today); `not_applying` removes the application and adds the key to `declined`; a gmail.com sender still sends and the
      result's row says "sent from a personal address".
- [x] Run: fail. Implement with signal-leads' `draft -> send -> keep` split: `draft` resolves everything, `send`
      holds only gated calls and idempotent writes, `keep` writes drafts and the result.
- [x] Run: pass.

## Task 8: Replies

**Files:** `src/replies.py`, `src/agent.py`, `tests/test_logic.py`, `tests/test_graph.py`

**Produces:** `ATS_HOSTS` (greenhouse.io, lever.co, ashbyhq.com, myworkday.com, smartrecruiters.com, icims.com);
`match(msg: dict, apps: list[dict]) -> dict | None` (thread id; else for an app with `applied_on`: sender domain
equals app domain, or sender domain in `ATS_HOSTS` and the company name in subject or body); `Reading` schema (kind
in interview, oa, offer, rejection, question, auto_ack, other; confidence; their_words; question_text); `rule_kind
(msg) -> str | None` (signal-leads `mail.rule_reply` plus auto-acknowledgement: "we have received your application",
"thank you for applying"); `async classify(model, msg, app) -> Reading`; `answer_from_bank(question_text, inputs) ->
str | None` (notice, CTC, work auth, relocation, links; None when the bank has nothing); `async reply_text(model,
kind, app, profile, answer) -> tuple[str, list[str]]` (interview/offer: thanks and availability; rejection: two lines
with a referral ask; question: the bank's answer; checked with `writer.checks` and own-domain links only).

- [x] Tests (logic): `match` by thread; a greenhouse sender naming the company matches an applied app and not an
      unapplied one (review focus 4); "we have received your application" is `auto_ack` by rule; a bounce is
      `bounce`; `answer_from_bank("What is your notice period?", {notice_days: 30})` mentions 30 days.
- [x] Tests (graph): a trigger run with one interview reply → `needs_you` has it first, a `gmail_draft` reply in the
      thread, stage `replied:interview`; an opt-out reply → `suppressed` has the domain, stage `closed`; a "please
      send your CV" question → the draft carries one PDF attachment named `pdf_name`; mail from an unknown thread and
      sender → nothing written, nothing drafted; a trigger run never runs `prepare`.
- [x] Run: fail. Implement `check_replies`. Run: pass.

## Task 9: Follow-ups and the report

**Files:** `src/stages.py`, `src/agent.py`, `tests/test_logic.py`, `tests/test_graph.py`

**Produces:** `OUTREACH = (4, 10)`, `CLOSE_DAY = 21`, `APPLIED_DAY = 7`, `MAX_TOUCHES = 3`; `due(app, now) ->
str | None` returning `"outreach_1" | "outreach_2" | "close" | "applied"` by spec 7 `follow_ups`: with a thread the
outreach clock, an `applied_on` folded in (no separate note); without a thread `applied` once at day 7; None when
replied, bounced, suppressed, closed or three touches; `async follow_up_text(model, which, app, profile) ->
tuple[str, list[str]]` (day 4: shorter, one new angle from a different project; day 10: two lines).

- [x] Tests (logic): sent 4 days ago → `outreach_1`; 10 days with one follow-up → `outreach_2`; 21 days → `close`;
      applied by form 7 days ago, no thread → `applied`; replied → None; both a thread and `applied_on` → only the
      outreach clock.
- [x] Tests (graph): a scheduled `follow_ups` run drafts the day-4 note in the thread and never calls `gmail_send`; a
      thread whose pre-flight fetch shows a human reply gets no draft and a `needs_you` line (review focus 5); a kept
      draft found in `in:sent newer_than:30d` becomes `sent` with `sent_at` from the message; `report` returns counts
      by stage and by reply kind and this week's sends and replies.
- [x] Run: fail. Implement `follow_ups` and `report`. Run: pass. Full suite green. `gravity validate` passes.

## Task 10: Hand over for the real run

- [x] Write `docs/testing.md` as signal-leads' (the pytest layer; `gravity login`, `gravity connect gmail`; the
      `gravity run` command per action with what to look at; what is expected to be blank; not built yet: discovery,
      the form, LinkedIn, calendar, prep pack).
- [x] Stop. Arush runs it against dev from his own terminal.
