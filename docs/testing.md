# Testing applyzer

Two layers. The first runs here with no network; the second is a real run against dev with your own accounts, and
you run it.

## 1. Logic and the graph, with fakes

```powershell
cd "D:\PROJECTS\AI AGENTS\JOB SCOUT AGENT"
$env:PYTHONUTF8="1"; $env:GRAVITY_RUN_TOKEN="t"; $env:GRAVITY_LLM_URL="http://x"; $env:GRAVITY_GATEWAY_URL="http://x"
.venv\Scripts\python -m pytest -q          # ours and the search's (tests/scout, Sid's, 119) together: 212
```

`PYTHONUTF8=1` is for Windows: the search's tests read their fixtures without naming an encoding, as the platform
(UTF-8) lets them. `uv pip install pyyaml` once for them.

The tests run on fakes even with the platform SDK installed in `.venv` (it is, for `gravity run`): `tests/conftest.py`
puts it out of the way for every test, so memory is an in-process dict and nothing can reach a gateway.

`tests/test_logic.py` covers the rules: job rows and keys, the resume parser's shape, the GitHub sync, the facts
check, the PDFs, the email checks, the contact order, the send gate, reply matching and sorting, the answer bank,
the follow-up cadence. `tests/test_graph.py` drives the whole graph with fake tools and a fake model: prepare,
paste the posting, send (approved, refused, two roles at one company, a failed check, a personal mailbox), mark
applied, not applying, replies from the mail trigger, follow-ups, the report. `tests/test_copied.py` keeps the
rules of the modules copied from signal-leads pinned here.

`gravity validate` checks the manifest, compiles the lockfile, and checks that the code reads and writes only the
names the manifest declares (`mail`, `page`, `send`, `draft`).

`tests/dump_fixtures.py` is not a test: it runs the graph on the fakes and writes four results (prepared, replied,
follow_ups, report) as JSON, the fixtures of the agent's page in Gravity's web app
(`core/apps/web/app/components/agent-views/applyzer/fixtures`). Refresh them when the result's shape changes:
`FIXTURES_OUT=<that folder> .venv\Scripts\python -m pytest -q tests/dump_fixtures.py`, then run that folder's
`bun test`.

## 2. A real run against dev

The agent is contract v2: the person picks the apps at setup. Locally, `gravity run` picks the defaults (Gmail, Exa)
unless told otherwise, and the agent needs Gravity's SDK, which is not on PyPI:

```powershell
uv pip install -e "<core repo>\packages\gravity-sdk"   # into this folder's .venv
gravity login
gravity connect gmail
gravity connect exa            # or firecrawl / tavily, then add --use page=firecrawl to each run
```

Composio Search (the default page reader and job search) needs no account: nothing to connect. A local run fills
every unpicked source and write with its default app, the optional sheet included, so add `--use tracker=none` to
each run unless you have a sheet to name (`--use tracker=googlesheets:spreadsheet=<id>`); gravity-cli 0.13.9 takes
`none`.

Before a send carries the resume, Gravity's dev gateway needs gravity-schema 0.13.7 (gravity-cli PR #46: Gmail's
send, draft and reply take the file), and 0.13.8 for the mail reads (`filters.folder`: inbox or sent, the same on
Gmail and Outlook) and for the attachment on Outlook. Until then a send goes without the attachment, and a mail
read comes back unnarrowed (the gateway says so in `warnings`; the code picks what it needs out of the records).
To test on Outlook: `gravity connect outlook`, then `--use send=outlook` on each run (mail and draft follow send).

Under `gravity run`, memory collections are kept in `.gravity/memory/` (`gravity memory show applications`), and
each run's PDFs are written to `.gravity/applications/`. `gravity memory clear` starts over.

Put your resume in a text file (or use `--input resume_file=@cv.pdf` with a PDF path) and two or three job links
in another, one per line: a bare link, or `Company | Role | link | hr@company.com`.

| What | Command | Look at |
|---|---|---|
| Find jobs | `gravity run --input resume_text=@cv.txt --input role="AI engineer" --input country=India --input count=5` (nothing pasted: the default run searches; up to 22 minutes) | the board: each job `found` with `fit N: why`, the posting link, verified or not; `could_not` for what the search could not find at the bar |
| Prepare one found job | `gravity run --action pick --action-input rows=1` | the row becomes an application |
| Prepare | `gravity run --input jobs=@jobs.txt --input resume_text=@cv.txt --input github_username=<you>` | `applications`: the stage, the contact and how it was found, `fit`, `lines_dropped` (bullets the facts check removed), the email, the PDF in `.gravity/applications/`; `answer_sheet`; `could_not`. Every result has this one table, so row numbers stay the same |
| A LinkedIn link | the same with a `linkedin.com/jobs/view/...` line | its row says "needs the posting's text" |
| Paste the posting | `gravity run --action paste_posting --action-input rows=<n> --action-input text=@posting.txt` | that row is prepared |
| Send | `gravity run --action send_email --action-input rows=1` and answer the approval `y` or `n` | the row's `update`: sent, or kept as a draft and why; with `y` the email in your Sent with the resume attached; with `n` the draft in Gmail. Use a job whose address is yours for a first try |
| I applied | `gravity run --action mark_applied --action-input rows=2` | the row says "applied" |
| Not applying | `gravity run --action not_applying --action-input rows=3` then Prepare again | the row is gone and stays gone |
| Check replies | answer your own test email from another address in the same thread, then `gravity run --action check_replies` | `needs_you` lines first, a reply draft in the thread, the row's `update`. An ATS mail saying the application moved or is under review shows as `status update` on the row and asks nothing |
| Follow-ups | `gravity run --action follow_ups` (a day-4 note needs `sent_at` 4 days back: edit `.gravity/memory/applications.json`) | drafts in the threads, each adding something the first email didn't say; the row's `update` says "you sent it yourself" for a kept draft you sent |
| Send a kept draft you already sent | send a kept draft yourself from Gmail, then `gravity run --action send_email --action-input rows=<n>` | no approval opens; the row says "not sent again: you sent it yourself" |
| A found person's address | prepare a job whose company has a team page but prints no addresses | the contact says "found by Hunter, valid" (or accept_all / risky), or "nobody found" with why |
| Weekly report | `gravity run --action report` | counts by stage, this week's sends and replies, `by_email_kind` (answered and median days to a reply, application emails against notes), `follow_ups.replies_after_one` |

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
- **Setup**: the role and country to search for, the jobs you already chose, the resume and the answers forms ask for.
  "Start over" clears everything.

"Find jobs" runs the search over Sid's synthetic postings (tests/scout/fixtures), so the demo works offline; a found
job's "Prepare this one" runs the rest for real.

Send opens a sheet from the bottom with the email and its attachment: approve it, or keep it as a draft.

A stand-in model (`demo/rehearsal.py`) answers by default, built from the text it is given. For a real model, any
OpenAI-compatible endpoint (Groq, as APPLYZER used):

```powershell
$env:DEMO_LLM_URL="https://api.groq.com/openai/v1"; $env:DEMO_LLM_KEY="<key>"; $env:DEMO_LLM_MODEL="llama-3.3-70b-versatile"
```

Its state (applications, mailbox, sheet, clock) is in `demo/.state/`. The page was checked in Chrome at iPhone size
with Playwright (`gravity-cli` scratch script), every tab and the approval sheet.
