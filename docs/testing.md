# Testing applyzer

Two layers. The first runs here with no network; the second is a real run against dev with your own accounts, and
you run it.

## 1. Logic and the graph, with fakes

```powershell
cd "D:\PROJECTS\AI AGENTS\JOB SCOUT AGENT"
$env:GRAVITY_RUN_TOKEN="t"; $env:GRAVITY_LLM_URL="http://x"; $env:GRAVITY_GATEWAY_URL="http://x"
.venv\Scripts\python -m pytest -q
```

`tests/test_logic.py` covers the rules: job rows and keys, the resume parser's shape, the GitHub sync, the facts
check, the PDFs, the email checks, the contact order, the send gate, reply matching and sorting, the answer bank,
the follow-up cadence. `tests/test_graph.py` drives the whole graph with fake tools and a fake model: prepare,
paste the posting, send (approved, refused, two roles at one company, a failed check, a personal mailbox), mark
applied, not applying, replies from the mail trigger, follow-ups, the report. `tests/test_copied.py` keeps the
rules of the modules copied from signal-leads pinned here.

`gravity validate` checks the manifest, compiles the lockfile, and checks that the code reads and writes only the
names the manifest declares (`mail`, `page`, `send`, `draft`).

## 2. A real run against dev

The agent is contract v2: the person picks the apps at setup. Locally, `gravity run` picks the defaults (Gmail, Exa)
unless told otherwise, and the agent needs Gravity's SDK, which is not on PyPI:

```powershell
uv pip install -e "<core repo>\packages\gravity-sdk"   # into this folder's .venv
gravity login
gravity connect gmail
gravity connect exa            # or firecrawl / tavily, then add --use page=firecrawl to each run
```

Before a send carries the resume, Gravity's dev gateway needs the gravity-schema change from the gravity-cli branch
`feat/gmail-v2-attachment-thread-draft` (Gmail's send and draft take the file; a draft can sit in a thread; a send
returns its thread). Until then a send goes without the attachment and replies can't be matched by thread.

Under `gravity run`, memory collections are kept in `.gravity/memory/` (`gravity memory show applications`), and
each run's PDFs are written to `.gravity/applications/`. `gravity memory clear` starts over.

Put your resume in a text file (or use `--input resume_file=@cv.pdf` with a PDF path) and two or three job links
in another, one per line: a bare link, or `Company | Role | link | hr@company.com`.

| What | Command | Look at |
|---|---|---|
| Prepare | `gravity run --input jobs=@jobs.txt --input resume_text=@cv.txt --input github_username=<you>` | `applications`: the stage, the contact and how it was found, `fit`, `lines_dropped` (bullets the facts check removed), the email, the PDF in `.gravity/applications/`; `answer_sheet`; `could_not`. Every result has this one table, so row numbers stay the same |
| A LinkedIn link | the same with a `linkedin.com/jobs/view/...` line | its row says "needs the posting's text" |
| Paste the posting | `gravity run --action paste_posting --action-input rows=<n> --action-input text=@posting.txt` | that row is prepared |
| Send | `gravity run --action send_email --action-input rows=1` and answer the approval `y` or `n` | the row's `update`: sent, or kept as a draft and why; with `y` the email in your Sent with the resume attached; with `n` the draft in Gmail. Use a job whose address is yours for a first try |
| I applied | `gravity run --action mark_applied --action-input rows=2` | the row says "applied" |
| Not applying | `gravity run --action not_applying --action-input rows=3` then Prepare again | the row is gone and stays gone |
| Check replies | answer your own test email from another address in the same thread, then `gravity run --action check_replies` | `needs_you` lines first, a reply draft in the thread, the row's `update` |
| Follow-ups | `gravity run --action follow_ups` (a day-4 note needs `sent_at` 4 days back: edit `.gravity/memory/applications.json`) | drafts in the threads; the row's `update` says "you sent it yourself" for a kept draft you sent |
| Weekly report | `gravity run --action report` | counts by stage, this week's sends and replies |

## What is expected to be blank

- `fit` says what the posting asks for that the resume doesn't show; nothing is added to the resume to cover it.
- `lines_dropped` above 0 means the model wrote a line with a number, name or tool the resume doesn't have, and
  code removed it.
- A row with "ready: no address yet" had no address on the line or the posting, and nobody on the company's pages:
  copy the email from the table, or add the address to the job line.
- The cover letter is a separate PDF in `.gravity/applications/`; the email carries the resume only (Gmail sends
  through Gravity take one attachment).

## Not built yet

Discovery (the other half: its code calls `jobs.add(rows, "discovery")`), filling the application form, LinkedIn
and X, calendar slots, the interview prep pack.

## 3. The demo (for us, on a laptop or a phone)

```powershell
cd "D:\PROJECTS\AI AGENTS\JOB SCOUT AGENT"
.venv\Scripts\python demo\server.py
```

It prints two links with a key in them: one for this machine, one for a phone on the same Wi-Fi (Windows asks once
to let Python through the firewall). The real graph runs in that process with a pretend mailbox and sheet, so
nothing is really sent. Four tabs:

- **Live**: the orchestrator, and under it a spine of Prepare, Send and Track. Each subagent appears as a row on the
  spine when it runs, with what it did (the fact checker says how many invented lines it removed). A run plays back a
  beat apart so it can be followed; "Skip ahead" jumps to the end. The buttons below run replies, follow-ups and the
  weekly report; +4 days and +7 days move the clock so follow-ups come due.
- **Jobs**: one card per application with its stage, who it goes to, what fits, Send, "I applied", "Pretend they
  reply…" (drops a company's answer into the inbox the way the mail trigger would), and under "What it wrote" the
  email, the cover letter and the PDFs.
- **Mail**: the pretend Sent, Drafts and Inbox, and the Sheet the tracker fills.
- **Setup**: the jobs, the resume and the answers forms ask for. "Start over" clears everything.

Send opens a sheet from the bottom with the email and its attachment: approve it, or keep it as a draft.

A stand-in model (`demo/rehearsal.py`) answers by default, built from the text it is given. For a real model, any
OpenAI-compatible endpoint (Groq, as APPLYZER used):

```powershell
$env:DEMO_LLM_URL="https://api.groq.com/openai/v1"; $env:DEMO_LLM_KEY="<key>"; $env:DEMO_LLM_MODEL="llama-3.3-70b-versatile"
```

Its state (applications, mailbox, sheet, clock) is in `demo/.state/`. The page was checked in Chrome at iPhone size
with Playwright (`gravity-cli` scratch script), every tab and the approval sheet.
