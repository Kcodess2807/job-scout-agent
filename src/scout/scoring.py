"""Stage B: the structured fit score, in code. Each dimension is 0..1 with its reason; the weights (config.Weights)
turn them into 0..100. Two caps keep thin evidence from looking strong: a posting whose full text was never read
cannot pass the threshold, and a low-confidence match stays just under it. The score is a ranking heuristic until
calibrated (tests/eval_dataset.json): an 85 is not an 85% chance of anything."""
from __future__ import annotations

import re

from config import ScoutConfig
from eligibility import title_level
from models import STRENGTH_WEIGHT, CandidateProfile, Listing, Match, SearchPrefs, Strength
import skills as sk

MIN_SKILL_UNITS = 3        # below this many required skills, the skills score is pulled toward neutral
LEVELS = {"entry": 0, "junior": 1, "mid": 2, "senior": 3, "lead": 4}
_SYN = [(r"\bartificial intelligence\b", "ai"), (r"\bgen(?:erative)?[ -]?ai\b", "generativeai"), (r"\bml\b", "machinelearning"),
        (r"\bmachine learning\b", "machinelearning"), (r"\bback[- ]?end\b", "backend"), (r"\bfront[- ]?end\b", "frontend"),
        (r"\bfull[- ]?stack\b", "fullstack"), (r"\b(?:swe|sde)\b", "software engineer"), (r"\bdevelopers?\b", "engineer"),
        (r"\bprogrammers?\b", "engineer"), (r"\bdev\b", "engineer"), (r"\bengineers\b", "engineer"),
        (r"\bllms?\b", "llm"), (r"\blarge language models?\b", "llm"), (r"\bdata scientists?\b", "datascientist"),
        (r"\banalysts\b", "analyst"), (r"\bui\s*/\s*ux\b", "uiux"), (r"\bux\b", "uiux"), (r"\bqa\b", "quality"),
        (r"\bsre\b", "devops"), (r"\bsr\.?\b", "senior"), (r"\bjr\.?\b", "junior")]
_LEVEL = {"senior", "junior", "lead", "staff", "principal", "i", "ii", "iii", "iv", "intern", "internship", "associate",
          "trainee", "graduate", "entry", "level", "head", "chief", "mid", "fresher"}
CORES = ("engineer", "analyst", "scientist", "designer", "manager", "architect", "consultant", "specialist",
         "researcher", "administrator", "writer", "marketer", "recruiter", "accountant", "executive", "officer", "tester",
         "lead", "devops", "datascientist")


def role_tokens(title: str) -> list[str]:
    t = title.lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    for pat, rep in _SYN:
        t = re.sub(pat, rep, t)
    return [w for w in re.findall(r"[a-z0-9+#]+", t) if w not in _LEVEL and w not in ("and", "of", "the", "for", "to", "in", "at")]


def _core(tokens: list[str]) -> str | None:
    return next((w for w in reversed(tokens) if w in CORES), None)


def title_similarity(job_title: str, target: str) -> float:
    a, b = role_tokens(job_title), role_tokens(target)
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    dice = 2 * len(sa & sb) / (len(sa) + len(sb))
    ca, cb = _core(a), _core(b)
    if ca and cb and ca != cb and not {ca, cb} <= {"engineer", "devops"}:
        dice *= 0.4   # "data analyst" is not a "data engineer"
    elif ca and ca == cb:
        dice = max(dice, 0.35 + 0.65 * dice)
    return min(1.0, dice)


def role_match(job_title: str, targets: list[str], related: list[str]) -> float:
    best = max((title_similarity(job_title, t) for t in targets), default=0.0)
    rel = max((title_similarity(job_title, t) * 0.92 for t in related), default=0.0)
    return max(best, rel)


def job_level(listing: Listing) -> int | None:
    lvl = title_level(listing.title)
    if lvl is not None:
        return lvl
    lo = listing.requirements.min_years
    if lo is None:
        return None
    return 0 if lo < 1 else 1 if lo < 3 else 2 if lo < 5 else 3 if lo < 8 else 4


