# applyzer

The apply-and-track half of the job pipeline, as a Gravity agent (contract v2). Given jobs a candidate has decided
to apply for, it tailors their resume and cover letter to each one from their own facts, finds someone to write to,
sends only when the candidate approves, then tracks replies and drafts follow-ups in their mailbox.

Finding the jobs (steps 1 to 5 of the pipeline) is not in here. It plugs in through one function: a discovery node
inside this graph calls `jobs.add(rows, "discovery")` with rows of `{company, title, url, hr_email}` and routes to
`prepare`. Nothing else in the agent knows where a row came from. See `docs/spec.md` §5.

## What is here

| Path | What |
|---|---|
| `manifest.yaml` | The agent: inputs, sources (`mail`, `page`), writes (`send`, `draft`, optional `tracker` sheet), actions, memory |
| `src/agent.py` | The graph: prepare, send (draft → send → keep), mark applied, not applying, check replies, follow-ups, report |
| `src/jobs.py` | The seam: job rows, their key, `add()` |
| `src/candidate.py` | The resume read into a profile; GitHub repos read into projects |
| `src/tailor.py` | Requirements from a posting, the tailored resume, the cover letter, the fact check that drops any line the resume can't back |
| `src/outreach.py`, `src/writer.py` | The email (application or short note), its checks, who to write to |
| `src/replies.py`, `src/stages.py` | Reply matching and sorting; the send gate and follow-up cadence |
| `src/resume_pdf.py` | The resume PDF (Typst), laid out like APPLYZER's LaTeX template |
| `demo/` | A local page, phone-friendly, that runs the real graph with a pretend mailbox |
| `docs/` | `spec.md` (design), `plan.md`, `ledger.md` (every decision and deferred issue), `testing.md` (how to test) |

## Run the tests

```powershell
uv venv --python 3.12
uv pip install -r requirements.txt pytest pytest-asyncio
$env:GRAVITY_RUN_TOKEN="t"; $env:GRAVITY_LLM_URL="http://x"; $env:GRAVITY_GATEWAY_URL="http://x"
.venv\Scripts\python -m pytest -q
gravity validate
```

## See it work

```powershell
.venv\Scripts\python demo\server.py
```

It prints a link for this machine and one for a phone on the same Wi-Fi. A stand-in model answers by default;
`docs/testing.md` §3 says how to point it at a real one.

## Status (2026-11-03)

Built and tested with fakes (89 tests), never run against dev. Before a real run:

- gravity-cli PR #46 (gravity-schema 0.12.4) has to be merged, released and on dev's gateway: it lets a v2 Gmail send
  carry the resume, lets a draft sit in a thread, and returns a send's thread so replies can be matched.
- The Gravity SDK is not on PyPI: for `gravity run`, `uv pip install -e <core repo>/packages/gravity-sdk` into `.venv`.

Follow-ups are drafts in Gmail, by decision, until the weekly report shows most go out unchanged. Deferred issues are
at the end of `docs/ledger.md`.
