# applyzer: the apply-and-track half of the job pipeline, as a Gravity agent

Status: built 2026-11-02, contract v2 since the same day. Built by Arush alone, locally, with the Gravity CLI. Not pushed.

## 1. Intent

The job pipeline has nine steps. Steps 1 to 5 (profile, discover, normalise, score, verify) are being written by
someone else. This agent is steps 6, 7 and 9: given jobs the candidate has decided to apply for, tailor the
documents, reach a human at the company, and track what comes back. It ports what works in APPLYZER
(github.com/Kcodess2807/APPLYZER) onto Gravity: documents per job, a reviewed batch, Gmail sends, follow-ups that
stop on a reply.

The discovery code plugs in later through one seam (section 5): it fills the `jobs` rows this agent reads.

## 2. Goals, non-goals, anti-goals

Goals
- For each job: a tailored resume PDF, a cover letter, a contact at the company, and an outreach email, all built
  from the candidate's own facts and nothing else.
- Every send approved by the candidate; never more than the caps.
- Replies tracked from Gmail by thread, sorted into what they are, with the ones that need the candidate shown first.
- Follow-ups drafted on a cadence and stopped by any reply, bounce, or three touches.
- A weekly count of where every application stands.

Non-goals (this version)
- Finding or scoring jobs (someone else), filling the application form (no browser in a run), LinkedIn or X, calendar
  slots, the interview prep pack, the funded-startup pitch lane (that is signal-leads), sending on a schedule.

Anti-goals (a failure even if it "works")
- A resume, letter or email that states a number, employer, date, title or skill the candidate's own resume does not.
- A send nobody approved; two cold emails to the same company; a follow-up after a reply.
- A reply from an address the agent never wrote to being classified or answered.

## 3. Constraints

- Gravity contract v2 (changed from v1 on 2026-11-02; the v1 version is in `.v1/`). The person picks the apps at
  setup. Sources: `mail` (message.search, the same account as `send`) and `page` (web_page.get: Exa, Firecrawl,
  Tavily or Composio Search). Writes: `send` (message.send, under `approvals.required_for`) and `draft`
  (message.create). No Hunter: v2 has no address finder. A scheduled run cannot wait on approval, so ticks draft,
  never send.
- A send or draft carries one file, the tailored resume (`gravity.act(..., file={name, data})`), once Gmail's v2
  mapping takes it (gravity-schema change, see the ledger). A cold note to a found person attaches nothing: the
  pipeline's rule is "no attachment on first touch". A reply
  that asks for the CV gets the tailored PDF attached, re-rendered from the stored resume JSON (a collection item is
  64 KB, so bytes are never stored). The PDF also reaches the candidate as a download for the ATS form.
