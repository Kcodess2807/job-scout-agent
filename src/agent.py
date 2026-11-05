"""applyzer: the apply-and-track half of the job pipeline. Given jobs the candidate chose, it tailors a resume and
cover letter to each from the candidate's own facts, finds someone to write to, sends on approval, and tracks
replies and follow-ups in Gmail. Code decides what runs, what is sent and what is true; models read pages into
fields and write prose.

Actions (manifest): prepare (default), paste_posting, send_email, mark_applied, not_applying (per row),
check_replies (also the mail trigger), follow_ups (daily, drafts only), report (weekly)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, StateGraph

import candidate
import discover as finder  # puts src/scout (the vendored search) on the path
import jobs
import mail
import outreach
import replies
import stages
import store
import tailor
import writer
from candidate import Profile
from tooling import make_embedder, make_io, make_model

PER_RUN = 5            # jobs prepared a run, under the $1 run budget
PDF_B64_MAX = 400_000  # a bigger PDF is not put in the result (job-scout's cap)
STAGES = {"found": "found", "to_prepare": "to prepare", "needs_text": "needs the posting's text", "closed": "closed", "docs_ready": "ready to send",
          "draft_kept": "draft kept", "sent": "emailed", "applied": "applied", "replied": "replied",
          "done": "closed"}


class State(TypedDict, total=False):
    inputs: dict
    memory: dict
    actions: list
    action: Any
    action_inputs: dict
    focus: list      # paste_posting -> prepare: the keys to prepare first
    emails: list     # draft -> send -> keep: one entry per email
    sent_today: int  # draft -> keep: sends already made today
    result: Any


def utcnow() -> datetime:
    return datetime.now(UTC)


def picked_keys(state: State) -> list[str]:
    """The applications behind the rows the person selected (numbers against the table every result shows), or, when
    they typed instead, the rows their message names: the row's title and company both in the text, as the page
    writes a row ("Backend Engineer · Blacksmith")."""
    last = (state.get("memory") or {}).get("last_rows") or []
    rows = [int(r) for r in (state.get("action_inputs") or {}).get("rows") or [] if str(r).isdigit()]
    if rows:
        return [last[r - 1] for r in rows if 0 < r <= len(last)]
    said = ((state.get("inputs") or {}).get("message") or "").lower()
    if not said.strip():
        return []
    jobs, apps = store.collection("jobs"), store.collection("applications")

    def named(k: str) -> bool:
        j = jobs.get(k) or apps.get(k) or {}
        title, company = (j.get("title") or "").lower(), (j.get("company") or "").lower()
        return bool(title and company and title in said and company in said)
    return [k for k in last if named(k)]


def contact_line(c: dict) -> str:
    if c.get("email"):
        who = ", ".join(x for x in (c.get("name"), c.get("title")) if x)
        return (f"{who}: " if who else "") + c["email"] + (f" ({c['how']})" if c.get("how") else "")
    return "nobody found" + (f": {c['note']}" if c.get("note") else "")


def stage_label(a: dict) -> str:
    if a.get("stage") == "docs_ready" and a.get("needs_email"):
        return "email needs rewriting"
    if a.get("stage") == "docs_ready" and not (a.get("contact") or {}).get("email"):
        return "ready: no address yet"
    if a.get("stage") == "replied" and a.get("reply_kind"):
        return f"replied: {a['reply_kind'].replace('_', ' ')}"
    return STAGES.get(a.get("stage"), a.get("stage") or "")


def fit_line(a: dict) -> str:
    if "matched" not in a:
        return ""
    has = "has " + ", ".join(a["matched"]) if a["matched"] else "has none of the must-haves"
    return has + ("; missing " + ", ".join(a["gaps"]) if a.get("gaps") else "")


def found_row(j: dict) -> dict:
    """A job the search found (or one pasted and not yet prepared), as a row of the table."""
    fit = f"fit {j['score']}: {j.get('why') or ''}".strip(": ") if j.get("score") is not None else ""
    note = ", ".join(x for x in ((j.get("verified") or "").replace("_", " "), f"posted {j['posted']}" if j.get("posted") else "",
                                 j.get("location") or "") if x)
    return {"key": j["key"], "order": j.get("order") or "", "stage": "found" if j.get("source") == "discovery" else "to_prepare",
            "company": j.get("company") or "", "title": j.get("title") or "", "url": j.get("url"), "fit_text": fit, "note": note}


# How the platform's page draws the table (a `_layout` the page honours; older pages ignore it and draw the rows by
# shape): one card per job, its stage as a chip, what this run did to it in accent, and the email and letter readable
# before anyone approves a send. The posting opens from the card.
LAYOUT = [{"type": "cards", "key": "applications", "caption": "Your applications", "title": "company", "subtitle": "role",
           "accent": ["update"], "chips": ["stage"], "href": "apply_link",
           "sections": [{"key": "fit", "label": "Fit"}, {"key": "contact", "label": "Who it goes to"},
                        {"key": "email", "label": "The email"}, {"key": "cover_letter", "label": "Cover letter"},
                        {"key": "note", "label": "Note"}],
           "action": {"label": "Open the posting", "href": "apply_link"}}]

# An application that went out, by Gravity or by hand: the only ones a reply or a follow-up can be about.
SENT_STAGES = ("sent", "applied", "replied", "closed", "done")


def latest_draft(a: dict) -> dict | None:
    """The draft the agent kept that still waits for the person: a reply to their last mail, else the newest follow-up
    not yet sent, else the application itself when a send was refused. {kind, text, id, where} or None."""
    r = a.get("reply_draft") or {}
    if r.get("text") and not r.get("sent_at"):
        return {"kind": "reply", "text": r["text"], "id": r.get("id"), "where": "reply_draft"}
    for d in reversed(a.get("follow_up_drafts") or []):
        if not d.get("sent_at"):
            return {"kind": f"follow-up {d.get('n', 1)}", "text": d.get("text") or "", "id": d.get("id"), "where": "follow_up"}
    if a.get("stage") == "draft_kept" and a.get("draft_text"):
        return {"kind": "the application", "text": a["draft_text"], "id": a.get("draft_id"), "where": "application"}
    return None


def board(all_jobs: dict, apps: dict, pdfs: dict | None = None, updates: dict | None = None) -> tuple[list[dict], list[str]]:
    """(the table every result shows, its keys in row order): every job, in the order it arrived, with its application
    when it has one, so a row number means the same job in every result. It is the only table in any result: a
    per-row click gives the row numbers of the table it was under. `updates` is what this run did to each row."""
    items = {**{k: found_row(j) for k, j in all_jobs.items()}, **{k: a for k, a in apps.items() if a.get("stage") != "removed"}}
    order = sorted(items.values(), key=lambda a: a.get("order") or "")
    rows = []
    for a in order:
        e = a.get("email") or {}
        if "fit_text" in a:  # a found job: no documents yet
            rows.append({"company": a["company"], "role": a["title"], "stage": STAGES[a["stage"]], "contact": "", "fit": a["fit_text"],
                         "apply_link": a.get("url") or "", "email": "", "email_problems": "", "lines_dropped": 0, "cover_letter": "",
                         "note": a["note"], "update": (updates or {}).get(a["key"], "")})
            continue
        row = {"company": a.get("company") or "", "role": a.get("title") or "", "stage": stage_label(a),
               "contact": contact_line(a.get("contact") or {}), "fit": fit_line(a),
               "apply_link": a.get("apply_url") or a.get("url") or "",
               "email": f"{e['subject']}\n\n{e['body']}" if e.get("body") else "",
               "email_problems": "; ".join(e.get("problems") or []), "lines_dropped": a.get("lines_dropped", 0),
               "cover_letter": a.get("letter") or "", "letter_problems": "; ".join(a.get("letter_problems") or []),
               "draft": (d := latest_draft(a)) and d["text"] or "", "draft_kind": d["kind"] if d else "",
               "note": a.get("note") or "",
               "update": (updates or {}).get(a["key"], "")}
        if pdfs and a["key"] in pdfs:
            row.update(pdfs[a["key"]])
        rows.append(row)
    return rows, [a["key"] for a in order]


def sheet_row(a: dict, stage: str | None = None) -> dict:
    """One application as a row of the person's own Google Sheet (APPLYZER's tracker columns, plus company, role and
    the reply). The link comes first: the sheet's upsert keys on the first column, so a row is updated, never doubled."""
    e, c = a.get("email") or {}, a.get("contact") or {}
    return {"Link": a.get("url") or a.get("key"), "Company": a.get("company") or "", "Role": a.get("title") or "",
            "Stage": stage or stage_label(a), "Sent to": c.get("email") or e.get("to") or "", "Subject": e.get("subject") or "",
            "Sent at": str(a.get("sent_at") or "")[:16].replace("T", " "), "Follow-ups": a.get("follow_ups", 0),
            "Reply": (a.get("reply_kind") or "").replace("_", " "), "Last reply": (a.get("last_reply") or "")[:200],
            "Thread": a.get("thread") or "", "Updated": utcnow().isoformat()[:16].replace("T", " ")}


