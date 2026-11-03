"""Resume text -> CandidateProfile.

Code reads what code can read reliably: sections, skills with how strongly each is shown, date ranges and the
years they add up to, degrees, location. A model pass (optional, one call) may add job titles, companies,
education and stated target roles, but every item it returns must quote the resume, and a quote that is not in
the text drops the item. Nothing is inferred about notice period, salary, relocation or work arrangement."""
from __future__ import annotations

import datetime as dt
import re

from pydantic import BaseModel, Field

import geo
import skills as sk
from models import CandidateProfile, Field_, Role, SkillEvidence, Strength
from security import INJECTION_RULE, fence

HEADERS: dict[str, str] = {
    "experience": r"(?:work |professional |relevant |industry )?experience|employment(?: history)?|work history|"
                  r"career history|internships?(?: experience)?",
    "projects": r"(?:academic |personal |key |selected |side )?projects?",
    "skills": r"(?:technical |core |key |relevant )?skills(?: (?:&|and) (?:tools|technologies|interests))?|technologies|"
              r"tech(?:nical)? stack|tools(?: (?:&|and) technologies)?|competencies|expertise",
    "education": r"education(?:al background)?|academics?(?: background| qualifications)?|qualifications",
    "certifications": r"certifications?|licen[cs]es?(?: (?:&|and) certifications)?|courses|training",
    "summary": r"(?:professional |career )?summary|profile|(?:career )?objective|about(?: me)?",
    "other": r"achievements|awards|honou?rs|publications|extra-?curricular(?: activities)?|activities|volunteering|"
             r"interests|hobbies|languages|references",
}
_HEADER = {name: re.compile(rf"^(?:{pat})$", re.I) for name, pat in HEADERS.items()}

TITLE_WORDS = re.compile(
    r"\b(engineer|developer|programmer|analyst|scientist|architect|consultant|designer|manager|lead|intern|"
    r"administrator|specialist|associate|executive|officer|researcher|technician|tester|qa|devops|sre|"
    r"head|director|founder|co-founder|trainee|apprentice|fellow|strategist|coordinator|accountant)\b", re.I)

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = rf"(?:{_MONTH}\s*['’]?\s*\d{{2,4}}|\d{{1,2}}\s*[/.-]\s*\d{{4}}|(?:19|20)\d{{2}})"
_RANGE = re.compile(rf"({_DATE})\s*(?:-|–|—|to|until|till)\s*({_DATE}|present|current|now|till date|to date|ongoing|today)",
                    re.I)
_YEARS_STATED = re.compile(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years|yrs)\.?\s+(?:of\s+)?(?:professional\s+|industry\s+|"
                           r"work\s+|hands-on\s+|total\s+|relevant\s+)?(?:experience|exp\b)", re.I)
_FRESHER = re.compile(r"\b(fresher|fresh graduate|recent graduate|final[- ]year student|graduating in|"
                      r"undergraduate student|currently pursuing|expected graduation|class of 20\d\d)\b", re.I)
_DEGREES = (
    ("phd", r"\b(ph\.?\s?d|doctorate|doctor of philosophy)\b"),
    ("master", r"\b(master'?s?|m\.?\s?tech|m\.?e\.|m\.?sc|m\.s\.?|ms in|mba|mca|m\.?com|post[- ]?graduate|pgdm)\b"),
    ("bachelor", r"\b(bachelor'?s?|b\.?\s?tech|b\.?e\.|b\.?sc|b\.s\.?|bs in|bca|bba|b\.?com|b\.?a\.|undergraduate degree)\b"),
    ("diploma", r"\b(diploma)\b"),
)
DEGREE_RANK = {"none": 0, "diploma": 1, "bachelor": 2, "master": 3, "phd": 4}
_PREFERRED = re.compile(r"(?:seeking|looking for|aspiring|aspire to be|objective[:\s]+to (?:work|join|grow) as|"
                        r"target role[:\s]+|interested in)(?:\s+(?:an? (?:role|position|job) as an?|roles? as an?|positions? as an?|an?|the))?"
                        r"\s+([A-Za-z][A-Za-z /&+.-]{2,48}?)(?=\s+(?:role|roles|position|positions|opportunit\w*)\b|[.,;]|"
                        r"\s+(?:at|in|with|where)\b|$)", re.I | re.M)


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def sections(text: str) -> dict[str, list[str]]:
    """Lines grouped under the resume's own headings; lines before the first heading are the 'header'."""
    out: dict[str, list[str]] = {"header": []}
    current = "header"
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        bare = re.sub(r"^[#*_\s|•-]+|[#*_:\s|]+$", "", line)
        if 2 < len(bare) <= 45:
            hit = next((name for name, pat in _HEADER.items() if pat.match(bare)), None)
            if hit:
                current = hit
                out.setdefault(current, [])
                continue
        out.setdefault(current, []).append(line)
    return out


