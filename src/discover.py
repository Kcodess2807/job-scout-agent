"""The job search (Sid's jobs-scout, vendored under src/scout) inside this agent: resume -> profile -> prefs ->
engine.scout -> rows for the seam (`jobs.add(rows, "discovery")`). His engine takes any `read(source, **args)`;
this gives it ours, by his source names. The small helpers at the top are copied from his agent.py (the compact
profile kept between runs, the rejections remembered for three days), which is not vendored because it declares
its own graph."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scout"))  # his modules import each other by bare name

from clarify import decide  # noqa: E402
from config import DEFAULT as SCOUT_CONFIG  # noqa: E402
from engine import scout  # noqa: E402
from evaluator import LLMBudget  # noqa: E402
from models import CandidateProfile, Field_, ScoutOutcome, SearchPrefs, SkillEvidence  # noqa: E402
from output import reason_for  # noqa: E402
from prefs import read_inputs  # noqa: E402
from profile import build_profile, enrich  # noqa: E402
from resume import load_resume  # noqa: E402

from jobs import registrable  # noqa: E402  (careers.zentrix.ai -> zentrix.ai: the team page lives on the company's own host)

# his channels by this agent's source names. The second search, the AI answers and "find similar" are not declared
# (0.7.2): each cost a setup step, an Explore step and a refusal per query, and the real runs found their jobs on the two
# main channels; his engine treats a channel that raises as off.
HIS_SOURCES = {"jobs": "search", "pages": "page", "jobs_more": "search_more", "answers": "answers", "similar": "similar"}
DECLARED = {"search", "page"}
SEEN_KEPT = 400
REJECTED_KEPT = 600       # postings found closed or unfit, remembered so the next runs skip them
REJECTED_DAYS = 3
_REUSED = ("titles", "preferred_roles", "education", "certifications")
DESCRIPTION_CHARS = 24000  # a jobs item is 32 KB


# ── his agent.py's helpers, as they are ───────────────────────────────────────────────────────────
def known_rejections(memory: dict, today: dt.date) -> dict:
    """Last runs' closed or unfit postings, still fresh enough to skip (a job shown is always re-checked)."""
    out = {}
    for key, v in (memory.get("rejected") or {}).items():
        try:
            age = (today - dt.date.fromisoformat(v.get("on", ""))).days
        except ValueError:
            continue
        if 0 <= age <= REJECTED_DAYS:
            out[key] = v
    return out


def compact_profile(p: CandidateProfile) -> dict:
    """What the next run may reuse: derived facts only, no resume lines, no employers, no contact details."""
    return {"resume_hash": p.resume_hash, "skills": [{"skill": s.skill, "strength": s.strength.value, "mentions": s.mentions}
                                                     for s in p.skills[:60]],
            "primary_skills": p.primary_skills, "secondary_skills": p.secondary_skills, "titles": p.titles[:5],
            "years": p.years(), "seniority": p.seniority.value, "highest_degree": p.highest_degree,
            "education": p.education[:2], "certifications": p.certifications[:5], "domains": p.domains,
            "preferred_roles": p.preferred_roles, "location": p.location.value}


def from_compact(c: dict) -> CandidateProfile:
    return CandidateProfile(
        skills=[SkillEvidence(**s) for s in c.get("skills") or []], primary_skills=c.get("primary_skills") or [],
        secondary_skills=c.get("secondary_skills") or [], titles=c.get("titles") or [],
        years_experience=Field_(value=c.get("years"), provenance="inferred" if c.get("years") is not None else "missing",
                                note="from the resume given in an earlier run"),
        seniority=Field_(value=c.get("seniority"), provenance="inferred" if c.get("seniority") else "missing"),
        highest_degree=c.get("highest_degree"), education=c.get("education") or [],
        certifications=c.get("certifications") or [], domains=c.get("domains") or [],
        preferred_roles=c.get("preferred_roles") or [], location=Field_(value=c.get("location"),
                                                                       provenance="explicit" if c.get("location") else "missing"),
        resume_present=True, resume_hash=c.get("resume_hash"),
        notes=["The resume from your earlier run was reused (its skills and years); paste it again to refresh."])