def seniority_fit(profile: CandidateProfile, listing: Listing) -> float:
    """A title states a level ("Senior", "Junior"); a minimum of years is only a floor, so being above it is fine
    unless far above (a 9-year engineer in a 1+ years role)."""
    cand = LEVELS.get(str(profile.seniority.value)) if profile.seniority.value else None
    titled = title_level(listing.title)
    job = titled if titled is not None else job_level(listing)
    if cand is None or job is None:
        return 0.95          # the posting states no level: nothing to hold against the person
    if cand <= 1 and job <= 1:
        return 1.0           # entry and junior roles are the same band for an early-career person
    diff = cand - job
    if titled is None and diff >= 0:
        return 1.0 if diff <= 2 else 0.7
    return {0: 1.0, 1: 0.8, 2: 0.45}.get(abs(diff), 0.2)


def skills_fit(listing: Listing, profile: CandidateProfile) -> tuple[float, list[str], list[str], list[str]]:
    """(value, matched, transferable, missing) over the posting's required skills. Either-or skills
    ("Power BI or Tableau") are one requirement, met by the strongest of them."""
    req = listing.requirements.required_skills
    have = profile.skill_map()
    if not req or not profile.resume_present:
        return 0.5, [], [], []
    names = set(have)
    early = str(profile.seniority.value) in ("entry", "junior")
    weight = dict(STRENGTH_WEIGHT)
    if early:  # with under 3 years, projects and the skills list are most of the evidence there is
        weight.update({Strength.project: 0.8, Strength.listed: 0.75})
    groups = [g for g in listing.requirements.alternatives if len(g) >= 2]
    grouped = {s for g in groups for s in g}
    units: list[list[str]] = groups + [[s] for s in req if s not in grouped]
    total, matched, trans, missing = 0.0, [], [], []
    for unit in units:
        best = max(unit, key=lambda s: weight[have[s].strength] if s in have else -1)
        ev = have.get(best)
        if ev:
            w = weight[ev.strength]
            total += w
            (matched if w >= 0.5 else trans).append(best)
            continue
        alt = next((a for a in (sk.transferable(s, names) for s in unit) if a), None)
        if alt:
            total += 0.35
            trans.append(f"{unit[0]} (has {alt})")
        else:
            missing.append(" or ".join(unit))
    # few recognised requirements are thin evidence: "1 of 1 matched" must not read as a perfect fit, so the
    # value is shrunk toward neutral (0.5) as if the missing units were unknown
    n = max(len(units), MIN_SKILL_UNITS)
    return (total + 0.5 * (n - len(units))) / n, matched, trans, missing


def experience_fit(listing: Listing, profile: CandidateProfile, cfg: ScoutConfig) -> tuple[float, str | None]:
    years = profile.years()
    lo, hi = listing.requirements.min_years, listing.requirements.max_years
    if lo is None:
        return (0.8 if years is not None else 0.65), None
    if years is None:
        return 0.5, f"asks for {lo:g}+ years; yours is unknown"
    if years >= lo:
        if hi is not None and years > hi + 3:
            return 0.75, f"asks for {lo:g}-{hi:g} years; you have {years:g} (more than it targets)"
        return 1.0, f"asks for {lo:g}{'-' + format(hi, 'g') if hi is not None else '+'} years; you have {years:g}"
    gap = lo - years
    tol = max(cfg.policy.experience_tolerance, cfg.policy.experience_tolerance_share * lo, 0.5)
    return max(0.55, 1 - 0.4 * gap / tol), f"asks for {lo:g}+ years; you have {years:g}"


def location_fit(listing: Listing, prefs: SearchPrefs) -> float:
    mode = listing.work_mode
    if mode == "remote":
        value = {"prefer_remote": 1.0, "remote_only": 1.0, "either": 0.9, "no_remote": 0.4}[prefs.work_mode]
        if not listing.location_country and not listing.location_cities:
            value *= 0.7   # remote from an unstated country: it may not hire where the person lives
        return value
    if prefs.work_mode == "remote_only":
        return 0.55 if mode == "unknown" else 0.0
    if prefs.cities:
        hit = [prefs.cities.index(c) for c in listing.location_cities if c in prefs.cities]
        value = (1.0 - 0.1 * min(hit)) if hit else (0.45 if listing.location_cities else 0.6)
    else:
        value = 0.85 if (listing.location_country or listing.location_cities) else 0.65
    if prefs.work_mode == "prefer_remote":
        value *= 0.8
    return max(0.0, value)


