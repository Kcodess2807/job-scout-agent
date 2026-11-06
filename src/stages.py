"""Where an application stands, and what code allows next: the send gate (caps) and the follow-up cadence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import mail

DAILY_CAP = 20      # sends a day (the pipeline's Gmail cap)
COMPANY_DAYS = 30   # one cold email per company in this many days


def can_send(sent_today: int, company_sent_at: str | None, now: datetime) -> str | None:
    """Why an email must wait (kept as a draft), or None when it may go to the person for approval."""
    if sent_today >= DAILY_CAP:
        return f"today's {DAILY_CAP} sends are used; send it tomorrow"
    if company_sent_at:
        days = (now - datetime.fromisoformat(company_sent_at)).days
        if days < COMPANY_DAYS:
            return f"this company was emailed {days} day{'s' if days != 1 else ''} ago"
    return None


CADENCE = {"note": (4, 10), "application": (7, 14)}  # the pipeline's short note; APPLYZER's application email
CLOSE_DAY = 21     # no answer by then: closed, no email
APPLIED_DAY = 7    # a form application with no thread: one note
NUDGE_DAY = 5      # quiet this long after an interview: a check-in is drafted


def due(app: dict, now: datetime) -> str | None:
    """What the follow-up run owes this application: follow_up_1, follow_up_2, close, applied, interview_nudge, or None.
    Three touches at most, and nothing once they replied, it bounced, or it was closed; after an interview, one check-in
    if they went quiet."""
    done = app.get("follow_ups", 0)
    if app.get("stage") == "replied" and (i := (app.get("interviews") or [None])[-1]) and not i.get("nudged"):
        quiet = (now.date() - datetime.fromisoformat(i["on"]).date()).days >= NUDGE_DAY
        return "interview_nudge" if quiet and str(app.get("replied_at") or "")[:10] <= i["on"] else None
    if app.get("stage") == "sent" and app.get("sent_at"):
        days = (now - datetime.fromisoformat(app["sent_at"])).days
        if days >= CLOSE_DAY:
            return "close"
        steps = CADENCE.get((app.get("email") or {}).get("kind"), CADENCE["note"])
        if done < len(steps) and days >= steps[done]:
            return f"follow_up_{done + 1}"
        return None
    if app.get("stage") == "applied" and app.get("applied_on") and not app.get("thread") and done == 0:
        days = (now.date() - datetime.fromisoformat(app["applied_on"]).date()).days
        return "applied" if days >= APPLIED_DAY else None
    return None


# ── What a row shows about itself: everything that happened, and what happens next ──────────────────
ENDED = {"bounce": "the address bounced", "opt_out": "they asked not to be contacted", "not_hiring": "they said they are not hiring",
         "no_answer": f"closed: no answer in {CLOSE_DAY} days"}


def draft_name(d: dict) -> str:
    return "the check-in after the interview" if d.get("kind") == "interview_nudge" else f"follow-up {d.get('n', 1)}"


def _when(at: str) -> datetime:
    t = datetime.fromisoformat(at)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def history(app: dict) -> list[dict]:
    """Everything that happened to one application, oldest first: [{at, what, text}]. `at` is an ISO time or date
    ("" for a record kept before that moment was stamped: those come last), `what` the kind a page draws by,
    `text` the sentence. Read from the record alone, so it says the same thing in every result."""
    e, c = app.get("email") or {}, app.get("contact") or {}
    to = e.get("to") or c.get("email") or ""
    out: list[dict] = []

    def add(at, what: str, text: str) -> None:
        out.append({"at": str(at or ""), "what": what, "text": text})

    def by(rec: dict) -> str:
        return ("through Gravity after your approval" if rec.get("sent_by") == "gravity"
                else f"by you from your mailbox ({mail.edited_words(rec.get('edit_ratio', 0))} edited)")

    if app.get("applied_on"):
        add(app["applied_on"], "applied", "you applied on the company's form")
    if app.get("draft_kept_at"):
        add(app["draft_kept_at"], "kept", "the application was kept as a draft in your mailbox")
    if app.get("sent_at"):
        add(app["sent_at"], "sent", f"sent {by(app)}" + (f", to {to}" if to else ""))
    for d in app.get("follow_up_drafts") or []:
        add(d.get("at"), "follow_up_drafted", f"{draft_name(d)} drafted in your mailbox")
        if d.get("sent_at"):
            add(d["sent_at"], "follow_up_sent", f"{draft_name(d)} sent {by(d)}")
    kind, said = app.get("reply_kind"), (app.get("last_reply") or "").strip()
    if app.get("last_status_at"):  # the words are the status update's only until a later reply overwrites them
        add(app["last_status_at"], "status", "they sent a status update" + (f': "{said}"' if said and not kind else ""))
    if kind in ENDED:
        add(app.get("closed_at"), "closed", ENDED[kind])
    elif kind:
        add(app.get("replied_at"), "reply", f"they replied: {kind.replace('_', ' ')}" + (f': "{said}"' if said else ""))
    r = app.get("reply_draft") or {}
    if r.get("text"):
        add(r.get("at"), "reply_drafted", "your reply drafted in your mailbox")
        if r.get("sent_at"):
            add(r["sent_at"], "reply_sent", "your reply sent")
    for i in app.get("interviews") or []:
        add(i.get("on"), "interview", "interview" + (f": {i['notes'][:300]}" if i.get("notes") else ""))
    t = app.get("thanks") or {}
    if t.get("text"):
        add(t.get("at"), "thanks_drafted", "thank-you drafted in your mailbox")
        if t.get("sent_at"):
            add(t["sent_at"], "thanks_sent", "thank-you sent")
    stamped = sorted((x for x in out if x["at"]), key=lambda x: _when(x["at"]))  # stable: a draft before its send
    return stamped + [x for x in out if not x["at"]]


def next_step(app: dict, now: datetime) -> dict | None:
    """What happens to this application next, and on which day when the cadence says: {at, text}, or None when
    nothing is owed. The days are `due`'s own, so the row and the daily run never disagree."""
    stage, reply = app.get("stage"), app.get("reply_draft") or {}
    waiting = [d for d in app.get("follow_up_drafts") or [] if not d.get("sent_at")]
    if stage == "replied":
        last, thanks = (app.get("interviews") or [None])[-1], app.get("thanks") or {}
        if reply.get("text") and not reply.get("sent_at"):
            return {"at": "", "text": "your reply is drafted: send it from this row, or from your mailbox"}
        if thanks.get("text") and not thanks.get("sent_at"):
            return {"at": "", "text": "your thank-you is drafted: send it from this row, or from your mailbox"}
        if waiting:
            return {"at": "", "text": f"{draft_name(waiting[-1])} is drafted: send it from this row, or from your mailbox"}
        if last and not last.get("nudged"):
            return {"at": (_when(last["on"]) + timedelta(days=NUDGE_DAY)).date().isoformat(), "text": "a check-in is drafted if they go quiet"}
        if last:
            return {"at": "", "text": "waiting on them after the interview"}
        return {"at": "", "text": "waiting on them: your reply went" if reply.get("sent_at") else "answer them: nothing is drafted for this one"}
    if stage == "draft_kept":
        return {"at": "", "text": "the application is a draft in your mailbox: send it from this row"}
    if stage in ("sent", "applied") and waiting:
        return {"at": "", "text": f"follow-up {waiting[-1].get('n', 1)} is drafted: send it from this row, or from your mailbox"}
    done = app.get("follow_ups", 0)
    if stage == "sent" and app.get("sent_at"):
        sent = _when(app["sent_at"])
        steps = CADENCE.get((app.get("email") or {}).get("kind"), CADENCE["note"])
        if done < len(steps):
            return {"at": (sent + timedelta(days=steps[done])).date().isoformat(), "text": f"follow-up {done + 1} is drafted if nobody answers"}
        return {"at": (sent + timedelta(days=CLOSE_DAY)).date().isoformat(), "text": "closed if nobody answers"}
    if stage == "applied" and app.get("applied_on") and not app.get("thread") and done == 0:
        c = app.get("contact") or {}
        has = c.get("kind") == "application" and c.get("email")
        return {"at": (_when(app["applied_on"]) + timedelta(days=APPLIED_DAY)).date().isoformat(),
                "text": "one follow-up is drafted if nobody answers" if has else "a reminder to follow up yourself: no address came with the job"}
    return None


