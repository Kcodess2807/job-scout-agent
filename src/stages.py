"""Where an application stands, and what code allows next: the send gate (caps) and the follow-up cadence."""

from __future__ import annotations

from datetime import datetime

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


def due(app: dict, now: datetime) -> str | None:
    """What the follow-up run owes this application: follow_up_1, follow_up_2, close, applied, or None. Three touches
    at most, and nothing once they replied, it bounced, or it was closed."""
    done = app.get("follow_ups", 0)
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
