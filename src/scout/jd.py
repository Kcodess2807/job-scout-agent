"""A posting's text -> structured facts, in code. Requirements are split by the posting's own headings
("Requirements", "Nice to have", "What you'll do") and inline cues ("a plus", "preferred"), so a skill named only
under "nice to have" is never treated as mandatory, and skills in "About us" or "Benefits" are ignored."""
from __future__ import annotations

import datetime as dt
import re

import geo
import skills as sk
from models import Requirements, Salary

_REQ = re.compile(r"requirements?|qualifications?|must[- ]haves?|what you(?:'ll)? (?:need|bring)|who you are|"
                  r"you (?:have|bring|are)|minimum|basic qualifications|required|what we(?:'re| are) looking for|"
                  r"skills(?: (?:and|&) experience)?|experience|about you|your profile|eligibility|key skills", re.I)
_PREF = re.compile(r"nice[- ]to[- ]haves?|preferred(?: qualifications| skills)?|bonus(?: points)?|good[- ]to[- ]haves?|"
                   r"plus(?:es)?|desired|desirable|extra credit|it(?:'s| would be) great if", re.I)
_RESP = re.compile(r"responsibilit(?:y|ies)|what you(?:'ll| will) do|the role|your role|day[- ]to[- ]day|your impact|"
                   r"key (?:duties|responsibilities)|what you will be doing|job description|role overview|in this role", re.I)
_IGNORE = re.compile(r"about (?!you\b|the role\b|this role\b|the job\b|the position\b|the opportunity\b|"
                     r"your role\b)(?:us|the company|the team|[A-Z][\w&.]+)|benefits|perks|why (?:join|work)|"
                     r"equal (?:opportunity|employment)|eeo|our (?:values|culture|mission)|compensation|"
                     r"what we offer|life at|diversity|privacy|how to apply|interview process", re.I)
_INLINE_PREF = re.compile(r"\b(nice to have|a plus|is a plus|are a plus|preferred|bonus|good to have|desirable|"
                          r"ideally|advantage(?:ous)?|optional)\b", re.I)

_NUM = r"(\d{1,2}(?:\.\d)?)"
_YEARS_RANGE = re.compile(rf"{_NUM}\s*(?:\+\s*)?(?:-|–|to)\s*{_NUM}\s*\+?\s*(?:years?|yrs?)", re.I)
_YEARS_MIN = re.compile(rf"(?:(?:minimum|min\.?|at least|over|more than)\s+(?:of\s+)?)?{_NUM}\s*\+?\s*(?:years?|yrs?)", re.I)
_EXP_WORD = re.compile(r"experien|exp\b|background|working|industry|professional|hands[- ]on", re.I)
_FRESH = re.compile(r"\b(freshers?|entry[- ]level|new grad(?:uate)?s?|no experience required|0\s*(?:-|to)\s*1\s*years?|"
                    r"recent graduates?|graduate program|campus hire)\b", re.I)