def answer_sheet(inputs: dict, p: Profile | None) -> dict:
    """What an application form asks, from the candidate's own answers, to paste into it."""
    sheet = {"name": p.full_name if p else "", "email": p.email if p else "", "phone": p.phone if p else "",
             "notice_period": f"{inputs['notice_days']} days" if inputs.get("notice_days") not in (None, "") else "",
             "current_pay": inputs.get("current_ctc") or "", "expected_pay": inputs.get("expected_ctc") or "",
             "work_authorisation": inputs.get("work_auth") or "", "relocation": inputs.get("relocate") or "",
             "links": inputs.get("links") or (", ".join(p.links) if p else "")}
    return {k: v for k, v in sheet.items() if v}


def source_text(p: Profile, picks: list[dict]) -> str:
    """What a generated line may say: the resume, plus the picked repos' own README and description."""
    return candidate.master_text(p) + "\n" + "\n".join(candidate.repo_text(x) for x in picks)


def own_links(p: Profile, picks: list[dict]) -> list[str]:
    return [*p.links, *(x.get("url") for x in picks if x.get("url"))]


def save_local(name: str, data: bytes) -> None:
    """Under `gravity run` the PDFs also land next to the run's memory, to open from a file browser."""
    folder = os.environ.get("GRAVITY_MEMORY_DIR")
    if folder:
        out = Path(folder).parent / "applications"
        out.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(data)


