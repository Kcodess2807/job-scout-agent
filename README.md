# applyzer: the job agent

A Gravity agent (contract v2) that finds live jobs that fit a candidate's resume, or takes the ones they chose, and
does the rest: tailors their resume and cover letter to each posting from their own facts, finds someone to write
to, sends only when the candidate approves, then tracks replies and drafts follow-ups in their mailbox.

Two halves, one graph. The search is Sid's `jobs-scout` (https://github.com/Sid20rathi/Job-scout-agent), vendored
unchanged under `src/scout/` with his tests under `tests/scout/` (`src/scout/VENDORED.md` says how to bring a newer
version over). The rest is a port of Arush's APPLYZER.

The apply-and-track half is a port of [APPLYZER](https://github.com/Kcodess2807/APPLYZER) onto Gravity, keeping what
worked there (the documents per job, the reviewed batch, the application email, the follow-up rules, the sheet) and
dropping its FastAPI, Postgres, LaTeX and Apps Script layers, which Gravity's memory, approvals, schedules and mail
trigger replace.

**Where this sits in the pipeline.** Of the pipeline's nine steps, this agent does 1 to 7 and 9: profile, discover,
normalise, score, verify (the search), then tailor, reach out and track. Step 8, the application form, is out: a run
has no browser, so the agent gives the candidate an answer sheet to paste instead.

**Status (2026-11-03).** Built, 212 tests passing with fakes (93 ours, 119 the search's), a demo that runs the real
graph on a laptop or a phone. Never run against dev. See [What is not done](#what-is-not-done).

---

## How it works

One LangGraph graph (`src/agent.py`), routed by the action the person pressed or by a mail-trigger event. Models read
pages into fields and write prose. Code decides everything else: what runs, what is sent, what counts as a fact.

### Actions

| Action | What happens |
|---|---|
| **Find jobs** (`discover`; the default run when nothing is pasted) | The search: resume to profile, the person's role, country, city, remote and count to search preferences, then boards and careers pages through the `search` source, each posting opened through `page` to check it is open, scored against the resume (threshold 70), deduplicated, ranked. The qualified jobs land on the board as `found`, with the fit score, why, and the posting link. Up to 22 minutes; stops early once it has enough. |
| **Prepare this one** (`pick`, per row) | Marks a found job picked and prepares it. |
| **Prepare** (default) | Reads the resume once into a profile (re-read only when the text changes). Reads each posting through the person's page reader. Picks up to three of the candidate's GitHub projects for it. Tailors the resume: the model may reorder, select and reword, never add; code then drops any line whose numbers, employers, dates, tools or proper nouns are not in the resume. Writes a cover letter (180 to 250 words) and an email. Finds a contact: the address that came with the job, else a recruiter or hiring person named on the company's own pages, at the address those pages print or, failing that, the one Hunter finds for them (Gravity's `hunter.find_email` and `hunter.verify_email` built-ins; an invalid address is dropped). At most 5 jobs a run, inside the run's budget. |
| **Send** (per row) | Opens an approval for each email. An address that came with the job (on the job line or printed on the posting) gets APPLYZER's application email, three bullets of proof, with the tailored resume attached. A person found on the company's own pages gets a short note under 90 words with no attachment. Caps: 20 sends a day, one email per company in 30 days; two roles at one company share one email. A refused send is kept as a Gmail draft. Pressing Send on a kept draft first looks in Sent: one the candidate already sent from Gmail is recorded as theirs and never sent again. |
| **Send the draft** (per row) | Every draft the agent keeps for a job (a reply to their last mail, a follow-up, the application itself after a refused send) waits on the row, text and all. This sends it through the person's own mailbox as they left it, after the same approval as Send, so tracking never needs a trip to Gmail or Outlook. Edit it in the mailbox first if you want; what is sent is what is there. |
| **I applied** (per row) | The candidate submitted the form; the follow-up clock starts. |
| **Not applying** (per row) | Removes the job for good; a shared email is rewritten for the roles left. |
| **Paste the posting** (per row) | For a page the reader could not open (LinkedIn's sign-in wall, Indeed): the candidate pastes the text and it is prepared. |
| **Check replies** | Started by the mail trigger when a reply lands, or by hand. Matches mail by thread; for an applied job also by the company's domain or a known ATS sender naming the company. Anything else is ignored, never classified. Rules first (bounce, out of office, auto-acknowledgement, opt-out), then the model: interview, online test, offer, rejection, question, status update (the ATS says the application moved or is under review: recorded on the row, nothing asked of the candidate), other. Interview and offer get a drafted reply in the thread, shown on the row and sendable from it; a recruiter's question is answered from the candidate's own answers when they cover it ("send your CV" attaches the resume); a rejection gets a two-line thanks with a referral ask. An opt-out suppresses the company. |
| **Follow-ups** (daily, 09:00) | Drafts only, by decision (below). Application email: day 7 and 14. Short note: day 4 and 10. Closed at 21 with no email. A form application with no thread: one note at day 7, only to an address that came with the job. Before each draft the thread is checked: anyone else's message since the send stops it. Drafts the candidate sent by hand are found in Sent and measured for how much they were changed. Each follow-up is given what was already written to that company, so it adds one new thing instead of repeating the first email. |
| **Weekly report** (Monday 09:00) | Counts by stage, sends and replies this week; over all time, how many application emails and how many notes to a person were answered and the median days to a reply; how many follow-up drafts the candidate sent, how many unchanged, and how many replies came after one. |

Every result shows one table, the applications, in a fixed order, with an `update` column for what that run did to
each row. Per-row buttons act on that table's row numbers, so the order never changes between results.

### The seam for discovery

Collection `jobs`, one row per posting, keyed on the posting URL's host and path (no query), else `company:title`:

```
{key, company, title, url, domain, location, description, hr_email, source, found_at, order}
```

Rows come from the `jobs` input (one per line: a link, or `Company | Role | link | hr@company.com`), picked at once,
or from the search (`src/discover.py` turns his `ScoutOutcome` into rows and calls `jobs.add(rows, "discovery")`),
unpicked until the person says "Prepare this one". Nothing past the seam knows where a row came from. A declined
posting never comes back from either.

### Memory

Notebook: the profile, the resume's hash, the day's send count, the table's row order. Collections: `jobs`,
`projects` (GitHub repos, re-read only when pushed), `applications` (one per job: stage, contact, the tailored resume
as JSON, letter and email text, thread, sent_at, applied_on, follow-up drafts, replies), `declined`, `suppressed`.
PDFs are never stored; they are re-rendered from the JSON when needed.

### The contract v2 manifest

| Name | Capability | Default app | Picked by the person at setup |
|---|---|---|---|
| source `mail` | `message.list`, 30-day window, the mailbox (inbox, sent) named per read | same account as `send` | |
| source `page` | `web_page.get` | Composio Search | Exa, Firecrawl, Tavily or Composio Search |
| source `search` | `web_result.search` | Composio Search | any web search |
| sources `search_more`, `answers`, `similar` (optional) | a second search, an AI web search, "more like this" | Exa, Composio Search | skipped quietly when not set up |
| write `send` | `message.send`, under approvals | Gmail | Gmail or Outlook: the code never names either |
| write `draft` | `message.create` | same as `send` | |
| write `tracker` (optional) | `row.upsert` | Google Sheets | their own spreadsheet, or Notion |

The optional sheet gets one row per application, keyed on the posting link, updated after every run: APPLYZER's
columns plus company, role, the reply and the last reply. A person who sets none up loses nothing: the applications
table in every result is the record.

---

## How it shows on Gravity

The explore page is drawn from the manifest and the result, not from anything the agent ships: the card is the name,
the description and the apps' logos; the agent's page splits the description into steps (each clause that starts
with a verb), lists the inputs by their labels, and draws a run's result by its shape. The result carries a
`_layout` (`src/agent.py`, `LAYOUT`) the page honours: one card per job, the stage as a chip, what the run did to it
in accent, the fit, who it goes to, the email and the cover letter readable before a send is approved, and a button
to the posting. Older pages ignore `_layout` and draw the rows as a table.

## What is in the repo

| Path | What |
|---|---|
| `manifest.yaml` | The agent |
| `src/agent.py` | The graph and every node |
| `src/jobs.py` | Job rows, their key, `add()` |
| `src/discover.py` | The search as one call: his resume reader, profile, preferences and `engine.scout`, over this agent's sources; the rows for the seam |
| `src/scout/` | Sid's search, vendored unchanged: providers for 20-odd boards and ATS systems, transport with budgets and backoff, normalising, verifying, dedupe, eligibility, scoring, ranking |
| `src/candidate.py` | The resume read into a profile (APPLYZER's parser prompt); GitHub repos read into projects (APPLYZER's enrichment prompt, plus a "README only" rule) |
| `src/tailor.py` | Requirements from a posting, the project pick (APPLYZER's prompt, order kept), the tailored resume, the facts check, the cover letter, the PDFs |
| `src/resume_pdf.py` | The resume laid out like APPLYZER's `resume.cls`, rendered with Typst (TeX can't run in the sandbox) |
| `src/outreach.py`, `src/writer.py` | Who to write to; the application email and the short note; the checks (length, claims, links, banned phrases); APPLYZER's follow-up prompt |
| `src/replies.py`, `src/stages.py` | Reply matching and sorting; the answer bank; the send gate and the follow-up cadence |
| `src/mail.py`, `src/research.py`, `src/evidence.py`, `src/store.py`, `src/tooling.py` | Mail records (Gmail, the trigger's and v2's shapes), page reading and the contact finder, the quote check, memory (with a local file store under `gravity run`), the v2 calls and the model |
| `tests/` | `test_logic.py` (the rules), `test_graph.py` (the whole graph with fake tools and a fake model, the search included over his fixture postings), `test_copied.py` (rules carried from the sibling agent), `tests/scout/` (his 119) |
| `demo/` | `server.py` (the real graph with a pretend mailbox, approvals you tap, a clock you can skip ahead, a live view of the orchestrator and its parts), `index.html`, `rehearsal.py` (a stand-in model), `samples.py` |
| `docs/spec.md` | The design: intent, goals, constraints, approaches, every action, the seam |
| `docs/plan.md` | The build plan, ticked |
| `docs/ledger.md` | Every decision made while building, with why and what it costs if wrong; the review findings and what was fixed; the deferred minor issues |
| `docs/testing.md` | How to test: the fakes, a real run on dev, the demo |

---

## Run it

Tests (no network):

```powershell
uv venv --python 3.12
uv pip install -r requirements.txt pytest pytest-asyncio pyyaml
$env:PYTHONUTF8="1"; $env:GRAVITY_RUN_TOKEN="t"; $env:GRAVITY_LLM_URL="http://x"; $env:GRAVITY_GATEWAY_URL="http://x"
.venv\Scripts\python -m pytest -q
gravity validate
```

The demo:

```powershell
.venv\Scripts\python demo\server.py
```

It prints a link for this machine and one for a phone on the same Wi-Fi. Four tabs: Live (the orchestrator and a
spine of Find, Prepare, Send and Track, each part lighting up as it runs), Jobs, Mail (a pretend Sent, Drafts, Inbox
and Sheet), Setup. "Find jobs" searches Sid's synthetic postings, so the demo works offline. A stand-in model answers by default; `docs/testing.md` §3 says how to point it at a real one (any
OpenAI-compatible endpoint).

A real run against dev: `docs/testing.md` §2. Short version: `gravity login`, `gravity connect gmail`, install the
Gravity SDK from the core repo into `.venv` (it is not on PyPI), then `gravity run --input jobs=@jobs.txt --input
resume_text=@cv.txt`. For a first send use a job line whose address is your own.

---

## Decisions already made

All in `docs/ledger.md` with the reasoning. The ones a teammate should know:

- **Nothing is sent without the candidate's yes.** `message.send` is under approvals. Scheduled runs cannot wait on
  one, so the daily follow-up run drafts and never sends.
- **Follow-ups stay drafts for now** (Arush, 2026-11-03). The weekly report counts how many drafts the candidate
  sent unchanged; when most do, follow-ups switch to sending on their own.
- **No invented claims.** Every generated line is checked against the resume's own text; a line that fails is
  dropped and the row says how many were. APPLYZER's resume generator filled gaps with made-up projects and numbers;
  none of that came over.
- **One email per company.** Two roles prepared together share one email naming both; a later role at the same
  company within 30 days is drafted, not sent.
- **No Hunter.** Contract v2 has no address finder, so a found person needs an address printed on the company's own
  pages. Expect "no address yet" rows until that is solved (a built-in in the gateway, or Apollo, now a v2 built-in).
- **The resume PDF follows APPLYZER's LaTeX template**, rendered with Typst: capitals, ruled headings, Objective,
  Skills as a two-column table after Education.
- **The sheet is a mirror, not the record.** Gravity's per-user memory is the system of record; the sheet is
  optional and per person.

## What is not done

- **No run against dev yet.** Everything is tested with fakes and the demo's stand-in model. The first real run will
  find what fakes can't: Gmail's real records, run time, whether the mail trigger, per-row buttons and memory
  collections behave for a v2 agent on dev, and the sheet's first write into an empty spreadsheet.
- **Two things need the platform.** gravity-schema 0.13.7 (gravity-cli PR #46, released 2026-11-03) lets a v2 Gmail
  send, draft or reply carry a file; 0.13.8 (gravity-cli PR, 2026-11-03) adds the `folder` filter the mail reads use
  (inbox, sent: the same words on Gmail and Outlook) and the attachment on Outlook. Dev's gateway has to pick them
  up; until then a send goes without the resume and a mail read is not narrowed to its mailbox.
- **The real model has never run the prompts.** Read ten tailored resumes and letters before trusting them.
- **The search has its own unknowns**, in his README's "Known limitations": no live run, web search as the only
  discovery (no job API in v2), LinkedIn and Indeed pages unreadable from a datacenter, an uncalibrated score.
- **Not built, by design:** the application form, LinkedIn and X, calendar slots, the interview prep pack, project
  ideas with a starter repo, the funded-startup lane.
- **Eight deferred minor issues** at the end of `docs/ledger.md`, for example: a two-role email attaches only the
  clicked row's resume; "closed to new applicants after 30 Nov" on a live posting reads as closed.
- **Hunter has not run.** The built-ins are declared and the answer is read tolerantly (the first `email` and
  `status` anywhere in it); the first real Prepare of a job with a team page shows whether the shape matches.

## History

Built 2026-11-02 and 03 from a written spec (`docs/spec.md`), test first, with one fresh-eyes review (two critical
and seven important findings, all fixed) and a second pass after moving from contract v1 to v2. The search joined on
2026-11-03 from Sid's repo at commit 1753f02. The v1 version is not in this repo.