# ── the search, as one call ───────────────────────────────────────────────────────────────────────
def rows_of(outcome: ScoutOutcome) -> list[dict]:
    """The qualified jobs (near misses left out) as rows for the seam, best first."""
    near = set(outcome.near_misses)
    rows = []
    for lst in outcome.jobs:
        if lst.key in near:
            continue
        m = outcome.matches.get(lst.key)
        rows.append({"company": lst.company or "", "title": lst.title, "url": lst.application_url or lst.canonical_url,
                     "domain": registrable(lst.company_domain) if lst.company_domain else None, "location": lst.location,
                     "description": (lst.description or lst.snippet or "")[:DESCRIPTION_CHARS], "hr_email": None,
                     "score": m.score if m else None, "why": reason_for(lst, m, {}) if m else "",
                     "verified": lst.verification.status, "posted": lst.posted_at, "picked": False, "scout_key": lst.key})
    return rows


async def search(inputs: dict, memory: dict, *, read, model, embed, ask=None, today: dt.date | None = None,
                 listed: list[str] | None = None) -> dict:
    """{rows, memory, info, warnings, summary, error}. `memory` is this agent's `scout` notebook key, in and out.
    `listed`: the search's keys of jobs already on the person's list, skipped so a continuing search finds new ones."""
    today = today or dt.date.today()

    async def read_page(url: str):
        answer = await read("pages", id=url, args={"url": url})
        records = answer.get("records") or []
        return records[0] if records else None

    resume = await load_resume(inputs.get("resume_text"), inputs.get("resume_file"), read_page)
    stored = memory.get("profile") or {}
    if not resume["text"] and stored.get("skills"):
        profile = from_compact(stored)
    else:
        profile = build_profile(resume["text"], resume_hash=resume["hash"], today=today)
        if resume["hash"] and stored.get("resume_hash") == resume["hash"]:
            profile = profile.model_copy(update={k: stored[k] for k in _REUSED if stored.get(k)})
        elif profile.resume_present:
            profile, _ = await enrich(profile, resume["text"], model)
    try:
        prefs, profile, info = decide(read_inputs(inputs), profile, ask=ask, memory_prefs=memory.get("prefs"),
                                      default_min_score=SCOUT_CONFIG.policy.min_score, strict_score=SCOUT_CONFIG.policy.strict_score)
    except ValueError as exc:
        return {"rows": [], "memory": memory, "info": {}, "warnings": [], "summary": {}, "error": str(exc)}
    llm = LLMBudget(SCOUT_CONFIG.budget.max_llm_calls)
    outcome = await scout(profile, prefs, read=read, resume_text=resume["text"], model=model, embed=embed,
                          config=SCOUT_CONFIG, llm=llm, known={**known_rejections(memory, today), **{k: {"why": "listed"} for k in listed or []}}, today=today)
    rows = rows_of(outcome)
    shown = [r["url"] for r in rows]
    seen = [k for k in memory.get("seen", []) if k not in shown] + shown
    rejected = {**known_rejections(memory, today), **{k: {**v, "on": today.isoformat()} for k, v in outcome.remember.items()}}
    rejected = dict(sorted(rejected.items(), key=lambda kv: kv[1].get("on", ""))[-REJECTED_KEPT:])
    answered = {"roles": prefs.target_roles if prefs.role_provenance == "user" else None,
                "years": profile.years() if profile.years_experience.provenance == "user" else None, "count": prefs.count}
    new_memory = {"seen": seen[-SEEN_KEPT:], "rejected": rejected,
                  "prefs": {k: v for k, v in answered.items() if v is not None},
                  "profile": compact_profile(profile) if profile.resume_present else memory.get("profile"),
                  "last_run": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")}
    return {"rows": rows, "memory": new_memory, "info": info, "warnings": list(outcome.warnings),
            "summary": outcome.summary.model_dump(mode="json"), "error": None, "status": outcome.search_status}


__all__ = ["HIS_SOURCES", "SCOUT_CONFIG", "SearchPrefs", "search", "rows_of"]
