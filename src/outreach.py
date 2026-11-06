"""Who to write to about a job, and the email. The address that came with the job wins (the candidate gave it, or
the posting prints it): that is an application by email, APPLYZER's way. Else a recruiter or hiring person named on
the company's own pages, at an address those pages print: that is a short first note."""

from __future__ import annotations

import re

import research
import writer
from candidate import Profile
from jobs import BOARD_HOSTS, registrable
from tailor import Requirements
from tooling import structured

PERSONAS = ["recruiter", "talent", "people", "hiring manager", "engineering manager", "founder"]
NOREPLY = re.compile(r"^(no-?reply|do-?not-?reply|donotreply|mailer|notifications?|privacy|legal|press|support|security)@", re.I)


def subject(roles: list[str], company: str) -> str:
    """APPLYZER's subject; two roles at one company go in one email."""
    return f"Application for {' / '.join(roles)} at {company}"


def company_domain(row: dict, req: Requirements) -> str | None:
    if row.get("domain"):
        return row["domain"]
    host = re.sub(r"^(https?://)?(www\.)?", "", (req.company_site or "").lower()).split("/")[0]
    if host and "." in host and not any(host == b or host.endswith("." + b) for b in BOARD_HOSTS):
        return registrable(host)
    return None


async def find_contact(page, model, row: dict, req: Requirements, find=None) -> dict:
    """{email, name, title, how, kind, verified, note}. kind is "application" for an address that came with the job,
    "note" for a person found, None when nobody was found. `find`: the address lookup for a person the company's
    pages name without an address (tooling.Gravity.find_email)."""
    none = {"email": None, "name": "", "title": "", "how": "none", "kind": None, "verified": False, "note": ""}
    if row.get("hr_email"):
        return {**none, "email": row["hr_email"].lower(), "how": "given with the job", "kind": "application", "verified": True}
    printed = [e.lower() for e in req.emails if "@" in e and not NOREPLY.match(e)]
    if printed:
        return {**none, "email": printed[0], "how": "on the posting", "kind": "application", "verified": True}
    d = company_domain(row, req)
    if not d:
        return {**none, "note": "the company's own site isn't known, so nobody there could be looked up"}
    c = await research.contact(page, model, d, PERSONAS, req.company, find=find)
    if not c.get("email"):
        return {**none, "name": c.get("name") or "", "title": c.get("title") or "", "note": c.get("note") or "no address found"}
    how = "on the company's pages" if c.get("email_source") == "published" else f"found by Hunter, {c.get('status') or 'unchecked'}"
    return {**none, "email": c["email"].lower(), "name": c.get("name") or "", "title": c.get("title") or "", "how": how,
            "kind": "note", "verified": c.get("tier") in ("verified", "published"), "note": c.get("note") or ""}


def brief(p: Profile, req: Requirements, roles: list[str], source: str) -> str:
    return (f"CANDIDATE: {p.full_name}\nRESUME:\n{source[:6000]}\n\nCOMPANY: {req.company}\nROLE: {' and '.join(roles)}\n"
            f"WHAT THE POSTING SAYS ABOUT THE WORK: {req.company_fact or ', '.join(req.keywords) or 'not given'}\n"
            f"MUST HAVE: {', '.join(req.must_have)}")


async def first_email(model, kind: str, p: Profile, req: Requirements, roles: list[str], source: str,
                      own_links: list[str]) -> tuple[str, list[str]]:
    """(the body, what is still wrong with it after one rewrite)."""
    system = writer.APPLICATION_SYSTEM if kind == "application" else writer.NOTE_SYSTEM
    extra = f"{req.company} {' '.join(roles)} {req.company_fact}"
    base, problems = brief(p, req, roles, source), []
    for _ in range(2):
        got = await structured(model, writer.Body, system,
                               base + (f"\nFIX THESE IN THE LAST DRAFT: {'; '.join(problems)}" if problems else ""))
        if got is None:
            return "", ["the model call failed"]
        problems = writer.email_checks(got.body.strip(), source, extra, kind, own_links, req.must_have + req.nice_to_have)
        if not problems:
            break
    return got.body.strip(), problems