# ── Why nobody answers: what the record shows, as things to change ───────────────────────────────────
ADVICE_AFTER = 5     # applications out before the record says anything
GAP_SHARE = 0.4      # a missing skill on this share of postings is worth a line


def advice(apps: list[dict], now: datetime) -> list[str]:
    """What to change, from the applications themselves, most useful first; [] until enough went out to tell."""
    out = [a for a in apps if a.get("sent_at") or a.get("applied_on")]
    if len(out) < ADVICE_AFTER:
        return []
    answered = [a for a in out if a.get("replied_at")]
    lines = []
    if not answered:
        lines.append(f"{len(out)} applications out and nobody has answered: change the resume or the roles you aim at before sending more.")
    gaps: dict[str, int] = {}
    for a in out:
        for g in a.get("gaps") or []:
            gaps[g] = gaps.get(g, 0) + 1
    for g, n in sorted(gaps.items(), key=lambda x: -x[1])[:2]:
        if n / len(out) >= GAP_SHARE:
            lines.append(f"{g} is missing on {n} of {len(out)} postings you applied to: learn it and show it, or aim at roles without it.")

    def rate(kind: str) -> tuple[int, int]:
        sent = [a for a in out if (a.get("email") or {}).get("kind") == kind and a.get("sent_at")]
        return sum(bool(a.get("replied_at")) for a in sent), len(sent)
    (nr, nn), (ar, an) = rate("note"), rate("application")
    if nn >= 3 and an >= 3 and nr / nn > ar / an:
        lines.append(f"Notes to a person get answered more ({nr} of {nn}) than emails to a job's address ({ar} of {an}): look for a person first.")
    forms = [a for a in out if a.get("applied_on") and not a.get("sent_at")]
    if len(forms) >= 3 and not any(a.get("replied_at") for a in forms):
        lines.append(f"{len(forms)} form applications and no answer: ask someone at those companies for a referral.")
    waiting = [d for a in out if a.get("stage") != "done" for d in a.get("follow_up_drafts") or [] if not d.get("sent_at")]
    if len(waiting) >= 2:
        lines.append(f"{len(waiting)} follow-ups are drafted and not sent: they only help once they go.")
    return lines
