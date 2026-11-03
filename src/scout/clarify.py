"""Extract first, ask second. Decides what is still missing once the form and the resume are read, asks at most
one batched question (through gravity.ask, passed in), applies disclosed defaults for the rest, and returns the
normalised SearchPrefs plus the profile with the person's own answers applied.

Asked only when it changes the search:
- the target role, when neither the form nor the resume gives one;
- years of experience, when a resume was given but its dates cannot establish it, or no resume and no answer.
Never asked: notice period, salary, relocation (only used when the person gives them)."""
from __future__ import annotations

import re
from collections.abc import Callable

from models import CandidateProfile, Field_, SearchPrefs
from prefs import DEFAULT_COUNT, corrections
from profile import seniority_of

Ask = Callable[..., object]

_INTERN_WORDS = re.compile(r"\b(intern(ship)?|trainee|apprentice)\b", re.I)


def missing(raw: dict, profile: CandidateProfile) -> list[str]:
    out = []
    if not raw["roles"] and not profile.preferred_roles and not profile.titles:
        out.append("role")
    if raw["years"] is None and profile.years() is None and not raw["internships"]:
        out.append("years")
    return out


def question_for(fields: list[str], profile: CandidateProfile) -> str | None:
    if fields == ["years"]:
        return ("Roughly how many years of work experience do you have? (e.g. 0, 2, 5) "
                + ("Your resume's dates could not tell." if profile.resume_present else ""))[:500].strip()
    if fields == ["role"]:
        return "Which role should I search for? (e.g. data analyst, backend engineer)"
    if fields:
        return "Which role should I search for, and roughly how many years of experience do you have? (e.g. 'data analyst, 2 years')"
    return None


def parse_answer(text: object, fields: list[str]) -> dict:
    t = " ".join(str(text or "").split())
    out: dict = {}
    if not t:
        return out
    if "years" in fields:
        m = re.search(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)?", t if "role" in fields else t + " years")
        if re.search(r"\b(fresher|none|no experience|zero)\b", t, re.I):
            out["years"] = 0.0
        elif m and (fields == ["years"] or re.search(r"\d\s*\+?\s*(years?|yrs?)", t, re.I)):
            out["years"] = float(m.group(1))
    if "role" in fields:
        role = re.sub(r"[,;]?\s*\d{1,2}(?:\.\d)?\s*\+?\s*(?:years?|yrs?)(?:\s+of\s+experience)?", "", t, flags=re.I)
        role = re.sub(r"\b(fresher|i am|i'm|a|an)\b", " ", role, flags=re.I)
        role = " ".join(role.split()).strip(" ,.;-")
        if re.search(r"[A-Za-z]{2,}", role):
            out["roles"] = [role[:60]]
    return out


def _role_from_title(title: str) -> str:
    return " ".join(_INTERN_WORDS.sub("", title).split()).strip(" -,") or title


def decide(raw: dict, profile: CandidateProfile, *, ask: Ask | None, memory_prefs: dict | None = None,
           default_min_score: int = 70, strict_score: int = 90) -> tuple[SearchPrefs, CandidateProfile, dict]:
    """(prefs, profile, info). info = {"asked": question or None, "answer": ..., "missing": [...]}."""
    raw = dict(raw)
    fix = corrections(raw.get("message") or "")
    if fix:  # a follow-up's corrections win over the earlier run's answers, the form's blanks and the resume
        for key in ("years", "work_mode", "count", "roles", "cities", "strict"):
            if key in fix:
                raw[key] = fix[key]
        if fix.get("add_cities"):
            raw["cities"] = list(dict.fromkeys(raw["cities"] + fix["add_cities"]))
    if memory_prefs:  # the last run's own answers (asked questions) fill what is still blank
        raw["roles"] = raw["roles"] or memory_prefs.get("roles") or []
        if raw["years"] is None and memory_prefs.get("years") is not None:
            raw["years"] = memory_prefs["years"]

    info: dict = {"asked": None, "answer": None, "missing": missing(raw, profile), "corrections": fix}
    if info["missing"] and ask is not None:
        q = question_for(info["missing"], profile)
        answer = ask(q) if q else None
        info.update(asked=q, answer=answer)
        got = parse_answer(answer, info["missing"])
        raw["roles"] = raw["roles"] or got.get("roles", [])
        if raw["years"] is None and "years" in got:
            raw["years"] = got["years"]

    assumptions: list[str] = []
    if raw["roles"]:
        roles, role_prov = raw["roles"], "user"
    elif profile.preferred_roles:
        roles, role_prov = profile.preferred_roles[:2], "inferred"
        assumptions.append(f"Role: {roles[0]}, the role your resume says you want.")
    elif profile.titles:
        roles, role_prov = [_role_from_title(profile.titles[0])], "inferred"
        assumptions.append(f"Role: {roles[0]}, your most recent title. Type a role to search for another.")
    else:
        raise ValueError("No role to search for: give a role, or a resume with your job titles.")

    # years: the person's own number wins; a conflict with the resume is said, not hidden
    if raw["years"] is not None:
        resume_years = profile.years()
        note = None
        if resume_years is not None and abs(resume_years - raw["years"]) > 1.5:
            note = f"you said {raw['years']:g} years; the resume's dates add up to {resume_years:g}"
            profile.notes.append(f"Experience: {note}; your number was used.")
        profile = profile.model_copy(update={
            "years_experience": Field_(value=float(raw["years"]), provenance="user", note=note),
            "seniority": Field_(value=seniority_of(float(raw["years"]), profile.titles[0] if profile.titles else None),
                                provenance="inferred", note="from the years you gave")})
    elif profile.years() is None:
        assumptions.append("Experience: unknown, so postings were not filtered on years; check each one's requirement.")

    cities = raw["cities"]
    if not cities and profile.location.value and isinstance(profile.location.value, str):
        from geo import find_cities
        hit = find_cities(profile.location.value)
        if hit and (not raw["country_code"] or hit[0][1] == raw["country_code"]):
            cities = [hit[0][0]]
            assumptions.append(f"City: {cities[0]} ranks higher because your resume gives it; other cities in "
                               f"{raw['country'] or 'your country'} are still included.")
    mode = raw["work_mode"]
    if mode is None:
        mode = "either"
        assumptions.append("Work arrangement: remote, hybrid and on-site are all included.")
    count = raw["count"] or (memory_prefs or {}).get("count") or DEFAULT_COUNT
    if not raw["count"]:
        assumptions.append(f"Number of jobs: {count}.")
    if not raw["country"]:
        assumptions.append("Country: none given, so jobs anywhere are included.")

    prefs = SearchPrefs(
        target_roles=roles, role_provenance=role_prov, internships=raw["internships"], country=raw["country"],
        country_code=raw["country_code"], cities=cities, work_mode=mode, employment_type=raw["employment_type"],
        count=int(count), count_note=raw.get("count_note"), min_salary=raw["min_salary"],
        min_score=strict_score if raw.get("strict") else default_min_score,
        include_likely_active=raw["include_likely_active"], assumptions=assumptions)
    return prefs, profile, info
