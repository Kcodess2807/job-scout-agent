"""Stage D: the model, used only where it adds something and only within the run's call budget.

1. evaluate(): for borderline matches (scores near the threshold, full posting read), the model checks each
   requirement against the resume and must quote both sides. Code drops any requirement not in the posting and
   downgrades any "met" whose resume quote is not in the resume, then turns what survives into a bounded score
   adjustment (config.Policy.llm_adjust_max). The model never sets the score.
2. explain(): one short "why it fits" line per returned job, from the facts code computed. A line with a number
   that is not in those facts is discarded for the computed reason.

Postings and resumes are fenced as untrusted data; the model has no tools, so a posting cannot make it act."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from models import CandidateProfile, Listing, Match
from security import INJECTION_RULE, fence


class LLMBudget:
    def __init__(self, max_calls: int):
        self.max_calls, self.calls = max_calls, 0
        self.sem = asyncio.Semaphore(2)   # at most two model calls in flight (Gravity.agent.md section 7)

    def take(self) -> bool:
        if self.calls >= self.max_calls:
            return False
        self.calls += 1
        return True


class _Check(BaseModel):
    requirement: str = Field(description="one requirement, copied exactly from the posting")
    status: Literal["met", "partial", "missing", "unclear"]
    resume_quote: str | None = Field(default=None, description="exact words from the resume that show it, if met or partial")


class _JobEval(BaseModel):
    id: str
    checks: list[_Check] = Field(default_factory=list)
    eligibility_concerns: list[str] = Field(default_factory=list, description="hard requirements the person may not meet")
    explanation: str = Field(description="at most 35 words: why it fits or does not, from the checks only")
    confidence: Literal["high", "medium", "low"] = "medium"


class _Evals(BaseModel):
    jobs: list[_JobEval] = Field(default_factory=list)


class _Why(BaseModel):
    id: str
    why: str = Field(description="one sentence, at most 30 words")


class _Whys(BaseModel):
    jobs: list[_Why] = Field(default_factory=list)


EVAL_SYSTEM = f"""You check a job's requirements against one person's resume. For each requirement (up to 10, the
most important first) say met, partial, missing or unclear. "met" needs an exact quote from the resume showing it;
a technology only named in a skills list or a side project is at most "partial" for a requirement asking for
professional experience with it. Never invent experience, and never add requirements the posting does not state.
{INJECTION_RULE}"""

WHY_SYSTEM = f"""You write one short, honest sentence per job saying why it fits this person or what is missing.
Use ONLY the facts given for that job. Never add a number, a company fact, a salary or a link that is not in the
facts. If a requirement is missing, say so plainly. {INJECTION_RULE}"""


def _norm(s: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9+#]+", (s or "").lower()))


def _profile_brief(profile: CandidateProfile) -> dict:
    return {"years_experience": profile.years(), "titles": profile.titles[:5],
            "skills": {s.skill: s.strength.value for s in profile.skills[:40]},
            "education": profile.education[:2], "certifications": profile.certifications[:4]}


def validate_checks(checks: list[_Check], job_text: str, resume_text: str) -> list[dict]:
    jn, rn = _norm(job_text), _norm(resume_text)
    out = []
    for c in checks[:12]:
        req = _norm(c.requirement)
        if len(req) < 6 or req not in jn:
            continue  # not in the posting: dropped
        status = c.status
        quote = (c.resume_quote or "").strip()
        if status in ("met", "partial") and (not quote or _norm(quote) not in rn or len(_norm(quote)) < 4):
            status, quote = "unclear", ""
        out.append({"requirement": c.requirement.strip()[:200], "status": status, "resume_quote": quote[:200] or None})
    return out


def adjustment(checks: list[dict], match: Match, max_adjust: int) -> int:
    if len(checks) < 3:
        return 0
    value = {"met": 1.0, "partial": 0.5, "unclear": 0.25, "missing": 0.0}
    share = sum(value[c["status"]] for c in checks) / len(checks)
    det = match.breakdown.get("required_skills", 0.5)
    return max(-max_adjust, min(max_adjust, round((share - det) * 2 * max_adjust)))


async def evaluate(model, jobs: list[tuple[Listing, Match]], profile: CandidateProfile, resume_text: str,
                   budget: LLMBudget, max_adjust: int) -> dict[str, dict]:
    """key -> {"checks", "adjustment", "explanation", "concerns", "confidence"} for each job the model answered."""
    if model is None or not jobs:
        return {}
    out: dict[str, dict] = {}
    for i in range(0, len(jobs), 4):
        chunk = jobs[i:i + 4]
        if not budget.take():
            break
        payload = [{"id": lst.key, "title": lst.title,
                    "requirements": fence("\n".join((lst.requirements.required_lines + lst.requirements.preferred_lines)[:18]
                                                    or [lst.description[:2500]]), f"posting {lst.key}", 3500)}
                   for lst, _ in chunk]
        messages = [{"role": "system", "content": EVAL_SYSTEM},
                    {"role": "user", "content": "Person: " + json.dumps(_profile_brief(profile)) + "\n" +
                     fence(resume_text, "resume", 7000) + "\nJobs:\n" + json.dumps(payload)}]
        try:
            async with budget.sem:
                res: _Evals = await model.with_structured_output(_Evals).ainvoke(messages)
        except Exception:  # noqa: BLE001 - stage D is optional; stage B's score stands
            continue
        by_key = {lst.key: (lst, m) for lst, m in chunk}
        for je in res.jobs:
            if je.id not in by_key:
                continue
            lst, m = by_key[je.id]
            checks = validate_checks(je.checks, lst.description or lst.snippet, resume_text)
            out[je.id] = {"checks": checks, "adjustment": adjustment(checks, m, max_adjust),
                          "explanation": je.explanation.strip()[:300],
                          "concerns": [c.strip()[:160] for c in je.eligibility_concerns[:3] if c.strip()],
                          "confidence": je.confidence}
    return out


class _Audit(BaseModel):
    id: str
    experience_quote: str | None = Field(default=None, description="the exact sentence stating years of experience "
                                         "the candidate needs, copied from the posting; null if it states none")
    seniority_quote: str | None = Field(default=None, description="exact words asking for a senior/staff/lead/principal "
                                        "person, or saying new graduates are not considered; null if none")
    degree_quote: str | None = Field(default=None, description="exact words REQUIRING a degree level (not 'preferred', "
                                     "'a plus' or 'or equivalent'); null if none")
    location_quote: str | None = Field(default=None, description="exact words saying where the hire must be based or "
                                       "which countries a remote hire may live in; null if none")


class _Audits(BaseModel):
    jobs: list[_Audit] = Field(default_factory=list)


AUDIT_SYSTEM = f"""You read job postings and copy out, word for word, the sentences that state hard requirements:
years of experience, seniority, a required degree, and where the hire must be located. Copy exact text only; never
paraphrase, never infer, and return null for anything the posting does not state. {INJECTION_RULE}"""


def _grounded_quote(quote: str | None, text: str) -> str | None:
    q = " ".join((quote or "").split())
    return q if len(q) >= 6 and _norm(q) and _norm(q) in _norm(text) else None


async def audit(model, listings: list[Listing], budget: LLMBudget) -> dict[str, dict]:
    """key -> {"experience", "seniority", "degree", "location"}: each a quote that is verifiably in the posting.
    Code, not the model, turns a quote into a number or a rule (jd.years_required, jd.stated_level...)."""
    if model is None or not listings:
        return {}
    out: dict[str, dict] = {}
    for i in range(0, len(listings), 6):
        chunk = listings[i:i + 6]
        if not budget.take():
            break
        payload = [{"id": lst.key, "title": lst.title, "posting": fence((lst.description or lst.snippet)[:7000],
                                                                         f"posting {lst.key}", 7000)} for lst in chunk]
        try:
            async with budget.sem:
                res: _Audits = await model.with_structured_output(_Audits).ainvoke(
                    [{"role": "system", "content": AUDIT_SYSTEM}, {"role": "user", "content": json.dumps(payload)}])
        except Exception:  # noqa: BLE001 - the job keeps its code-read facts and an "unchecked" note
            continue
        by_key = {lst.key: lst for lst in chunk}
        for a in res.jobs:
            lst = by_key.get(a.id)
            if lst is None:
                continue
            text = lst.description or lst.snippet
            out[a.id] = {"experience": _grounded_quote(a.experience_quote, text),
                         "seniority": _grounded_quote(a.seniority_quote, text),
                         "degree": _grounded_quote(a.degree_quote, text),
                         "location": _grounded_quote(a.location_quote, text)}
    return out


def facts(listing: Listing, match: Match) -> dict:
    return {"title": listing.title, "company": listing.company, "score": match.score,
            "matched_skills": match.matched_skills[:8], "transferable": match.transferable_skills[:4],
            "missing_skills": match.missing_skills[:6], "experience": match.experience_note,
            "work_mode": listing.work_mode, "location": listing.location, "concerns": match.concerns[:2]}


def _numbers_ok(text: str, allowed: str) -> bool:
    return all(n in allowed for n in re.findall(r"\d+(?:\.\d+)?", text))


def computed_reason(listing: Listing, match: Match) -> str:
    parts = []
    req = listing.requirements.required_skills
    if match.matched_skills:
        parts.append(f"has {', '.join(match.matched_skills[:5])}" + (f" ({len(match.matched_skills)} of {len(req)} required skills)" if req else ""))
    if match.transferable_skills:
        parts.append(f"related experience for {', '.join(match.transferable_skills[:2])}")
    if match.experience_note:
        parts.append(match.experience_note)
    if match.missing_skills:
        parts.append(f"missing {', '.join(match.missing_skills[:3])}")
    text = "; ".join(parts) or "title and location match; the posting lists few concrete requirements"
    return text[:1].upper() + text[1:] + "."


async def explain(model, items: list[tuple[Listing, Match]], budget: LLMBudget) -> dict[str, str]:
    if model is None or not items or not budget.take():
        return {}
    payload = [{"id": lst.key, **facts(lst, m)} for lst, m in items[:12]]
    try:
        async with budget.sem:
            res: _Whys = await model.with_structured_output(_Whys).ainvoke(
                [{"role": "system", "content": WHY_SYSTEM}, {"role": "user", "content": json.dumps(payload, default=str)}])
    except Exception:  # noqa: BLE001 - the computed reason ships instead
        return {}
    allowed = {p["id"]: json.dumps(p, default=str) for p in payload}
    return {w.id: w.why.strip()[:300] for w in res.jobs if w.id in allowed and w.why.strip()
            and _numbers_ok(w.why, allowed[w.id])}