async def graph():
    io = make_io()
    model = make_model()

    async def load_profile(inputs: dict, notebook: dict, notes: list) -> Profile | None:
        """Parsed once per resume text (its hash is kept); a new resume is parsed again."""
        text, _ = await candidate.resume_text(inputs.get("resume_file"), inputs.get("resume_text"))
        if not text:
            if notebook.get("profile"):  # an action run (paste the posting, pick) carries no resume: the last one given stands
                return Profile(**notebook["profile"])
            notes.append("no resume: upload it or paste it, and run again")
            return None
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        if notebook.get("resume_hash") == digest and notebook.get("profile"):
            return Profile(**notebook["profile"])
        p = await candidate.parse(model, text)
        if p is None or not (p.experiences or p.projects or p.education):
            notes.append("the resume couldn't be read into a profile: paste it as text and run again")
            return None
        notebook.update({"profile": p.model_dump(), "resume_hash": digest})
        return p

    async def load_projects(inputs: dict, notes: list) -> list[dict]:
        coll = store.collection("projects")
        known = dict(coll.find(limit=200))
        user = (inputs.get("github_username") or "").strip().lstrip("@")
        if user:
            try:
                for repo in await candidate.github_projects(model, user, known):
                    store.put_safely(coll, repo["name"], repo)
                    known[repo["name"]] = repo
            except httpx.HTTPError as e:
                notes.append(f"GitHub couldn't be read ({type(e).__name__}); the projects already known were used")
        return list(known.values())

    async def prepare_one(row: dict, p: Profile, projects: list[dict]) -> dict:
        """One job to an application: read, tailored, a letter, a contact. The email is written per company after."""
        app = {k: row.get(k) for k in ("key", "company", "title", "url", "domain", "order")}
        app["created_at"] = utcnow().isoformat()
        text = row.get("description") or ""
        if not text and row.get("url"):
            text = (await io.page(row["url"]))[:15000]
        if not text:
            return {**app, "stage": "needs_text", "note": "the page couldn't be read: paste the posting's text"
                    if row.get("url") else "no link: paste the posting's text"}
        if tailor.is_closed(text):
            return {**app, "stage": "closed", "note": "the page says the job is closed"}
        if tailor.is_login_wall(text):
            return {**app, "stage": "needs_text", "note": "the link opened a sign-in page, not the posting: paste its text"}
        req = await tailor.requirements(model, text)
        if req is None:
            return {**app, "stage": "needs_text", "note": "the posting couldn't be read: paste its text"}
        if req.closed:
            return {**app, "stage": "closed", "note": "the page says the job is closed"}
        if not (req.must_have or req.keywords or req.company_fact):  # navigation text, not a posting
            return {**app, "stage": "needs_text", "note": "the page didn't read as a job posting: paste its text"}
        req = req.model_copy(update={"company": row.get("company") or req.company, "title": row.get("title") or req.title})
        picks = await tailor.pick_projects(model, req.title, text, projects)
        source = source_text(p, picks)
        doc, dropped = await tailor.tailor_resume(model, p, req, picks, source)
        if doc is None:
            return {**app, "company": req.company, "title": req.title, "stage": "needs_text",
                    "note": "the resume couldn't be tailored this run; run Prepare again"}
        letter, letter_problems = await tailor.cover_letter(model, p, req, source)
        contact = await outreach.find_contact(io.page, model, row, req, find=io.find_email)
        matched, gaps = tailor.skill_match(req, source)
        return {**app, "company": req.company, "title": req.title, "stage": "docs_ready", "req": req.model_dump(),
                "resume": doc.model_dump(), "lines_dropped": dropped, "letter": letter, "letter_problems": letter_problems,
                "contact": contact, "projects": [x["name"] for x in picks if x.get("name")], "matched": matched,
                "gaps": gaps, "apply_url": req.apply_url or row.get("url") or "", "_picks": picks}

    async def write_emails(ready: list[dict], p: Profile) -> None:
        """One email per company this run: two roles there share it (and its subject names both)."""
        groups: dict[str, list[dict]] = {}
        for a in ready:
            gk = a.get("domain") or re.sub(r"[^a-z0-9]+", "-", (a.get("company") or "").lower())
            groups.setdefault(gk, []).append(a)
        stamp = utcnow().isoformat()
        for gk, apps in groups.items():
            lead = next((a for a in apps if a["contact"].get("email")), apps[0])
            kind = lead["contact"].get("kind") or "application"  # nobody found: the candidate addresses it
            roles = [a["title"] for a in apps]
            req = tailor.Requirements(**lead["req"])
            body, problems = await outreach.first_email(model, kind, p, req, roles, source_text(p, lead["_picks"]),
                                                        own_links(p, lead["_picks"]))
            email = {"kind": kind, "to": lead["contact"].get("email"), "to_name": lead["contact"].get("name") or "",
                     "subject": outreach.subject(roles, req.company), "body": body, "problems": problems}
            for a in apps:
                a.update({"email": email, "group": f"{gk}@{stamp}",
                          "contact": a["contact"] if a["contact"].get("email") else lead["contact"]})

    # ── prepare, and paste_posting (which hands prepare the pasted job first) ──────────────────────────
    async def paste_posting(state: State):
        text = ((state.get("action_inputs") or {}).get("text") or "").strip()
        keys = picked_keys(state)
        apps, job_rows = store.collection("applications"), store.collection("jobs")
        for k in keys:
            row = job_rows.get(k)
            if row and text:
                store.put_safely(job_rows, k, {**row, "description": text[:20000]})
                apps.delete(k)
        return {"focus": keys}

    async def prepare(state: State):
        inputs, notebook, notes = state.get("inputs") or {}, dict(state.get("memory") or {}), []
        p = await load_profile(inputs, notebook, notes)
        await jobs.add(jobs.parse_lines(inputs.get("jobs") or ""), "input")
        apps_coll = store.collection("applications")
        apps = dict(apps_coll.find(limit=store.FIND_CAP))
        if p is None:
            rows, keys = board(dict(store.collection("jobs").find(limit=store.FIND_CAP)), apps)
            why = [*notes, *(getattr(model, "notes", None) or [])]  # a failed model call says why (a spent key, a budget)
            return {"result": {"summary": "Nothing prepared: " + notes[0], "applications": rows, "could_not": why, "_layout": LAYOUT},
                    "memory": {**notebook, "last_rows": keys}}
        projects = await load_projects(inputs, notes)
        all_jobs = dict(store.collection("jobs").find(limit=store.FIND_CAP))
        declined = set(store.collection("declined").has_many(list(all_jobs)))
        focus = state.get("focus") or []
        pending = sorted((r for k, r in all_jobs.items() if k not in apps and k not in declined and r.get("picked", True)),
                         key=lambda r: (r["key"] not in focus, r.get("order") or ""))
        if len(pending) > PER_RUN:
            notes.append(f"{len(pending) - PER_RUN} more jobs wait for the next run ({PER_RUN} a run, to stay inside "
                         "the run's budget): run Prepare again")
        sem = asyncio.Semaphore(3)

        async def one(row):
            async with sem:
                return await prepare_one(row, p, projects)
        done = await asyncio.gather(*(one(r) for r in pending[:PER_RUN]))
        by_name = {x.get("name"): x for x in projects}
        # an email that named a role the candidate dropped is written again, for the roles left
        redo = [{**a, "needs_email": False, "_picks": [by_name[n] for n in a.get("projects") or [] if n in by_name]}
                for a in apps.values() if a.get("stage") == "docs_ready" and a.get("needs_email")]
        ready = [a for a in done if a["stage"] == "docs_ready"]
        await write_emails(ready + redo, p)
        done = [*done, *redo]
        pdfs = {}
        for a in done:
            a.pop("_picks", None)
            if a["stage"] == "docs_ready":
                pdf = tailor.pdf_bytes(tailor.ResumeDoc(**a["resume"]))
                name = tailor.pdf_name(p, a["company"], a["title"])
                save_local(name, pdf)
                b64 = base64.b64encode(pdf).decode("ascii")
                pdfs[a["key"]] = {"pdf_name": name, **({"pdf_b64": b64} if len(b64) <= PDF_B64_MAX else {})}
                if a.get("letter") and not a.get("letter_problems"):
                    save_local(tailor.pdf_name(p, a["company"], a["title"], "Cover_Letter"),
                               tailor.letter_pdf(p, a["company"], a["letter"]))
            store.put_safely(apps_coll, a["key"], a)
            apps[a["key"]] = a
        notes += await sheet(list(done))
        rows, keys = board(all_jobs, apps, pdfs)
        waiting = sum(a["stage"] == "needs_text" for a in done)
        summary = (f"{len(ready)} application{'s' if len(ready) != 1 else ''} ready"
                   + (f", {waiting} waiting for the posting's text" if waiting else "")
                   + ". Nothing is sent until you press Send.")
        result = {"summary": summary, "applications": rows, "answer_sheet": answer_sheet(inputs, p),
                  "could_not": notes + [n for n in getattr(model, "notes", []) if n not in notes],
                  "_offer": ["send_email", "paste_posting", "mark_applied", "not_applying", "check_replies"]}
        return {"result": result, "memory": {**notebook, "last_rows": keys}}

    async def hand_sent(apps: dict, only: set[str] | None, now: datetime) -> tuple[dict, dict]:
        """Kept drafts the candidate sent themselves: found in Sent after the draft was kept, their edit measured.
        (changed, updates) for them and the roles that shared the email. Sent is read once (the manifest's 30-day
        window) and each kept draft's address picked out of the records' recipients; `only` limits it to the
        applications a run is about."""
        changed, updates = {}, {}
        kept = {(a.get("email") or {}).get("to"): a for a in apps.values()
                if a.get("stage") == "draft_kept" and (a.get("email") or {}).get("to") and (only is None or a["key"] in only)}
        sent = list(reversed(await io.mail("sent"))) if kept else []  # newest first
        for to, a in kept.items():
            for m in (m for m in sent if to.lower() in (m["to"] or "").lower()):
                if a["key"] in changed:
                    break
                since = a.get("draft_kept_at")
                if since and (m["when"] or now) < datetime.fromisoformat(since) - timedelta(minutes=5):
                    continue  # an email to that address from before the draft: not this one
                ratio = mail.edit_ratio(a.get("draft_text", ""), m["text"])
                mates = [b for b in apps.values() if a.get("group") and b.get("group") == a["group"]
                         and b.get("stage") == "draft_kept"] or [a]
                for b in mates:
                    changed[b["key"]] = {**b, "stage": "sent", "sent_by": "you", "thread": m["thread"],
                                         "sent_at": (m["when"] or now).isoformat(), "edit_ratio": ratio,
                                         "sent_text": mail.typed_text(m["text"])[:2000], "follow_ups": 0}
                    updates[b["key"]] = f"you sent it yourself ({mail.edited_words(ratio)} edited)"
        return changed, updates

    async def sheet(apps: list[dict], stage: str | None = None) -> list[str]:
        """The changed applications to the person's sheet, when they set one up; what went wrong, as a note."""
        if not apps:
            return []
        err = await io.track([sheet_row(a, stage) for a in apps])
        return [f"the Google Sheet wasn't updated: {err}"] if err else []

    def with_board(summary: str, notebook: dict, updates: dict | None = None, needs: list[str] | None = None,
                   extra: dict | None = None) -> dict:
        """A result: what needs the candidate, as lines first, then the one table, with what this run did per row."""
        rows, keys = board(dict(store.collection("jobs").find(limit=store.FIND_CAP)), dict(store.collection("applications").find(limit=store.FIND_CAP)),
                           updates=updates)
        result = {"summary": summary, **({"needs_you": needs} if needs else {}), "applications": rows, **(extra or {}), "_layout": LAYOUT}
        if notes := list(getattr(model, "notes", None) or []):  # a spent budget, a failed model call: why a field is blank
            result["could_not"] = [*(result.get("could_not") or []), *notes]
        return {"result": result, "memory": {**notebook, "last_rows": keys}}

    # ── send_email: three nodes, as signal-leads. draft (everything that needs no approval: the checks, the caps,
    # the drafts for what must wait) -> send (nothing but the approved `send` writes and idempotent writes:
    # after an approval this node re-runs from its start) -> keep (drafts for refused sends, the result).
    async def draft(state: State):
        notebook = state.get("memory") or {}
        p = Profile(**notebook.get("profile") or {})
        apps = dict(store.collection("applications").find(limit=store.FIND_CAP))
        now = utcnow()
        today = now.date().isoformat()
        # the day's count comes from what was sent, so a run that failed after a send still counts it
        sent_groups = {a.get("group") or a["key"] for a in apps.values()
                       if a.get("sent_by") == "gravity" and str(a.get("sent_at", "")).startswith(today)}
        sent_today = max((notebook.get("sent_today") or {}).get(today, 0), len(sent_groups))
        picked = [apps[k] for k in picked_keys(state) if k in apps]
        already, already_why = await hand_sent(apps, {a["key"] for a in picked}, now)
        for k, a in already.items():
            store.put_safely(store.collection("applications"), k, a)
            apps[k] = a
        suppressed = set(store.collection("suppressed").has_many([a["domain"] for a in picked if a.get("domain")]))
        entries, done_groups, to_send = [], set(), 0
        for a in picked:
            if a.get("group") and a["group"] in done_groups:
                continue
            row = {"company": a.get("company") or "", "roles": a.get("title") or "", "keys": [a["key"]], "note": ""}
            if a["key"] in already:
                entries.append({**row, "status": "skipped", "why": f"not sent again: {already_why[a['key']]}"})
                continue
            if a.get("stage") not in ("docs_ready", "draft_kept"):
                entries.append({**row, "status": "skipped", "why": f"it is {stage_label(a)}"})
                continue
            if a.get("needs_email") or not a.get("email"):
                entries.append({**row, "status": "skipped", "why": "its email needs rewriting: run Prepare first"})
                continue
            members = [b for b in apps.values() if a.get("group") and b.get("group") == a["group"]
                       and b.get("stage") in ("docs_ready", "draft_kept")] or [a]
            done_groups.add(a.get("group"))
            e = a.get("email") or {}
            row.update({"keys": [m["key"] for m in members], "roles": " / ".join(m.get("title") or "" for m in members),
                        "to": e.get("to"), "subject": e.get("subject") or "", "body": outreach.wrap(e.get("body") or "", p, e.get("to_name")),
                        "domain": a.get("domain") or ""})
            if e.get("kind") == "application" and a.get("resume"):
                pdf = base64.b64encode(tailor.pdf_bytes(tailor.ResumeDoc(**a["resume"]))).decode("ascii")
                row["attachment"] = {"name": tailor.pdf_name(p, a["company"], a["title"]), "data": pdf}
            if mail.mailbox_type(p.email) == "personal":
                row["note"] = "sent from a personal address, which is how most candidates write; it reads as you"
            if not e.get("to"):
                entries.append({**row, "status": "skipped", "why": "no address to send it to: copy the email from the table"})
                continue
            if row["domain"] and row["domain"] in suppressed:
                entries.append({**row, "status": "skipped", "why": "they asked not to be contacted"})
                continue
            company = (a.get("company") or "").lower()
            earlier = [b["sent_at"] for b in apps.values() if b.get("sent_at") and b["key"] not in row["keys"]
                       and ((a.get("domain") and b.get("domain") == a["domain"]) or (b.get("company") or "").lower() == company)]
            why = "; ".join(e.get("problems") or []) or stages.can_send(sent_today + to_send, max(earlier) if earlier else None, now)
            if why:
                await keep_draft(row)
                entries.append({**row, "status": "kept as a draft", "why": why})
                continue
            to_send += 1
            entries.append(row)
        return {"emails": entries, "sent_today": sent_today}

    async def keep_draft(e: dict) -> None:
        rec = await io.draft(e["to"], e["subject"], e["body"], file=e.get("attachment"))
        coll = store.collection("applications")
        for k in e["keys"]:
            if a := coll.get(k):
                store.put_safely(coll, k, {**a, "stage": "draft_kept", "draft_text": e["body"], "draft_id": rec.get("id"),
                                           "draft_kept_at": utcnow().isoformat()})

    async def send(state: State):
        entries, coll = [dict(e) for e in state.get("emails") or []], store.collection("applications")
        # checked again here, not only when drafting: an opt-out can land while the email waits for approval
        pending = [e for e in entries if not e.get("status")]
        stop = set(store.collection("suppressed").has_many([e["domain"] for e in pending if e.get("domain")]))
        for e in entries:
            if e.get("status"):
                continue
            if e.get("domain") in stop:
                e.update({"status": "skipped", "why": "they asked not to be contacted"})
                continue
            try:  # waits for the person's approval (the manifest's approvals.required_for)
                got = await io.send(e["to"], e["subject"], e["body"], file=e.get("attachment"))
            except RuntimeError as err:
                e.update({"status": "not sent", "why": str(err)[:200]})
                continue
            if got.get("rejected"):
                e["status"] = "rejected"
                continue
            e["status"] = "sent"
            for k in e["keys"]:
                if a := coll.get(k):
                    store.put_safely(coll, k, {**a, "stage": "sent", "sent_by": "gravity", "sent_at": utcnow().isoformat(),
                                               "thread": got.get("thread"), "message_id": got.get("id"),
                                               "sent_text": e["body"], "follow_ups": 0})
        return {"emails": entries}

    async def keep(state: State):
        notebook, entries = dict(state.get("memory") or {}), [dict(e) for e in state.get("emails") or []]
        updates = {}
        for e in entries:
            if e["status"] == "rejected":
                await keep_draft(e)
                e.update({"status": "kept as a draft", "why": "not approved"})
            text = e["status"] + (f": {e['why']}" if e.get("why") else "") + (f"; {e['note']}" if e.get("note") else "")
            for k in e["keys"]:
                updates[k] = text
        sent = sum(e["status"] == "sent" for e in entries)
        today = utcnow().date().isoformat()
        notebook["sent_today"] = {today: int(state.get("sent_today") or 0) + sent}
        coll = store.collection("applications")
        notes = await sheet([a for k in updates if (a := coll.get(k))])
        return with_board(f"{sent} sent, {len(entries) - sent} kept as drafts or skipped.", notebook, updates,
                          extra={"could_not": notes} if notes else None)

    # ── mark_applied, not_applying ───────────────────────────────────────────────────────────────────
    async def mark_applied(state: State):
        when = (state.get("action_inputs") or {}).get("applied_on") or utcnow().date().isoformat()
        coll, updates = store.collection("applications"), {}
        for k in picked_keys(state):
            if a := coll.get(k):
                stage = "applied" if a.get("stage") in ("docs_ready", "draft_kept", "needs_text") else a.get("stage")
                store.put_safely(coll, k, {**a, "applied_on": str(when)[:10], "stage": stage})
                updates[k] = f"marked applied on {str(when)[:10]}"
        notes = await sheet([a for k in updates if (a := coll.get(k))])
        return with_board("Marked as applied; a follow-up is drafted if nobody answers.", state.get("memory") or {}, updates,
                          extra={"could_not": notes} if notes else None)

    async def not_applying(state: State):
        coll, declined = store.collection("applications"), store.collection("declined")
        apps, removed = dict(coll.find(limit=store.FIND_CAP)), []
        job_rows = store.collection("jobs")
        for k in picked_keys(state):
            gone = apps.pop(k, None)
            removed += [gone] if gone else []
            store.put_safely(declined, k, {"at": utcnow().date().isoformat()})
            coll.delete(k)
            job_rows.delete(k)
            # the role's mates shared one email naming it: theirs is written again by the next Prepare
            for b in apps.values():
                if gone and gone.get("group") and b.get("group") == gone["group"] and b.get("stage") in ("docs_ready", "draft_kept"):
                    b.update({"stage": "docs_ready", "email": None, "group": None, "needs_email": True})
                    store.put_safely(coll, b["key"], b)
        notes = await sheet(removed, "not applying")
        return with_board("Removed; those postings won't be prepared again.", state.get("memory") or {},
                          extra={"could_not": notes} if notes else None)

    # ── check_replies: drafts only. Started by the mail trigger (inputs.event.emails) or by hand (the inbox).
    async def check_replies(state: State):
        notebook, inputs = state.get("memory") or {}, state.get("inputs") or {}
        p = Profile(**notebook.get("profile") or {})
        coll = store.collection("applications")
        apps = dict(coll.find(limit=store.FIND_CAP))
        if not any(a.get("stage") in SENT_STAGES for a in apps.values()):
            return with_board("No application has been sent yet, so there are no replies to check.", notebook)
        event = (inputs.get("event") or {}).get("emails")
        if event:
            inbox = mail.messages(event)
        else:  # by hand: the last two days of the inbox
            since = utcnow() - timedelta(days=2)
            inbox = [m for m in await io.mail("inbox") if (m["when"] or since) >= since]
        needs, drafted, changed, updates = [], 0, {}, {}
        for m in inbox:
            current = {k: changed.get(k, x) for k, x in apps.items()}
            a = replies.match(m, list(current.values()))
            if not a or m["id"] in (a.get("seen") or []):
                continue
            # two roles sent in one email share a thread and a group: what the reply says applies to both
            mates = [b for b in current.values() if b["key"] == a["key"] or (a.get("group") and b.get("group") == a["group"])]
            upd: dict = {}
            words = mail.typed_text(m["text"])[:600]
            sender = mail.address(m["from"])
            name = m["from"].split("<")[0].strip().strip('"') if "<" in m["from"] else ""
            head = f"{a.get('company') or ''}, {a.get('title') or ''}"
            kind, question = replies.rule_kind(m), ""
            if kind is None:
                r = await replies.classify(model, m)
                kind, question = (r.kind, r.question) if r else ("other", "")
            line, draft_note = None, ""
            if kind in ("auto_ack", "status_update"):
                upd["acknowledged"] = True
                if kind == "status_update":
                    upd.update({"last_reply": words[:300], "last_status_at": utcnow().isoformat()})
            elif kind in ("opt_out", "not_hiring", "bounce"):
                upd.update({"stage": "done", "reply_kind": kind, "last_reply": words[:300]})
                if kind != "bounce" and a.get("domain"):
                    store.put_safely(store.collection("suppressed"), a["domain"], {"at": utcnow().isoformat(), "why": kind})
                if kind == "bounce":
                    line = f"{head}: the address bounced; find another way in."
            elif kind != "out_of_office":
                upd.update({"stage": "done" if kind == "rejection" else "replied", "reply_kind": kind,
                            "last_reply": words[:300], "replied_at": utcnow().isoformat()})
                body, attach, why = None, False, ""
                if not outreach.NOREPLY.match(sender):
                    if kind in replies.REPLY_SYSTEM:
                        body = await replies.reply_text(model, kind, words, a.get("company") or "", a.get("title") or "")
                    elif kind == "question" and (ans := replies.answer_from_bank(question or words, inputs)):
                        body, attach = ans["text"], ans["attach"]
                if body and (writer.AI_TELLS.search(body) or "!" in body or writer.LINK.search(body)):
                    body, why = None, " The drafted reply failed a check; answer it yourself."
                if body:
                    file = None
                    if attach and a.get("resume"):
                        pdf = base64.b64encode(tailor.pdf_bytes(tailor.ResumeDoc(**a["resume"]))).decode("ascii")
                        file = {"name": tailor.pdf_name(p, a["company"], a["title"]), "data": pdf}
                    subject = m["subject"] if m["subject"].lower().startswith("re:") else f"Re: {m['subject']}"
                    text = outreach.wrap(body, p, name)
                    rec = await io.draft(sender, subject, text, thread=m["thread"], file=file)
                    upd["reply_draft"] = {"id": rec.get("id"), "text": text, "to": sender, "kind": kind, "at": utcnow().isoformat()}
                    drafted += 1
                    draft_note = " A reply is drafted: send it from the row, or from your mailbox."
                if kind != "rejection":
                    line = f"{head}: {kind.replace('_', ' ')}. {m['from']} wrote: \"{words[:200]}\".{draft_note}{why}"
            if line:
                needs.append((replies.NEEDS_YOU.index(kind) if kind in replies.NEEDS_YOU else 99, line))
            for b in mates:
                changed[b["key"]] = {**b, **upd, "seen": (b.get("seen") or []) + [m["id"]]}
                updates[b["key"]] = f"{kind.replace('_', ' ')}" + (": reply drafted" if draft_note else "")
        for k, a in changed.items():
            store.put_safely(coll, k, a)
        lines = [x for _, x in sorted(needs, key=lambda n: n[0])]
        notes = await sheet(list(changed.values()))
        return with_board(f"{len(lines)} need you, {drafted} repl{'ies' if drafted != 1 else 'y'} drafted.",
                          notebook, updates, lines, extra={"could_not": notes} if notes else None)

    # ── follow_ups: drafts only (a scheduled run never waits on an approval) ───────────────────────────
    async def follow_ups(state: State):
        """First, kept drafts the candidate sent by hand (found in Sent after the draft was kept, their edit
        recorded). Then what each application is owed: a follow-up in its thread, one note for a form application
        to the address that came with the job, or a quiet close."""
        notebook, now = state.get("memory") or {}, utcnow()
        p = Profile(**notebook.get("profile") or {})
        coll = store.collection("applications")
        apps = dict(coll.find(limit=store.FIND_CAP))
        needs, drafts, inbox = [], 0, None
        changed, updates = await hand_sent(apps, None, now)
        # follow-up drafts the candidate sent from Gmail: matched in Sent by thread, after the draft, their edit measured.
        # This is the number behind "keep drafting, or let them go out on their own".
        pending = {a["thread"]: a for a in apps.values() if a.get("thread")
                   and any(not d.get("sent_at") for d in a.get("follow_up_drafts") or [])}
        if pending:
            for m in await io.mail("sent"):
                a = pending.get(m["thread"])
                if not a:
                    continue
                a = changed.get(a["key"], a)
                drafts_ = [dict(d) for d in a.get("follow_up_drafts") or []]
                waiting = [d for d in drafts_ if not d.get("sent_at") and m["when"] and m["when"] >= datetime.fromisoformat(d["at"])]
                if waiting:
                    d = waiting[-1]
                    d.update({"sent_at": m["when"].isoformat(), "edit_ratio": mail.edit_ratio(d["text"], m["text"])})
                    changed[a["key"]] = {**a, "follow_up_drafts": drafts_, "last_touch_at": m["when"].isoformat()}
                    updates[a["key"]] = f"you sent follow-up {d['n']} ({mail.edited_words(d['edit_ratio'])} edited)"
        domains = [a["domain"] for a in apps.values() if a.get("domain")]
        suppressed = set(store.collection("suppressed").has_many(domains))
        for k in apps:
            a = changed.get(k, apps[k])
            what = stages.due(a, now)
            if not what or (a.get("domain") and a["domain"] in suppressed):
                continue
            head = f"{a.get('company') or ''}, {a.get('title') or ''}"
            if what == "close":
                changed[k] = {**a, "stage": "done", "reply_kind": "no_answer"}
                updates[k] = "closed: no answer in 21 days"
                continue
            e, c = a.get("email") or {}, a.get("contact") or {}
            if what == "applied":  # a form application: only an address that came with the job, never a found person
                to = c.get("email") if c.get("kind") == "application" else None
            else:
                to = c.get("email") or e.get("to")
            if a.get("thread"):  # pre-flight: anyone but the candidate in the thread since the send wrote back
                if inbox is None:  # one read for every application due today
                    inbox = await io.mail("inbox")
                thread = [m for m in inbox if m["thread"] == a["thread"]]
                since = datetime.fromisoformat(a["sent_at"])
                # the candidate's own address: their resume's, and whoever sent what was in the thread by the send
                mine = {p.email.lower()} | {mail.address(m["from"]) for m in thread
                                            if m["when"] and m["when"] <= since + timedelta(minutes=1)}
                mine.discard("")
                theirs = [m for m in thread if (m["when"] or now) > since and mail.address(m["from"]) not in mine]
                if theirs:
                    said = mail.typed_text(theirs[-1]["text"])[:200]
                    needs.append(f"{head}: they answered in the thread. {theirs[-1]['from']} wrote: \"{said}\".")
                    changed[k] = {**a, "stage": "replied", "reply_kind": "other", "last_reply": said}
                    updates[k] = "they answered; no follow-up"
                    continue
            n = 1 if what == "applied" else int(what[-1])
            changed[k] = {**a, "follow_ups": n, "last_follow_up_at": now.isoformat()}
            if not to:
                needs.append(f"{head}: you applied a week ago and no address came with the job to follow up with.")
                continue
            days = (now - datetime.fromisoformat(a.get("sent_at") or f"{a['applied_on']}T00:00:00+00:00")).days
            earlier = "\n\n".join([a.get("sent_text") or e.get("body") or "", *(d["text"] for d in a.get("follow_up_drafts") or [])])
            body, problems = await outreach.follow_up(model, n, p, a.get("company") or "", a.get("title") or "", days,
                                                      candidate.master_text(p), p.links, applied=what == "applied", earlier=earlier.strip())
            if problems or not body:
                needs.append(f"{head}: the follow-up draft failed a check ({'; '.join(problems)}); write it yourself.")
                continue
            subject = e.get("subject") or outreach.subject([a.get("title") or ""], a.get("company") or "")
            text = outreach.wrap(body, p, e.get("to_name") or c.get("name") or "")
            rec = await io.draft(to, f"Re: {subject}" if a.get("thread") else subject, text, thread=a.get("thread"))
            changed[k]["follow_up_drafts"] = [*(a.get("follow_up_drafts") or []),
                                              {"n": n, "at": now.isoformat(), "text": text, "id": rec.get("id"), "to": to}]
            drafts += 1
            label = {"follow_up_1": "follow-up 1", "follow_up_2": "follow-up 2", "applied": "a follow-up"}[what]
            updates[k] = f"{label} drafted" + (", in the thread" if a.get("thread") else "") + ": send it from the row, or from your mailbox"
        for k, a in changed.items():
            store.put_safely(coll, k, a)
        notes = await sheet(list(changed.values()))
        out = any(a.get("stage") in SENT_STAGES for a in {**apps, **changed}.values())
        return with_board(f"{drafts} follow-up draft{'s' if drafts != 1 else ''} ready to send." if out
                          else "Nothing to follow up yet: no application has been sent.", notebook, updates, needs,
                          extra={"could_not": notes} if notes else None)

    # ── send_draft: the draft the agent kept for a row goes out as the person left it, after their approval ──
    async def send_draft(state: State):
        coll, updates, needs = store.collection("applications"), {}, []
        now = utcnow()
        for k in picked_keys(state):
            a = coll.get(k)
            if not a:
                continue
            d = latest_draft(a)
            head = f"{a.get('company') or ''}, {a.get('title') or ''}"
            if not d:
                updates[k] = "nothing drafted for this one"
                continue
            if not d.get("id"):
                updates[k] = f"{d['kind']} was drafted before drafts were remembered: send it from your mailbox"
                continue
            try:
                got = await io.send_draft(d["id"])
            except RuntimeError as err:
                updates[k] = f"couldn't send {d['kind']}: {str(err)[:160]}"
                needs.append(f"{head}: the draft is gone from your mailbox, so it was probably sent or deleted there. {str(err)[:120]}")
                continue
            if got.get("rejected"):
                updates[k] = f"{d['kind']}: not approved; it stays in your mailbox"
                continue
            stamp = now.isoformat()
            if d["where"] == "application":
                a = {**a, "stage": "sent", "sent_by": "gravity", "sent_at": stamp, "thread": got.get("thread") or a.get("thread"),
                     "message_id": got.get("id"), "sent_text": d["text"], "follow_ups": 0}
            elif d["where"] == "follow_up":
                drafts_ = [dict(x) for x in a.get("follow_up_drafts") or []]
                for x in reversed(drafts_):
                    if not x.get("sent_at"):
                        x.update({"sent_at": stamp, "edit_ratio": 0.0, "sent_by": "gravity"})
                        break
                a = {**a, "follow_up_drafts": drafts_, "last_touch_at": stamp}
            else:
                a = {**a, "reply_draft": {**a["reply_draft"], "sent_at": stamp}, "last_touch_at": stamp}
            store.put_safely(coll, k, a)
            updates[k] = f"{d['kind']} sent"
        sent = sum(u.endswith(" sent") for u in updates.values())
        notes = await sheet([a for k in updates if (a := coll.get(k))])
        return with_board(f"{sent} draft{'s' if sent != 1 else ''} sent.", state.get("memory") or {}, updates, needs,
                          extra={"could_not": notes} if notes else None)

    # ── report: where every application stands, folded from memory ───────────────────────────────────
    async def report(state: State):
        apps = [a for _, a in store.collection("applications").find(limit=store.FIND_CAP)]
        week = utcnow() - timedelta(days=7)
        recent = lambda a, key: bool(a.get(key)) and datetime.fromisoformat(a[key]) >= week  # noqa: E731
        by_stage: dict[str, int] = {}
        for a in apps:
            by_stage[stage_label(a)] = by_stage.get(stage_label(a), 0) + 1
        sent, answered = sum(recent(a, "sent_at") for a in apps), sum(recent(a, "replied_at") for a in apps)
        fu = [d for a in apps for d in a.get("follow_up_drafts") or []]
        fu_sent = [d for d in fu if d.get("sent_at")]
        unchanged = sum(d.get("edit_ratio", 1) < 0.05 for d in fu_sent)
        # replies that came after a follow-up the candidate sent: the number behind "do follow-ups work"
        after = sum(bool(a.get("replied_at")) and any(d.get("sent_at") and d["sent_at"] < a["replied_at"]
                                                      for d in a.get("follow_up_drafts") or []) for a in apps)
        # by the kind of first email (application to the job's address, or a note to a person found), over all time
        kinds: dict[str, dict] = {}
        for a in apps:
            k = (a.get("email") or {}).get("kind")
            if k and a.get("sent_at"):
                d = kinds.setdefault(k, {"sent": 0, "replied": 0, "days": []})
                d["sent"] += 1
                if a.get("replied_at"):
                    d["replied"] += 1
                    d["days"].append((datetime.fromisoformat(a["replied_at"]) - datetime.fromisoformat(a["sent_at"])).days)
        by_kind = {k: {"sent": d["sent"], "replied": d["replied"],
                       "median_days_to_reply": statistics.median(d["days"]) if d["days"] else None} for k, d in sorted(kinds.items())}
        summary = f"{sent} sent and {answered} repl{'ies' if answered != 1 else 'y'} this week."
        if by_kind:
            summary += " Answered so far: " + ", ".join(
                f"{d['replied']} of {d['sent']} {'application emails' if k == 'application' else 'notes to a person'}"
                + (f" (about {d['median_days_to_reply']:g} days to a reply)" if d["median_days_to_reply"] is not None else "")
                for k, d in by_kind.items()) + "."
        if fu:
            summary += f" {len(fu_sent)} of {len(fu)} follow-up drafts sent by you, {unchanged} unchanged, {after} answered after one."
        return with_board(summary, state.get("memory") or {},
                          extra={"sent_this_week": sent, "replies_this_week": answered, "by_stage": dict(sorted(by_stage.items())),
                                 "by_email_kind": by_kind,
                                 "follow_ups": {"drafted": len(fu), "sent_by_you": len(fu_sent), "sent_unchanged": unchanged,
                                                "replies_after_one": after}})

    # ── discover: the vendored search, writing found jobs through the seam; pick: the person's choice -> prepare ──
    async def discover(state: State):
        inputs, notebook = state.get("inputs") or {}, dict(state.get("memory") or {})

        dead: dict[str, str] = {}  # an optional source the person skipped or whose app needs reconnecting: told once, not per query

        async def read(source: str, **args):
            name = finder.HIS_SOURCES[source]
            if name not in finder.DECLARED:
                raise RuntimeError(f"this agent declares no {name} source")  # his engine: that channel is off
            if name in dead:
                raise RuntimeError(dead[name])
            try:
                return await io.read(name, **args)
            except Exception as e:  # noqa: BLE001 - gravity.CallFailed: his engine treats a failing channel as off
                if any(w in str(e) for w in ("NEEDS_RECONNECT", "has no sources.", "NOT_GRANTED")):
                    dead[name] = str(e)[:200]
                raise
        got = await finder.search(inputs, notebook.get("scout") or {}, read=read, model=model, embed=make_embedder())
        if got["error"]:
            return with_board(got["error"], notebook)
        added = await jobs.add(got["rows"], "discovery")
        notebook["scout"] = got["memory"]
        n, new = len(got["rows"]), {r["key"] for r in added}
        updates = {r["key"]: ("found this run" if r["key"] in new else "found again") for r in added}
        summary = (f"{n} job{'s' if n != 1 else ''} found that fit your resume. Pick the ones to apply for."
                   if n else "No job found that fits well enough this run.")
        return with_board(summary, notebook, updates, extra={"could_not": got["warnings"]} if got["warnings"] else None)

    # ── add_address: the person names who a prepared application goes to, when the search found nobody ───────────
    async def add_address(state: State):
        addr = ((state.get("action_inputs") or {}).get("email") or "").strip()
        memory = state.get("memory") or {}
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr):
            return with_board("That doesn't read as an email address; nothing changed.", memory)
        coll, updates = store.collection("applications"), {}
        apps = dict(coll.find(limit=store.FIND_CAP))
        for k in picked_keys(state):
            a = apps.get(k)
            if not a or a.get("stage") not in ("docs_ready", "draft_kept"):
                continue
            # two roles at one company share the email: the address is theirs too
            for b in [x for x in apps.values() if x["key"] == k or (a.get("group") and x.get("group") == a["group"])]:
                b["contact"] = {**(b.get("contact") or {}), "email": addr, "how": "given by you", "note": ""}
                if b.get("email"):
                    b["email"] = {**b["email"], "to": addr}
                store.put_safely(coll, b["key"], b)
                updates[b["key"]] = f"goes to {addr}: press Send"
        return with_board(f"Address added to {len(updates)} application{'s' if len(updates) != 1 else ''}. Press Send on the row; it waits for your approval."
                          if updates else "Pick a prepared row to add an address to.", memory, updates)

    async def pick(state: State):
        coll, keys = store.collection("jobs"), picked_keys(state)
        for k in keys:
            if row := coll.get(k):
                store.put_safely(coll, k, {**row, "picked": True})
        return {"focus": keys}

    def route(state: State) -> str:
        # A run the mail trigger started carries the mail as inputs.event: it checks replies, never prepares.
        inputs = state.get("inputs") or {}
        if (inputs.get("event") or {}).get("emails"):
            return "check_replies"
        first = (state.get("actions") or ["prepare"])[0]
        if first == "prepare" and not (inputs.get("jobs") or "").strip():
            if picked_keys(state):
                return "pick"   # a typed message naming a job on the board: that one is picked, then prepared
            # nothing pasted: prepare what was picked, else go and find jobs
            apps = set(k for k, _ in store.collection("applications").find(limit=store.FIND_CAP))
            waiting = any(j.get("picked", True) and k not in apps for k, j in store.collection("jobs").find(limit=store.FIND_CAP))
            if not waiting:
                return "discover"
        return {"paste_posting": "paste_posting", "send_email": "draft", "send_draft": "send_draft", "mark_applied": "mark_applied",
                "not_applying": "not_applying", "check_replies": "check_replies", "follow_ups": "follow_ups",
                "report": "report", "discover": "discover", "pick": "pick", "add_address": "add_address"}.get(first, "prepare")

    nodes = {"prepare": prepare, "paste_posting": paste_posting, "draft": draft, "send": send, "keep": keep,
             "send_draft": send_draft, "mark_applied": mark_applied, "not_applying": not_applying,
             "check_replies": check_replies, "follow_ups": follow_ups, "report": report, "discover": discover, "pick": pick,
             "add_address": add_address}
    entries = ("prepare", "paste_posting", "draft", "send_draft", "mark_applied", "not_applying", "check_replies", "follow_ups",
               "report", "discover", "pick", "add_address")
    wf = StateGraph(State)
    for name, fn in nodes.items():
        wf.add_node(name, fn)
    wf.set_conditional_entry_point(route, {n: n for n in entries})
    wf.add_edge("paste_posting", "prepare")
    wf.add_edge("pick", "prepare")
    wf.add_edge("draft", "send")
    wf.add_edge("send", "keep")
    for name in ("prepare", "keep", "send_draft", "mark_applied", "not_applying", "check_replies", "follow_ups", "report", "discover", "add_address"):
        wf.add_edge(name, END)
    return wf.compile()