def preferred_fit(listing: Listing, profile: CandidateProfile) -> tuple[float, list[str]]:
    pref = listing.requirements.preferred_skills
    have = profile.skill_map()
    got = [s for s in pref if s in have and have[s].strength != Strength.inferred]
    skill_part = (len(got) / len(pref)) if pref else 0.6
    jd_domains, mine = set(listing.requirements.domains), set(profile.domains)
    domain_part = 0.6 if not jd_domains else (1.0 if jd_domains & mine else 0.35)
    return 0.7 * skill_part + 0.3 * domain_part, got


def confidence(listing: Listing, profile: CandidateProfile) -> str:
    points = 0.0
    points += 2 if listing.full_text else 0
    points += 1 if len(listing.requirements.required_skills) >= 3 else 0
    points += 1 if profile.years() is not None else 0
    points += 0.5 if listing.requirements.min_years is not None or listing.requirements.required_lines else 0
    points += 1 if profile.resume_present else 0
    if not profile.resume_present:
        return "low"
    return "high" if points >= 5 else "medium" if points >= 3.5 else "low"


def caps(cfg: ScoutConfig) -> tuple[int, int]:
    """The two evidence caps, always below the threshold whatever it is set to: an unread posting or a
    low-confidence match can rank as a lead but never qualify."""
    below = cfg.policy.min_score - 1
    return min(cfg.policy.snippet_only_cap, below - 5), min(cfg.policy.low_confidence_cap, below)


def score(listing: Listing, profile: CandidateProfile, prefs: SearchPrefs, cfg: ScoutConfig, *, related: list[str],
          semantic: float, rejections: list[str], concerns: list[str]) -> Match:
    w = cfg.weights
    s_val, matched, trans, missing = skills_fit(listing, profile)
    e_val, e_note = experience_fit(listing, profile, cfg)
    r_val = role_match(listing.title, prefs.target_roles, related) * seniority_fit(profile, listing)
    p_val, got_pref = preferred_fit(listing, profile)
    l_val = location_fit(listing, prefs)
    breakdown = {"required_skills": round(s_val, 3), "experience": round(e_val, 3), "role": round(r_val, 3),
                 "preferred": round(p_val, 3), "location": round(l_val, 3), "semantic": round(semantic, 3)}
    raw = sum(getattr(w, k) * v for k, v in breakdown.items())
    conf = confidence(listing, profile)
    snippet_cap, low_cap = caps(cfg)
    cap, why = None, None
    if not listing.full_text:
        cap, why = snippet_cap, "only the search snippet was read, not the posting"
    elif conf == "low":
        cap, why = low_cap, "too little evidence on one side to be sure"
    value = round(100 * raw)
    final = min(value, cap) if cap is not None else value
    return Match(eligible=not rejections, rejections=rejections, concerns=concerns, score=int(final), raw_score=round(raw, 4),
                 breakdown=breakdown, cap=cap, cap_reason=why if cap is not None and value > cap else None,
                 confidence=conf, matched_skills=matched, transferable_skills=trans, missing_skills=missing,
                 matched_preferred=got_pref, experience_note=e_note)


def rescore(match: Match, adjustment: int, cfg: ScoutConfig) -> Match:
    """Apply a bounded stage-D adjustment; the caps still hold."""
    adj = max(-cfg.policy.llm_adjust_max, min(cfg.policy.llm_adjust_max, int(adjustment)))
    value = round(100 * match.raw_score) + adj
    final = min(value, match.cap) if match.cap is not None else value
    return match.model_copy(update={"score": int(max(0, min(100, final))), "llm_adjustment": adj})


def prefilter(listing: Listing, prefs: SearchPrefs, related: list[str]) -> float:
    """Title relevance before a page is spent on it: 0..1."""
    return role_match(listing.title, prefs.target_roles, related)