_REMOTE = re.compile(r"\b(fully remote|100% remote|remote[- ]first|remote(?: role| position| job| work| opportunity)?|"
                     r"work from (?:home|anywhere)|wfh|distributed team)\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
_STRONG_REMOTE = re.compile(r"\b(fully remote|100% remote|remote[- ]first|work from anywhere|remote (?:role|position|job)|"
                            r"this (?:role|position|job) is remote|distributed team)\b", re.I)
_ONSITE = re.compile(r"\b(on[- ]?site|in[- ]office|work from office|wfo|office[- ]based|in[- ]person)\b", re.I)
_NOT_REMOTE = re.compile(r"\b(not (?:a )?remote|no remote|remote work is not|this is not a remote)\b", re.I)
_REMOTE_REGION = re.compile(r"remote\s*[(\-–,:]\s*(?:only\s+)?(?:in\s+)?([A-Za-z .]{2,30}?)\s*(?:only)?\s*[)\n,;.]", re.I)

_TYPES = (("internship", r"\b(internship|intern\b|interns\b|summer intern|co-?op)\b"),
          ("contract", r"\b(contract(?:or)?|freelance|c2h|contract[- ]to[- ]hire|fixed[- ]term)\b"),
          ("part_time", r"\b(part[- ]time)\b"), ("temporary", r"\b(temporary|seasonal)\b"),
          ("full_time", r"\b(full[- ]time|permanent|fte)\b"))

_DEGREE = (("phd", r"ph\.?\s?d|doctorate"), ("master", r"master'?s|m\.?\s?s\.?\b|m\.?\s?tech|mba|postgraduate"),
           ("bachelor", r"bachelor'?s|b\.?\s?tech|b\.?e\.\b|undergraduate degree|b\.?\s?s\.?\b|degree in"))
_AUTH = re.compile(r"[^.\n]*\b(authori[sz]ed to work|work authori[sz]ation|right to work|visa sponsorship|"
                   r"sponsorship is not|unable to sponsor|cannot sponsor|citizenship|security clearance|"
                   r"green card|permanent resident)\b[^.\n]*", re.I)

CLOSED = ("no longer accepting applications", "no longer available", "no longer open", "position has been filled",
          "this job has expired", "job has expired", "posting has expired", "this posting is closed", "job is closed",
          "applications are closed", "applications have closed", "this position is closed", "position is no longer",
          "role has been filled", "vacancy has been filled", "job not found", "page not found", "the page you requested could not be found",
          "this job listing is no longer active", "we are no longer accepting", "listing has expired", "job posting has been removed",
          "not accepting applications", "applications closed", "application closed", "job expired", "posting expired",
          "posting removed", "posting has been removed", "vacancy closed", "vacancy is closed", "position filled",
          "this job is no longer available", "this role is no longer available", "job is no longer available")
# a closed phrase inside one of these sentences is about something else ("we are not accepting applications from
# agencies", "applications closed? write to us anyway"), not about this posting
_CLOSED_ASIDE = re.compile(r"\b(agenc(?:y|ies)|third[- ]part(?:y|ies)|recruit(?:er|ment) firms?|unsolicited|via email|by email|"
                           r"other (?:roles|positions|openings)|similar (?:jobs|roles)|talent (?:pool|community)|"
                           r"if (?:this|the) (?:role|job|position)|if applications)\b", re.I)
APPLY = ("apply for this job", "apply now", "submit application", "submit your application", "apply for this position",
         "easy apply", "apply on company website", "i'm interested", "apply to this job", "start your application",
         "application form", "upload your resume", "upload resume", "attach resume")


def lines_of(text: str) -> list[str]:
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        # long paragraphs become sentences so a requirement is one unit
        parts = re.split(r"(?<=[.;])\s+(?=[A-Z])", line) if len(line) > 240 else [line]
        out.extend(p.strip() for p in parts if p.strip())
    return out


_LEAD = r"(?:(?:key|basic|minimum|preferred|required|your|our|the|technical|additional|desired|job|role)\s+)*"


def _heading(line: str) -> str | None:
    """The section a heading line opens, or None for an ordinary line. Marked headings (markdown #, **bold**, a
    trailing colon) are classified by keyword; a plain short line only when it is nothing but a heading phrase, so
    "Experience with Python" stays a requirement and is not taken for an "Experience" heading."""
    s = line.strip()
    bare = re.sub(r"^[#*_\s>|]+|[#*_:\s|]+$", "", s)
    if not bare or len(bare) > 70 or re.search(r"\d\s*\+?\s*(?:-|–|to)?\s*\d*\s*(?:years?|yrs?)\b", bare, re.I):
        return None   # "**4+ years**" is a requirement written in bold, not a heading
    marked = s.startswith("#") or (s.startswith("**") and s.rstrip(":").endswith("**")) or (s.endswith(":") and len(bare) <= 60)
    order = (("ignore", _IGNORE), ("preferred", _PREF), ("responsibilities", _RESP), ("required", _REQ))
    if marked:
        for name, pat in order:
            if pat.search(bare):
                return name
        return "other" if s.startswith(("#", "**")) else None
    if len(bare.split()) <= 6 and not re.search(r"[.,;]$", bare):
        for name, pat in order:
            if re.fullmatch(rf"{_LEAD}(?:{pat.pattern})", bare, re.I):
                return name
    return None


def split_sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"required": [], "preferred": [], "responsibilities": [], "other": [], "ignore": []}
    current = "other"
    for line in lines_of(text):
        h = _heading(line)
        if h:
            current = h
            continue
        bucket = current
        if bucket in ("required", "other") and _INLINE_PREF.search(line):
            bucket = "preferred"
        out[bucket].append(re.sub(r"^(?:[-*•·▪◦]+|\d{1,2}[.)])\s*", "", line.replace("**", "")).strip()[:400])
    return out


