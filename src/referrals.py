"""Warm referrals: people at a company who could put the candidate forward, and a short note to each. Apollo names them,
their public LinkedIn profile shows what they share with the candidate (code compares the two, so a shared school is
a fact, not a model's guess), and the note is checked like every email. A note goes only as a draft the person sends
after approving it; someone with no address gets the note to paste into LinkedIn, which contract v2 cannot message."""

from __future__ import annotations

import asyncio
import re

import writer
from candidate import Profile
from tooling import structured
from trust import first, same_role

KEEP = 3             # people shown for a job
PROFILES = 6         # public profiles read per job: one credit each
QUIET_DAYS = 7       # between two notes to one company
TITLES = ("hiring manager", "team lead", "talent acquisition")
SYSTEM = ("You write a short note from a job candidate to someone who works at a company, about an open role there. Under 90 "
          "words: one line on who the candidate is; what they share with this person, when it is given; one or two "
          "results from the resume that fit the role, with their numbers as written; then one question: whether this "
          "person would be open to referring them, or could say who is hiring for it. It asks for nothing else. Plain "
          "text, no attachment, no flattery, no exclamation marks, no links unless they are the candidate's own. Never a "
          "number, employer or tool the resume does not show. " + writer.DATA_NOTE)


def subject(role: str, company: str) -> str:
    return f"Question about the {role} role at {company}"


def people_in(got: dict) -> list[dict]:
    """Apollo's answer as {id, name, first, last, title, linkedin}, without anyone it gives no name for."""
    items = got.get("people") or got.get("contacts") or (got.get("data") or {}).get("people") or [] if isinstance(got, dict) else []
    out = []
    for x in items:
        fn, ln = str(first(x, "first_name") or ""), str(first(x, "last_name") or "")
        name = str(first(x, "name") or f"{fn} {ln}").strip()
        if name:
            out.append({"id": x.get("id"), "name": name, "first": fn or name.split()[0], "last": ln or " ".join(name.split()[1:]),
                        "title": str(first(x, "title") or ""), "linkedin": str(first(x, "linkedin_url", "linkedin") or "")})
    return out


def _texts(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [t for x in v.values() for t in _texts(x)]
    if isinstance(v, list):
        return [t for x in v for t in _texts(x)]
    return []


def _named(name: str, texts: list[str]) -> bool:
    n = " ".join(re.findall(r"\w+", (name or "").lower()))
    return len(n) >= 4 and any(re.search(rf"\b{re.escape(n)}\b", " ".join(re.findall(r"\w+", t.lower()))) for t in texts)


def common_ground(p: Profile, profile: dict) -> str:
    """What the public profile and the resume share, in words a note may use; "" when nothing is. Only the two
    profile sections are read: a school named in someone's post is not a shared school."""
    schools, jobs = _texts(profile.get("education") or []), _texts(profile.get("experience") or profile.get("experiences") or [])
    for e in p.education:
        if _named(e.institution, schools):
            return f"you both studied at {e.institution}"
    for x in p.experiences:
        if _named(x.company, jobs):
            return f"you both worked at {x.company}"
    return ""


async def address(io, who: dict, domain: str) -> tuple[str, str]:
    """(their work address at the company, how it was found); ("", "") when there is none worth using. Apollo first
    (its record also carries a last name its search hides), then Hunter, which checks what it finds."""
    got = await io.built_in("apollo.enrich_person", **({"id": who["id"]} if who.get("id") else {"linkedin_url": who.get("linkedin")}))
    rec = got.get("person") if isinstance(got.get("person"), dict) else got
    email, status = str(rec.get("email") or "").lower(), rec.get("email_status")
    who["last"] = str(rec.get("last_name") or who["last"])
    # Apollo answers `email_not_unlocked@domain.com` for an address the plan does not reveal
    if email.endswith("@" + domain) and "not_unlocked" not in email and status == "verified":
        return email, "Apollo, verified"
    found = await io.find_email(who["first"], who["last"], domain) if who["last"] else {}
    return (found["email"], f"Hunter, {found.get('status') or 'unchecked'}") if found.get("email") else ("", "")


async def note(model, p: Profile, who: dict, role: str, company: str, source: str, own: list[str]) -> tuple[str, list[str]]:
    """(the note's body, what is still wrong with it after one rewrite)."""
    base = (f"CANDIDATE: {p.full_name}\nRESUME:\n{source[:6000]}\n\nCOMPANY: {company}\nROLE: {role}\n"
            f"WRITING TO: {who['name']}, {who['title']}\nWHAT THEY SHARE: {who.get('common') or 'nothing known; do not invent one'}")
    extra = f"{company} {role} {who['name']} {who.get('common') or ''}"
    problems: list[str] = []
    for _ in range(2):
        got = await structured(model, writer.Body, SYSTEM, base + (f"\nFIX THESE IN THE LAST DRAFT: {'; '.join(problems)}" if problems else ""))
        if got is None:
            return "", ["the model call failed"]
        problems = writer.email_checks(got.body.strip(), source, extra, "note", own)
        if not problems:
            break
    return got.body.strip(), problems


async def find(io, domain: str, role: str, p: Profile) -> list[dict]:
    """The people worth writing to: those who share something with the candidate first, then those closest to the
    role. Each with their address when one can be had."""
    got = await io.built_in("apollo.search_people", q_organization_domains=[domain],
                            person_titles=[role, f"senior {role}", *TITLES], per_page=25)
    # someone in the role itself can speak for the work, so those profiles are read first
    found = sorted(people_in(got), key=lambda x: not same_role(role, x["title"], 0.5))[:PROFILES]

    async def read(who: dict) -> dict:
        page = await io.built_in("scrapecreators.linkedin_profile", url=who["linkedin"]) if who["linkedin"] else {}
        return {**who, "common": common_ground(p, page)}
    read_ = await asyncio.gather(*(read(x) for x in found))
    best = sorted(read_, key=lambda x: (not x["common"], not same_role(role, x["title"], 0.5)))[:KEEP]

    async def reach(who: dict) -> dict:
        email, how = await address(io, who, domain)
        return {k: who[k] for k in ("name", "title", "linkedin", "common")} | {"email": email, "how": how}
    return list(await asyncio.gather(*(reach(x) for x in best)))


def line(x: dict) -> str:
    """One person, for a page that draws only text."""
    state = (f"answered {x['replied_at'][:10]}: {x.get('last_reply') or ''}" if x.get("replied_at")
             else f"note sent {x['sent_at'][:10]}" if x.get("sent_at")
             else "note drafted" if x.get("draft_id") else "note to paste into LinkedIn" if x.get("note") else "no note yet")
    return f"{x['name']}, {x['title']}" + (f" ({x['common']})" if x.get("common") else "") + f": {state}"