def _parse_date(text: str, *, end: bool, today: dt.date) -> tuple[int, int] | None:
    t = text.strip().lower()
    if t in ("present", "current", "now", "till date", "to date", "ongoing", "today"):
        return today.year, today.month
    m = re.match(rf"({_MONTH})\s*['’]?\s*(\d{{2,4}})", t)
    if m:
        month = _MONTHS.get(m.group(1)[:3])
        year = int(m.group(2))
        year = 2000 + year if year < 100 else year
        return (year, month) if month else None
    m = re.match(r"(\d{1,2})\s*[/.-]\s*(\d{4})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(2)), int(m.group(1))
    m = re.match(r"((?:19|20)\d{2})", t)
    if m:
        return int(m.group(1)), (12 if end else 1)
    return None


def _months(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, (b[0] - a[0]) * 12 + (b[1] - a[1]) + 1)


def _split_title(line: str) -> tuple[str | None, str | None]:
    """'AI Engineer at Acme' / 'Acme | AI Engineer' / 'AI Engineer, Acme' -> (title, company)."""
    text = _RANGE.sub("", line)
    text = re.sub(r"[()\[\]]", " ", text)
    parts = [p.strip(" -–—|,·•*_") for p in re.split(r"\s+(?:at|@)\s+|\s[|–—-]\s|,\s|\t", text) if p.strip(" -–—|,·•*_")]
    title = next((p for p in parts if TITLE_WORDS.search(p) and len(p) <= 70), None)
    company = next((p for p in parts if p != title and 1 < len(p) <= 60 and not geo.find_cities(p)
                    and not re.search(r"\d{4}", p)), None)
    return (" ".join(title.split()) if title else None), company


def history(lines: list[str], today: dt.date) -> list[Role]:
    roles: list[Role] = []
    for i, line in enumerate(lines):
        m = _RANGE.search(line)
        if not m:
            continue
        start = _parse_date(m.group(1), end=False, today=today)
        end = _parse_date(m.group(2), end=True, today=today)
        if not start or not end or end < start or start[0] < 1970:
            continue
        title, company = _split_title(line)
        if not title:  # the title is usually the line above (or below) the dates
            for j in (i - 1, i + 1, i - 2):
                if 0 <= j < len(lines) and not _RANGE.search(lines[j]):
                    title, company2 = _split_title(lines[j])
                    company = company or company2
                    if title:
                        break
        near = " ".join(lines[max(0, i - 1): i + 2])
        is_intern = bool(re.search(r"\bintern(ship)?\b|\btrainee\b|\bapprentice\b", f"{title or ''} {near}", re.I))
        present = m.group(2).strip().lower() in ("present", "current", "now", "till date", "to date", "ongoing", "today")
        roles.append(Role(title=title or "Unnamed role", company=company, start=f"{start[0]:04d}-{start[1]:02d}",
                          end="present" if present else f"{end[0]:04d}-{end[1]:02d}", months=_months(start, end),
                          internship=is_intern))
    roles.sort(key=lambda r: ("9999-99" if r.end == "present" else r.end or ""), reverse=True)
    return roles


def total_months(roles: list[Role]) -> int:
    """Overlapping jobs are counted once."""
    intervals = []
    for r in roles:
        if r.internship or not r.start or not r.months:
            continue
        y, m = (int(x) for x in r.start.split("-"))
        start = y * 12 + m
        intervals.append((start, start + r.months - 1))
    intervals.sort()
    total, cur = 0, None
    for a, b in intervals:
        if cur and a <= cur[1] + 1:
            cur = (cur[0], max(cur[1], b))
        else:
            if cur:
                total += cur[1] - cur[0] + 1
            cur = (a, b)
    if cur:
        total += cur[1] - cur[0] + 1
    return total


def seniority_of(years: float | None, latest_title: str | None) -> str | None:
    if years is None:
        return None
    level = 0 if years < 1 else 1 if years < 3 else 2 if years < 6 else 3 if years < 10 else 4
    t = (latest_title or "").lower()
    if re.search(r"\b(senior|sr\.?)\b", t) and years >= 4:
        level = max(level, 3)
    if re.search(r"\b(lead|principal|staff|head|director)\b", t) and years >= 6:
        level = max(level, 4)
    return ("entry", "junior", "mid", "senior", "lead")[level]


def _skill_evidence(secs: dict[str, list[str]]) -> list[SkillEvidence]:
    by_section = {name: sk.find("\n".join(lines)) for name, lines in secs.items()}
    found: dict[str, SkillEvidence] = {}
    order = (("experience", Strength.demonstrated), ("projects", Strength.project), ("skills", Strength.listed),
             ("summary", Strength.claimed), ("header", Strength.claimed), ("certifications", Strength.claimed),
             ("education", Strength.claimed), ("other", Strength.claimed))
    for name, strength in order:
        for skill, n in by_section.get(name, {}).items():
            if skill in found:
                found[skill].mentions += n
                found[skill].sections.append(name)
            else:
                quote = sk.first_line_with("\n".join(secs.get(name, [])), skill)
                found[skill] = SkillEvidence(skill=skill, strength=strength, mentions=n, sections=[name], quote=quote)
    for skill in sk.implied(set(found)):
        found[skill] = SkillEvidence(skill=skill, strength=Strength.inferred, mentions=0, sections=[])
    rank = {s: i for i, s in enumerate(Strength)}
    return sorted(found.values(), key=lambda e: (rank[e.strength], -e.mentions, e.skill))


def build_profile(text: str, *, resume_hash: str | None = None, today: dt.date | None = None) -> CandidateProfile:
    today = today or dt.date.today()
    if not (text or "").strip():
        return CandidateProfile(resume_present=False, notes=["No resume was given."])
    secs = sections(text)
    exp_lines = secs.get("experience", [])
    roles = history(exp_lines, today)
    if not roles:  # some resumes have no headings: look for date ranges anywhere outside education
        roles = [r for r in history([ln for k, v in secs.items() if k not in ("education", "projects") for ln in v], today)]
    months = total_months(roles)
    intern_months = sum(r.months or 0 for r in roles if r.internship)
    notes: list[str] = []

    stated = [float(m.group(1)) for m in _YEARS_STATED.finditer(text)]
    computed = round(months / 12, 1) if months else None
    if computed is not None and stated and abs(max(stated) - computed) > 1.5:
        years = Field_(value=computed, provenance="conflict",
                       note=f"the resume says {max(stated):g} years but its dates add up to {computed:g}")
        notes.append(f"Experience: {years.note}; the dates were used.")
    elif computed is not None:
        years = Field_(value=computed, provenance="explicit", note="from the dates of each job")
    elif stated:
        years = Field_(value=max(stated), provenance="explicit", note="as the resume states it")
    elif _FRESHER.search(text) or (not roles and secs.get("education")):
        years = Field_(value=0.0, provenance="inferred",
                       note="no paid job with dates found" + (" besides internships" if intern_months else ""))
    else:
        years = Field_(provenance="missing")

    evidence = _skill_evidence(secs)
    strong = [e.skill for e in evidence if e.strength == Strength.demonstrated or
              (e.strength in (Strength.listed, Strength.project) and e.mentions >= 2)]
    primary = strong[:12]
    secondary = [e.skill for e in evidence if e.skill not in primary and e.strength != Strength.inferred][:20]

    titles = []
    for r in roles:
        if r.title != "Unnamed role" and r.title not in titles:
            titles.append(r.title)

    edu_text = "\n".join(secs.get("education", [])) or text
    degrees = [name for name, pat in _DEGREES if re.search(pat, edu_text, re.I)]
    highest = max(degrees, key=lambda d: DEGREE_RANK[d]) if degrees else None
    education = [" ".join(ln.split())[:140] for ln in secs.get("education", []) if re.search(
        "|".join(p for _, p in _DEGREES), ln, re.I)][:4]
    certs = [" ".join(ln.strip(" -•*").split())[:120] for ln in secs.get("certifications", []) if 3 < len(ln) <= 160][:8]

    preferred = []
    for m in _PREFERRED.finditer(text):
        role = m.group(1).strip(" .-")
        if TITLE_WORDS.search(role) and role.lower() not in (p.lower() for p in preferred):
            preferred.append(role)
    headline = next((ln for ln in secs.get("header", [])[1:4] if TITLE_WORDS.search(ln) and len(ln) <= 80), None)
    if headline:
        first = re.split(r"\s[|•·,–-]\s", headline)[0].strip(" *#")
        if TITLE_WORDS.search(first) and first.lower() not in (p.lower() for p in preferred):
            preferred.append(first)

    header_text = "\n".join(secs.get("header", [])[:8])
    loc_line = re.search(r"(?:location|based in|address)\s*[:\-]\s*([^\n|]{2,60})", text, re.I)
    city_hits = geo.find_cities(loc_line.group(1) if loc_line else header_text)
    if city_hits:
        location = Field_(value=f"{city_hits[0][0]}, {geo.country_name(city_hits[0][1])}", provenance="explicit")
    else:
        country = geo.find_country(loc_line.group(1)) if loc_line else geo.find_country(header_text)
        location = Field_(value=geo.country_name(country), provenance="explicit") if country else Field_()

    evidence_lines = []
    for name in ("experience", "projects", "summary"):
        for ln in secs.get(name, []):
            ln = " ".join(ln.strip(" -•*▪").split())
            if evidence_lines and ln[:1].islower() and not evidence_lines[-1].endswith((".", ":")):
                evidence_lines[-1] = f"{evidence_lines[-1]} {ln}"[:400]   # a wrapped bullet's second line
            elif 25 <= len(ln) <= 320 and not _RANGE.fullmatch(ln):
                evidence_lines.append(ln)
    evidence_lines = evidence_lines[:60]

    sen = seniority_of(years.value if isinstance(years.value, (int, float)) else None, titles[0] if titles else None)
    if not exp_lines and not roles:
        notes.append("No experience section with dates was found.")
    return CandidateProfile(
        skills=evidence, primary_skills=primary, secondary_skills=secondary, titles=titles[:8], history=roles[:12],
        years_experience=years, internship_months=intern_months,
        seniority=Field_(value=sen, provenance="inferred" if sen else "missing",
                         note="from years of experience and the latest title" if sen else None),
        education=education, highest_degree=highest, certifications=certs, domains=sk.find_domains("\n".join(exp_lines)),
        preferred_roles=preferred[:4], location=location, evidence_lines=evidence_lines, resume_present=True,
        resume_hash=resume_hash, notes=notes)


# ---- the optional model pass: titles, companies, education, stated roles; every item quotes the resume

class _Quoted(BaseModel):
    value: str = Field(description="the item, as written in the resume")
    quote: str = Field(description="the exact words of the resume that show it, at most 20 words")


class _Extract(BaseModel):
    job_titles: list[_Quoted] = Field(default_factory=list, description="job titles held, most recent first")
    target_roles: list[_Quoted] = Field(default_factory=list, description="roles the person says they want")
    education: list[_Quoted] = Field(default_factory=list, description="degrees with the field of study")
    certifications: list[_Quoted] = Field(default_factory=list)


EXTRACT_PROMPT = """You read a resume into fields. Copy items exactly as written; for each give a short exact quote.
Return nothing for a field the resume does not state. Never guess notice period, salary, relocation or work
arrangement; they are not fields here. {rule}"""


def grounded(items: list[_Quoted], text_norm: str) -> list[str]:
    """Items whose quote is in the resume and whose value's words are in the quote."""
    out = []
    for it in items:
        q, v = _norm(it.quote), _norm(it.value)
        if not q or q not in text_norm or not v:
            continue
        words = [w for w in re.findall(r"[a-z0-9+#.]+", v) if len(w) > 2]
        if words and sum(w in q for w in words) / len(words) >= 0.6 and it.value.strip() not in out:
            out.append(" ".join(it.value.split())[:100])
    return out


async def enrich(profile: CandidateProfile, text: str, model, max_tokens: int = 1200) -> tuple[CandidateProfile, bool]:
    """One structured model call; returns (profile, called). A failure leaves the code-read profile as it is."""
    if model is None or not profile.resume_present:
        return profile, False
    try:
        llm = model.with_structured_output(_Extract).bind(max_tokens=max_tokens) if hasattr(model, "bind") \
            else model.with_structured_output(_Extract)
        out: _Extract = await llm.ainvoke([
            {"role": "system", "content": EXTRACT_PROMPT.format(rule=INJECTION_RULE)},
            {"role": "user", "content": fence(text, "resume", 12000)}])
    except Exception:  # noqa: BLE001 - the code-read profile still stands
        return profile, True
    norm = _norm(text)
    titles = grounded(out.job_titles, norm)
    roles = [r for r in grounded(out.target_roles, norm) if TITLE_WORDS.search(r)]
    edu = grounded(out.education, norm)
    certs = grounded(out.certifications, norm)
    update = {}
    if titles:
        update["titles"] = list(dict.fromkeys(profile.titles + [t for t in titles if t not in profile.titles]))[:8]
    if roles:
        update["preferred_roles"] = list(dict.fromkeys(profile.preferred_roles + roles))[:4]
    if edu and not profile.education:
        update["education"] = edu[:4]
    if certs and not profile.certifications:
        update["certifications"] = certs[:8]
    return (profile.model_copy(update=update) if update else profile), True