_NO_GRADS = re.compile(r"\bnot (?:currently )?(?:looking for|hiring|considering|open to) (?:new|recent|fresh) grad", re.I)
_ABOUT_COMPANY = re.compile(r"\b(founded|since \d{4}|in business|our (?:company|history|clients|customers)|we have been|"
                            r"track record|anniversary|warranty|years? ago|per year|a year|every year|years? old|vesting|"
                            r"contract (?:of|for)|duration|tenure of the)\b", re.I)
# a years statement about the person: "2+ years of Python", "3 years in ML", "4+ years" alone on a line
_YEARS_ABOUT_YOU = re.compile(r"\d\s*\+?\s*(?:-|–|to)?\s*\d*\s*\+?\s*(?:years?|yrs?)\b\s*(?:\+\s*)?(?:of|in|with|as|building|"
                              r"working|developing|designing|shipping|leading|hands[- ]on|professional|industry|relevant|"
                              r"production|\W*$)", re.I)


_WORDNUM = {w: str(i) for i, w in enumerate(("zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                                             "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen"))}
_WORDNUM_RE = re.compile(r"\b(" + "|".join(_WORDNUM) + r")(?=[\s-]*(?:\+|plus|or more|to|-|–|\(|years?|yrs?))", re.I)


def digits(line: str) -> str:
    """"three-plus years" -> "3+ years", "five or more years" -> "5+ years", "two to four years" -> "2 to 4 years"."""
    t = _WORDNUM_RE.sub(lambda m: _WORDNUM[m.group(1).lower()], line)
    t = re.sub(r"(\d)\s*-?\s*(?:plus|or more)\b", r"\1+", t, flags=re.I)
    return t


def years_required(lines: list[str]) -> tuple[float | None, float | None, str | None]:
    """(min, max, quote) from the first line that states the years a candidate needs. A line offering
    alternatives ("2+ years in ML, or 3+ years in software") needs the smallest of them."""
    for original in lines:
        line = digits(original)
        if _FRESH.search(line) and not _NO_GRADS.search(line) and not re.search(r"\bnot\b[^.]{0,30}\b(fresh|entry|new grad)", line, re.I) \
                and not _YEARS_MIN.search(line.replace("0-1", "")):
            return 0.0, 1.0, original[:200]
        if _ABOUT_COMPANY.search(line) or not (_EXP_WORD.search(line) or _YEARS_ABOUT_YOU.search(line)):
            continue
        ranges = [(float(a), float(b)) for a, b in _YEARS_RANGE.findall(line) if float(a) <= float(b) <= 40]
        mins = [float(m.group(1)) for m in _YEARS_MIN.finditer(line) if float(m.group(1)) <= 30]
        if ranges and (not mins or min(lo for lo, _ in ranges) <= min(mins)):
            lo, hi = min(ranges)
            return lo, hi, original[:200]
        if mins:
            return min(mins), None, original[:200]
    return None, None, None


_SENIOR_TEXT = re.compile(r"\b(?:looking for|seeking|hiring|searching for|you are|you're|we need|join us as)\s+an?\s+"
                          r"(?:[\w-]+\s+){0,3}?(senior|staff|principal|lead)\s+(?:[\w/-]+\s+){0,2}?"
                          r"(?:engineer|developer|scientist|architect|analyst|designer)", re.I)


def stated_level(text: str) -> int | None:
    """3 when the text says it wants a senior/staff/lead person, 1 for "not new grads", else None."""
    t = (text or "")[:6000]
    if _SENIOR_TEXT.search(t):
        return 3
    if _NO_GRADS.search(t):
        return 1
    return None


def work_mode(text: str, title: str = "", location: str = "") -> str:
    if _NOT_REMOTE.search(text or ""):
        return "hybrid" if _HYBRID.search(text or "") else "onsite"
    # header lines: short, label-like ("Remote - India", "Hybrid, Pune"), not sentences
    top = [ln.strip(" #*-") for ln in (text or "").splitlines()[:6]
           if 0 < len(ln.strip()) <= 40 and not re.search(r"[.!?]\s|[.!?]$|\band\b", ln.strip())]
    head = "\n".join([title, location, *top])   # the posting's own header lines ("Remote - India") say it outright
    low_head = head.lower()
    if _HYBRID.search(low_head):
        return "hybrid"
    if _REMOTE.search(low_head):
        return "remote"
    if _ONSITE.search(low_head):
        return "onsite"
    m = re.search(r"(?:workplace type|work (?:mode|type|arrangement|model)|location type|remote status)\s*[:\-]\s*([^\n]{2,40})",
                  text or "", re.I)
    if m:
        v = m.group(1).lower()
        return "hybrid" if "hybrid" in v else "remote" if "remote" in v else "onsite" if re.search(r"site|office", v) else "unknown"
    if _NOT_REMOTE.search(text or ""):
        return "hybrid" if _HYBRID.search(text or "") else "onsite"
    body = (text or "")[:6000]
    hy, rm, on = len(_HYBRID.findall(body)), len(_REMOTE.findall(body)), len(_ONSITE.findall(body))
    if hy and hy >= rm:
        return "hybrid"
    # a passing "remote Fridays" in the benefits is not a remote job: the body needs a strong phrase or repetition
    if (_STRONG_REMOTE.search(body) or rm >= 2) and rm > on + hy:
        return "remote"
    if on:
        return "onsite"
    return "unknown"


def remote_region(text: str) -> str | None:
    m = _REMOTE_REGION.search((text or "")[:4000] + "\n")
    if not m:
        return None
    code = geo.country_code(m.group(1)) or ({"emea": "EMEA", "europe": "EU", "eu": "EU", "latam": "LATAM",
                                              "apac": "APAC", "north america": "NA"}.get(m.group(1).strip().lower()))
    return code


def employment_type(text: str, title: str = "") -> str:
    head = title.lower()
    for name, pat in _TYPES:
        if re.search(pat, head, re.I):
            return name
    m = re.search(r"(?:employment type|job type|type of employment|commitment)\s*[:\-]\s*([^\n]{2,40})", text or "", re.I)
    scope = m.group(1) if m else (text or "")[:5000]
    for name, pat in _TYPES:
        if re.search(pat, scope, re.I):
            return name
    return "unknown"


_SAL_INR_LPA = re.compile(r"(?:₹|inr|rs\.?)?\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:-|–|to)\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:lpa|lakhs?(?:\s+per\s+annum)?|l\.?p\.?a\.?)\b", re.I)
_SAL_RANGE = re.compile(r"(₹|\$|£|€|inr|usd|gbp|eur|cad|aud|sgd|aed)\s*(\d[\d,]*(?:\.\d+)?)\s*(k|m)?\s*(?:-|–|to)\s*"
                        r"(?:₹|\$|£|€|inr|usd|gbp|eur|cad|aud|sgd|aed)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|m)?"
                        r"(?:\s*(?:per|/|a)\s*(year|yr|annum|month|mo|hour|hr))?", re.I)
_CUR = {"₹": "INR", "inr": "INR", "$": "USD", "usd": "USD", "£": "GBP", "gbp": "GBP", "€": "EUR", "eur": "EUR",
        "cad": "CAD", "aud": "AUD", "sgd": "SGD", "aed": "AED"}


def salary(text: str, country_code: str | None = None) -> Salary | None:
    t = (text or "")[:8000]
    m = _SAL_INR_LPA.search(t)
    if m:
        lo, hi = float(m.group(1)) * 100_000, float(m.group(2)) * 100_000
        if lo <= hi:
            return Salary(min=lo, max=hi, currency="INR", period="year", text=m.group(0).strip())
    m = _SAL_RANGE.search(t)
    if m:
        def num(v, unit):
            n = float(v.replace(",", ""))
            return n * (1000 if (unit or "").lower() == "k" else 1_000_000 if (unit or "").lower() == "m" else 1)
        lo, hi = num(m.group(2), m.group(3)), num(m.group(4), m.group(5) or m.group(3))
        cur = _CUR.get(m.group(1).lower())
        if cur == "USD" and country_code in ("CA", "AU", "SG", "NZ", "HK"):
            cur = geo.currency(country_code)  # "$" in a Canadian posting is CAD
        per = (m.group(6) or "").lower()
        period = "hour" if per in ("hour", "hr") else "month" if per in ("month", "mo") else "year" if per else (
            "hour" if hi < 500 else "month" if hi < 20_000 and cur not in ("INR",) else "year")
        if 0 < lo <= hi:
            return Salary(min=lo, max=hi, currency=cur, period=period, text=m.group(0).strip())
    return None


def annual(s: Salary | None) -> tuple[float | None, float | None]:
    if not s:
        return None, None
    mult = {"year": 1, "month": 12, "hour": 2080}.get(s.period)
    if not mult:
        return None, None
    return (s.min * mult if s.min else None), (s.max * mult if s.max else None)


_AGO = re.compile(r"(?:posted|reposted|published|active)?\s*(\d{1,3})\+?\s*(minute|hour|day|week|month)s?\s+ago", re.I)
_ISO = re.compile(r"(?:date ?posted|posted on|posted|published|datePosted\"?\s*:?\s*\"?)\s*[:\-]?\s*(20\d\d-\d\d-\d\d)", re.I)
_LONG = re.compile(r"(?:posted|published|date posted)\s*(?:on)?\s*[:\-]?\s*(\d{1,2}\s+[A-Za-z]{3,9},?\s+20\d\d|"
                   r"[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d\d)", re.I)
_VALID = re.compile(r"(?:validThrough\"?\s*:?\s*\"?|apply by|application deadline|closing date|last date to apply)\s*[:\-]?\s*"
                    r"(20\d\d-\d\d-\d\d|\d{1,2}\s+[A-Za-z]{3,9},?\s+20\d\d|[A-Za-z]{3,9}\s+\d{1,2},?\s+20\d\d)", re.I)


def _date(text: str) -> dt.date | None:
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%d %B, %Y", "%B %d, %Y", "%b %d, %Y", "%d %b, %Y"):
        try:
            return dt.datetime.strptime(" ".join(text.split()), fmt).date()
        except ValueError:
            continue
    return None


def age_upper_days(n: int, unit: str) -> int:
    """The oldest a posting can be when a site says "<n> <unit> ago": sites round down, so "3 weeks ago" is 21 to 27
    days and "1 month ago" 30 to 59. Freshness is judged on the old end, so a stale job never reads as fresh."""
    unit = unit.lower().rstrip("s")
    return {"minute": 0, "hour": 1 if n >= 12 else 0, "day": n, "week": 7 * n + 6, "month": 30 * n + 29}[unit]


def posted(text: str, today: dt.date) -> tuple[str | None, str | None]:
    """(ISO date, the words it came from), only when the posting states it."""
    t = (text or "")[:8000]
    m = _ISO.search(t)
    if m and (d := _date(m.group(1))) and d <= today:
        return d.isoformat(), m.group(0).strip()[:60]
    m = _LONG.search(t)
    if m and (d := _date(m.group(1).replace(",", ""))) and d <= today:
        return d.isoformat(), m.group(0).strip()[:60]
    m = _AGO.search(t)
    if m:
        return (today - dt.timedelta(days=age_upper_days(int(m.group(1)), m.group(2)))).isoformat(), m.group(0).strip()[:60]
    if re.search(r"\b(just posted|posted today|posted\s+just now)\b", t, re.I):
        return today.isoformat(), "posted today"
    return None, None


def valid_through(text: str) -> dt.date | None:
    m = _VALID.search((text or "")[:8000])
    return _date(m.group(1).replace(",", "")) if m else None


def closed_signal(text: str, extra: tuple[str, ...] = ()) -> str | None:
    """The closed-posting phrase the page shows about this posting, or None. A phrase counts only where its sentence
    is not about agencies, other roles or a talent pool."""
    low = " ".join((text or "").lower().split())[:20000]
    for phrase in (*extra, *CLOSED):
        start = low.find(phrase)
        while start != -1:
            a = max(low.rfind(".", 0, start), low.rfind("!", 0, start), low.rfind("?", 0, start)) + 1
            ends = [i for i in (low.find(c, start + len(phrase)) for c in ".!?") if i != -1]
            if not _CLOSED_ASIDE.search(low[a:min(ends) if ends else len(low)]):
                return phrase
            start = low.find(phrase, start + len(phrase))
    return None


def apply_signal(text: str) -> str | None:
    low = " ".join((text or "").lower().split())
    return next((p for p in APPLY if p in low), None)


def degree_required(lines: list[str]) -> tuple[str | None, str | None]:
    for line in lines:
        low = line.lower()
        if re.search(r"or equivalent|equivalent (?:practical )?experience|or related experience|preferred|a plus|"
                     r"not (?:required|mandatory|necessary|a must)|advantage|welcome|nice to have|bonus|ideally|"
                     r"desirable|optional|helpful|beneficial", low):
            continue
        if not re.search(r"\b(required|must|mandatory|need)\b|degree in", low):
            continue
        for name, pat in _DEGREE:
            if re.search(pat, low):
                return name, line[:200]
    return None, None


_OR = re.compile(r"\bor\b|/|\beither\b|\bany of\b|\bone of\b|\bsimilar\b", re.I)


def alternatives(lines: list[str]) -> list[list[str]]:
    """Skills a line offers as either-or ("Power BI or Tableau", "Django/FastAPI"): one requirement, not several."""
    out = []
    for line in lines:
        if not _OR.search(line):
            continue
        found = sorted(sk.find(line))
        if len(found) >= 2:
            # only skills that sit around an "or" are alternatives; "Python and SQL or Spark" keeps Python required
            parts = re.split(r",|\band\b|;", line)
            for part in parts:
                group = sorted(sk.find(part))
                if len(group) >= 2 and _OR.search(part) and group not in out:
                    out.append(group)
    return out


_TAIL = re.compile(r"\n[^\n]{0,40}(similar jobs|people also viewed|similar searches|more jobs from|recommended jobs|"
                   r"jobs you may be interested in|explore collaborative articles|referrals increase your chances|"
                   r"every person working for|corporate security responsibility|equal (?:employment )?opportunity employer|"
                   r"### seniority level|seniority level\s*$)", re.I | re.M)


def focus(text: str) -> str:
    """The posting itself: everything before the first boilerplate tail (other jobs, security and legal text)."""
    t = text or ""
    m = _TAIL.search(t, 300)
    return t[:m.start()] if m else t


def years_in(quote: str) -> tuple[float | None, float | None, str | None]:
    """The years in a sentence already known to state the experience requirement (no "is this about the
    candidate?" test needed); the smallest alternative when it offers several."""
    line = digits(quote)
    if _ABOUT_COMPANY.search(line) and not _EXP_WORD.search(line):
        return None, None, None
    ranges = [(float(a), float(b)) for a, b in _YEARS_RANGE.findall(line) if float(a) <= float(b) <= 40]
    mins = [float(m.group(1)) for m in _YEARS_MIN.finditer(line) if float(m.group(1)) <= 30]
    if ranges and (not mins or min(lo for lo, _ in ranges) <= min(mins)):
        lo, hi = min(ranges)
        return lo, hi, quote[:200]
    if mins:
        return min(mins), None, quote[:200]
    return years_required([quote])


def apply_audit(req: Requirements, found: dict) -> tuple[Requirements, list[str]]:
    """Fold a grounded requirement check into the requirements. Every number comes from code reading the quote."""
    upd, notes = {}, []
    if req.min_years is None and found.get("experience"):
        lo, hi, quote = years_in(found["experience"])
        if lo is None and _FRESH.search(found["experience"]):
            lo, hi, quote = 0.0, 1.0, found["experience"]
        if lo is not None:
            upd.update(min_years=lo, max_years=hi, years_quote=quote)
            notes.append(f"experience found on re-check: \"{quote[:120]}\"")
    if req.stated_level is None and found.get("seniority"):
        level = stated_level(found["seniority"]) or (3 if re.search(r"\b(senior|staff|principal|lead)\b", found["seniority"], re.I)
                                                    else 1 if _NO_GRADS.search(found["seniority"]) else None)
        if level:
            upd["stated_level"] = level
            notes.append(f"seniority found on re-check: \"{found['seniority'][:120]}\"")
    if req.degree_required is None and found.get("degree"):
        deg, quote = degree_required([found["degree"]])
        if deg:
            upd.update(degree_required=deg, degree_quote=quote)
            notes.append(f"degree requirement found on re-check: \"{quote[:120]}\"")
    return (req.model_copy(update=upd) if upd else req), notes


def parse(text: str, title: str = "") -> Requirements:
    secs = split_sections(focus(text))
    required_text = "\n".join(secs["required"])
    preferred_text = "\n".join(secs["preferred"])
    structured = bool(secs["required"])
    if structured:
        body_for_skills = required_text
    elif secs["preferred"]:
        # no "Requirements" heading but a "Preferred Qualifications" list (common on LinkedIn): it is the only list
        # of qualifications the posting has, so it is what the job requires, together with the description's lines
        lines = secs["other"] + secs["responsibilities"] + secs["preferred"]
        body_for_skills, preferred_text = "\n".join(lines), ""
        secs["required"], secs["preferred"] = lines[:30], []
    else:  # no "Requirements" heading: the description's own lines carry what the job needs
        body_for_skills = "\n".join(secs["other"] + secs["responsibilities"])
        if not secs["required"]:
            secs["required"] = (secs["other"] + secs["responsibilities"])[:30]
    found = set(sk.find(f"{title}\n{body_for_skills}"))
    req = found - sk.SOFT                                   # soft skills rank as preferred, never as must-haves
    pref = (set(sk.find(preferred_text)) | (found & sk.SOFT)) - req
    if structured:  # skills named only under responsibilities are expectations, not hard requirements
        pref |= set(sk.find("\n".join(secs["responsibilities"]))) - req
    lo, hi, quote = years_required(secs["required"] + secs["other"] + secs["responsibilities"] + secs["preferred"])
    deg, deg_q = degree_required(secs["required"] + secs["other"])
    if lo is None or deg is None:
        # catch-all: a heading the section reader mislabels ("About you" read as "About us") must never hide a
        # requirement, so every line of the posting itself is scanned once more, whatever section it fell in
        every = [re.sub(r"^(?:[-*•·▪◦]+|\d{1,2}[.)])\s*", "", ln.replace("**", "")).strip() for ln in lines_of(focus(text))]
        if lo is None:
            lo, hi, quote = years_required(every)
        if deg is None:
            deg, deg_q = degree_required(every)
    auth = list(dict.fromkeys(m.group(0).strip()[:160] for m in _AUTH.finditer(text or "")
                              if not re.search(r"without regard|regardless of|irrespective of|discriminat", m.group(0), re.I)))[:3]
    return Requirements(
        required_skills=sorted(req), preferred_skills=sorted(pref),
        alternatives=[g for g in alternatives(secs["required"] if structured else secs["other"]) if set(g) <= req],
        required_lines=secs["required"][:30],
        preferred_lines=secs["preferred"][:15], responsibility_lines=secs["responsibilities"][:20],
        min_years=lo, max_years=hi, years_quote=quote, degree_required=deg, degree_quote=deg_q, authorization=auth,
        stated_level=stated_level(focus(text)),
        domains=sk.find_domains("\n".join(secs["other"][:20] + secs["responsibilities"])))