- A run makes its own PDF with the `typst` wheel (core's job-scout already does this in the sandbox: no TeX). Hosted,
  the bytes travel base64 in the row (job-scout caps this at 400 KB); locally they are also written to `.gravity/`.
- One mailbox (the candidate's own; Gmail by default) for sends, drafts and the reply trigger.
- Default run budget $1: a spent budget drops prose, not the run (same `BUDGET_NOTE` pattern as signal-leads).
- Memory: 10,000 items a collection; few calls a run.

## 4. Approaches

A. **Its own agent, fed a jobs table.** Inputs today: the jobs pasted one per line; later the discovery node writes
   the same rows. The seam is one row shape. Picked: it matches "someone else does discovery, you build the rest",
   builds in Arush's own folder, and ships without touching anyone's agent.
B. Actions bolted onto core's `job-scout` (3,400 lines, someone else's, released through core). One agent for the
   candidate, but not a local build. Not picked.
C. Email-driven (forward a posting to the agent). Hides the table the candidate approves against. Later, maybe.

## 5. The seam: a job row

Collection `jobs`, key = the posting URL's host and path lowercased (no query), else `company:title`.

```
{key, company, title, url, domain, location, description, hr_email, source, found_at}
```

`description`, `hr_email`, `location` may be null: `prepare` fetches the posting and fills what it can. Today the
rows come from the `jobs` input (one per line: a link, or `Company | Title | link | hr@address`). Discovery arrives
as code merged into this graph (assumption from "when we receive the search code, we integrate it": memory
collections are per agent, so a separate agent could not write these rows). Its nodes call one function,
`jobs.add(rows)`, with `source: "discovery"`, and route to `prepare`. Nothing else in the agent knows where a row
came from.

A posting `web.fetch` cannot read (LinkedIn wants a login, Indeed refuses datacenter clients) is not tailored from the
title alone: the row is marked `needs_text` and the candidate pastes the posting with the per-row action
`paste_posting` (input `text`). No Firecrawl fallback in this version.

## 6. What the candidate gives

Inputs: `jobs` (textarea, required), resume as `resume_file` (pdf/docx/txt) or `resume_text` (choose one),
`github_username` (optional), and the answer bank: `notice_days` (number), `current_ctc`, `expected_ctc` (text),
`work_auth` (dropdown: can work there / needs sponsorship / not sure), `relocate` (yes / no / remote only),
`links` (text: LinkedIn, portfolio). The answer bank is used in emails and letters when a posting asks, and is shown
back as the "answer sheet" the candidate pastes into a form.

Profile (notebook): parsed once from the resume with APPLYZER's parser prompt (model, structured), stored with the
resume text's hash; re-parsed when the hash changes. GitHub projects (collection `projects`): public API, README cut
to 3,000 characters, APPLYZER's enrichment prompt plus the rule "only what the README says; no metric that is not in
it"; a repo is re-read only when `pushed_at` moved. Up to 20 repos, most recently pushed first.

## 7. Actions

`prepare` (default, manual). For each job row without documents:
1. `web.fetch` the posting; the model reads it into requirements: must-have skills, nice-to-have, keywords,
   seniority, location, apply link, and any email address on the page. A posting that 404s or says closed is marked
   `closed`.
2. Pick up to 3 projects: APPLYZER's selection prompt, the model's order kept (APPLYZER lost it; a known bug).
3. Tailor the resume: the master profile as JSON goes to the model with the requirements; it may reorder sections and
   bullets, rewrite the summary, choose which projects and bullets appear, and phrase with the posting's own words. It
   may not add anything. Code then checks: every token with a digit, every employer, title, institution and date in
   the output exists in the master text, else that bullet is dropped (job-scout's `resume_check` rule). Typst renders
   `First_Last_Company_Role.pdf`.
4. Cover letter: 180 to 250 words, three paragraphs, from the same facts, same check.
5. Outreach email: under 90 words, one specific fact from the posting or the company page, one ask, no attachment,
   plain text, subject `Application for {title} at {company}` (APPLYZER's). Checked in code with signal-leads'
   `writer.checks` list (banned phrases, no exclamation, ends with the ask) plus the facts check.
6. Contact, in order: `hr_email` on the row; an address on the posting or careers/contact page; a named recruiter,
   talent or hiring person named on the company's team page, at an address those pages print; else none, and the
   email stays a draft the candidate addresses.
7. Write the `applications` row at stage `docs_ready`.

Result: a table, one row per job: company, role, contact (how found, verified or not), what was tailored (the
projects chosen, the skills matched, the gaps), the email text, PDF download, and `could_not` for each degradation.

`send_email` (per row). The signal-leads `draft -> send -> keep` shape: `send` holds only the approved `send`
writes and idempotent writes so an approval resume never redoes work. Caps checked at send time: 20 a day, one per
company domain a day; a personal mailbox (gmail.com sender) still sends, since a candidate writes from their own
address, but the result says so. A refused approval keeps a Gmail draft. Stage `sent` with `thread_id`, `sent_at`.

`mark_applied` (per row, input `applied_on` date, default today). The candidate submitted the form; the application
follow-up clock starts.

`not_applying` (per row). Removes the job and its documents; the job key is remembered so the same posting is not
prepared again. Declining one role never blocks the company: only an opt-out reply suppresses a domain.

Two roles at one company: one cold email per company in 30 days. When both postings are prepared in the same run the
email names both roles; a second posting later gets a draft only, with the reason, and the candidate decides.

`check_replies` (mail trigger `in:inbox is:unread subject:Re -category:promotions`, consumer can change; also
manual; the query drops `subject:Re` since ATS mail starts fresh threads). Matches each message to an application
by `thread_id` first. An application the candidate `mark_applied` also matches mail from the job's domain, or from a
known ATS sender (greenhouse, lever, ashby, workday, smartrecruiters, icims) with the company named in the subject
or body. Mail that matches neither is ignored, never classified.
Rules first (bounce, out of office, auto-acknowledgement), then the model into `interview | oa | offer | rejection |
question | auto_ack | other` with confidence; under 0.7 becomes `other`. `interview`, `oa`, `offer` and `other` are
always listed under `needs_you`, with a drafted reply in the thread for interview and offer (thanks, availability,
within the pipeline's 24-hour rule). `question` is `needs_you` with a drafted reply when the answer bank covers it
(notice, CTC, work authorisation, relocation, links; "send your CV" attaches the PDF). `rejection` gets a drafted
two-line thanks that asks for a referral. A reply
that states the company is not hiring or asks not to be contacted closes the application and adds the domain to
`suppressed`.

`follow_ups` (schedule `0 9 * * *`, drafts only). One clock per application. With an outreach thread: day 4 a
shorter note with one new angle, day 10 two lines, day 21 closed with no email; a `mark_applied` date folds into that
thread (the day-4 or day-10 note mentions the application) and counts toward the three touches. Without a thread
(applied by form only): once, day 7, a draft to `hr_email` when there is one, else a `needs_you` line saying there is
no address to follow up with. Before each draft the thread is fetched and a human reply found there goes to
`needs_you` instead. Stops: any reply, bounce, suppressed, or three touches. A kept draft the candidate sent by hand is found in `in:sent newer_than:30d` and counted as sent.

`report` (schedule `0 9 * * 1`). Counts by stage and by reply kind, sends this week, replies this week.

## 8. Memory

Notebook: profile, resume hash, the day's send count. Collections: `jobs`, `projects`, `applications` (one per job:
stage, contact, the tailored resume as JSON, letter and email text, thread, sent_at, applied_on, follow_ups, replies),
`declined` (job keys), `suppressed` (domains). `prepare` handles at most 5 jobs a run under the $1 budget and says so.

## 9. Reuse

From signal-leads (copied, not imported): `tooling`, `store`, `mail` (message shapes, mailbox type, rule replies,
edit ratio), `writer` (checks, banned list, data-not-instructions note), `stages` (adapted cadence),
`research.read_pages` and `research.contact`. From APPLYZER: the resume-parser prompt, the GitHub enrichment prompt,
the project-selection prompt, the cold-email structure (cut to 90 words), the follow-up stop rules. From core's
job-scout: the Typst resume template and the digit-must-exist resume check.

## 10. Testing

Same two layers as signal-leads. `tests/test_logic.py` for the rules (facts check, job key, classification rules,
cadence, caps), `tests/test_graph.py` driving the graph with fake tools and a fake model (prepare, send approved and
refused, the daily cap, check_replies from the trigger, follow-ups stopping on a reply, not_applying, report). Then
real runs on dev from Arush's terminal with his own Gmail, written up in `docs/testing.md`.

## 11. Left to the builder

Exact prompt wording, the Typst template's look, how long the resume may be (one page under five years' experience,
two above: my default), the `needs_you` ordering, the Exa fallback when the company's team page names nobody.

## 12. Added 2026-11-03

- The resume PDF is laid out like APPLYZER's LaTeX template (`resume.cls`), rendered by Typst (`src/resume_pdf.py`).
- An optional Google Sheet (`writes.tracker`, row.upsert): one row per application, keyed on its link, updated after
  every run with its stage, who it went to, when, follow-ups and the last reply. APPLYZER kept the same sheet through
  the Sheets API.
- `demo/`: a local page (phone-friendly) that runs the graph with a pretend mailbox and sheet, approvals as a sheet you
  tap, a clock to skip ahead, and the orchestrator and its parts lighting up as they run (`docs/testing.md` §3).