def wrap(body: str, p: Profile, to_name: str = "") -> str:
    """The greeting and the signature block (APPLYZER adds both outside the model)."""
    first = (to_name or "").split()[0] if to_name else ""
    sign = "\n".join(x for x in (p.full_name, " | ".join(y for y in (p.phone, *p.links) if y)) if x)
    return f"Hi {first or 'there'},\n\n{body.strip()}\n\nBest,\n{sign}"


# APPLYZER ai_service.generate_followup_email: its system prompt and tone per follow-up kept; its "3-4 short
# paragraphs in HTML" cut to the pipeline's short nudge in plain text.
FOLLOW_SYSTEM = ("You are an expert professional email writer specializing in job application follow-ups. You write "
                 "concise, professional, and effective follow-up emails that maintain interest without being pushy. "
                 "Two or three plain sentences, under 90 words. Reaffirm interest in the role, add one thing from the "
                 "resume that is relevant to it and was not in the earlier email, and end with one short question "
                 "(for a second follow-up: whether the role is still open). No greeting, no signature, no "
                 "attachment, plain text. Never a number, employer or tool the resume does not show. " + writer.DATA_NOTE)
TONES = {1: "polite and professional, expressing continued interest without being pushy",
         2: "respectful and understanding, acknowledging they may be busy while maintaining interest"}


async def follow_up(model, n: int, p: Profile, company: str, role: str, days: int, source: str,
                    own: list[str], applied: bool = False, earlier: str = "", interviewed: bool = False) -> tuple[str, list[str]]:
    """(the follow-up body, what is still wrong with it after one rewrite). `applied`: the candidate applied
    through the company's form and has not emailed before, so this is their first email, not a reminder. `earlier`:
    what was already written to them, so the follow-up adds something instead of repeating it."""
    context = (f"The candidate interviewed {days} days ago and has heard nothing since; ask for an update on the next step." if interviewed
               else f"The candidate applied through the company's form {days} days ago and has not emailed before; this is "
               "their first email, following up on that application." if applied
               else f"This is the first follow-up after {days} days of no response." if n == 1
               else f"This is follow-up #{n} after {days} days since the first email.")
    base = (f"Job Title: {role}\nCompany: {company}\nApplicant Name: {p.full_name}\n{context}\nTone: {TONES.get(n, TONES[2])}"
            + (f"\nALREADY WRITTEN TO THEM (do not repeat its points; add one new thing):\n{earlier[:1200]}" if earlier else "")
            + f"\nRESUME:\n{source[:6000]}")
    problems: list[str] = []
    for _ in range(2):
        got = await structured(model, writer.Body, FOLLOW_SYSTEM,
                               base + (f"\nFIX THESE IN THE LAST DRAFT: {'; '.join(problems)}" if problems else ""))
        if got is None:
            return "", ["the model call failed"]
        problems = writer.email_checks(got.body.strip(), source, f"{company} {role}", "note", own)
        if not problems:
            break
    return got.body.strip(), problems


THANKS_SYSTEM = ("You write a short thank-you from a job candidate after an interview. Two or three plain sentences, under 90 "
                 "words: thank them, name one thing from the conversation when notes are given, and say they would like to "
                 "continue. No greeting, no signature, plain text, no flattery, no exclamation marks. Never a number, employer "
                 "or tool the resume or the notes do not show. " + writer.DATA_NOTE)


async def thank_you(model, p: Profile, company: str, role: str, notes: str, source: str, own: list[str]) -> tuple[str, list[str]]:
    """(the thank-you's body, what is still wrong with it after one rewrite). `notes`: the person's own words on how it went."""
    base = f"Job Title: {role}\nCompany: {company}\nApplicant Name: {p.full_name}\nNOTES ON THE INTERVIEW: {notes or 'none given'}\nRESUME:\n{source[:6000]}"
    problems: list[str] = []
    for _ in range(2):
        got = await structured(model, writer.Body, THANKS_SYSTEM, base + (f"\nFIX THESE IN THE LAST DRAFT: {'; '.join(problems)}" if problems else ""))
        if got is None:
            return "", ["the model call failed"]
        problems = writer.email_checks(got.body.strip(), source, f"{company} {role} {notes}", "thanks", own)
        if not problems:
            break
    return got.body.strip(), problems
