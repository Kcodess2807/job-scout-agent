"""The form answers (and a follow-up message) -> raw preferences. Pure. Hard constraints and soft preferences are
kept apart here: "remote" is remote-only (a hard filter), "prefer remote" only ranks."""
from __future__ import annotations

import re

import geo

MIN_COUNT, MAX_COUNT, DEFAULT_COUNT = 1, 25, 10

_MODES = {"either": "either", "any": "either", "prefer remote": "prefer_remote", "remote preferred": "prefer_remote",
          "remote": "remote_only", "remote only": "remote_only", "on-site or hybrid": "no_remote",
          "onsite or hybrid": "no_remote", "on-site": "no_remote", "hybrid": "no_remote"}
_TYPES = {"any": "any", "full-time": "full_time", "full time": "full_time", "part-time": "part_time",
          "part time": "part_time", "contract": "contract"}


def _text(v) -> str:
    return " ".join(str(v or "").split())


def _number(v) -> float | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, str):  # "2 years", "1.5 yrs", "6 months"
        m = re.search(r"\d+(?:\.\d+)?", v.replace(",", ""))
        if not m:
            return None
        n = float(m.group(0))
        return round(n / 12, 2) if re.search(r"\bmonths?\b", v, re.I) else n
    try:
        n = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def salary(v) -> float | None:
    """'600000', '6,00,000', '6 LPA', '6 lakh', '1.2 crore', '80k', '$90,000' -> a yearly amount."""
    if v is None or isinstance(v, (int, float)):
        return _number(v)
    t = _text(v).lower().replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", t)
    if not m:
        return None
    n, rest = float(m.group(0)), t[m.end():]
    if re.match(r"\s*(lpa|l\b|lakhs?|lacs?)", rest):
        n *= 100_000
    elif re.match(r"\s*(cr|crores?)\b", rest):
        n *= 10_000_000
    elif re.match(r"\s*k\b", rest):
        n *= 1_000
    elif re.match(r"\s*(m|mn|million)\b", rest):
        n *= 1_000_000
    if re.search(r"\b(per month|a month|monthly|/\s*month|pm)\b", t):
        n *= 12
    return n or None


def split_list(v, *, places: bool = False) -> list[str]:
    """'Pune, Bangalore' / 'Pune or Bangalore' -> two items. Roles keep slashes ('AI/ML Engineer')."""
    if isinstance(v, list):
        items = [str(x) for x in v]
    else:
        items = re.split(r"[,;/|]|\bor\b|\band\b" if places else r"[;|]|,\s|\bor\b", _text(v))
    return [i.strip(" .") for i in items if i and i.strip(" .")]


def read_inputs(inputs: dict) -> dict:
    """What the person typed, normalised, with `None` for what they left out (so clarify can tell)."""
    count_raw = _number(inputs.get("count"))
    count = int(count_raw) if count_raw else None
    count_note = None
    if count is not None and not MIN_COUNT <= count <= MAX_COUNT:
        clamped = max(MIN_COUNT, min(MAX_COUNT, count))
        count_note = f"You asked for {count}; one run looks for {MIN_COUNT} to {MAX_COUNT}, so it looked for {clamped}."
        count = clamped
    country_text = _text(inputs.get("country"))
    code = geo.country_code(country_text)
    cities = []
    for c in split_list(inputs.get("city"), places=True):   # "Pune preferred, open to Bangalore" -> Pune, Bengaluru
        hits = [name for name, _ in geo.find_cities(c)]
        cities.extend(hits or [c.title()])
    mode_raw = _text(inputs.get("remote")).lower()
    type_raw = _text(inputs.get("employment_type")).lower()
    # the fit bar is fixed by the system (Policy.min_score); a caller may still pass strictness="strict"
    strict = _text(inputs.get("strictness")).lower().startswith("strict")
    likely = str(inputs.get("include_likely_active", "")).strip().lower()
    return {
        "roles": split_list(inputs.get("role"))[:4],
        "internships": _text(inputs.get("looking_for") or "jobs").lower().startswith("intern"),
        "country": geo.country_name(code) or country_text,
        "country_code": code,
        "cities": list(dict.fromkeys(cities)),
        "work_mode": _MODES.get(mode_raw) if mode_raw else None,
        "employment_type": _TYPES.get(type_raw, "any") if type_raw else "any",
        "count": count, "count_note": count_note,
        "years": _number(inputs.get("experience_years")),
        "min_salary": salary(inputs.get("min_salary")),
        "strict": strict,
        # likely-open jobs count by default; only an explicit "false" from a caller turns that off
        "include_likely_active": likely not in ("false", "0", "no", "off"),
        "message": _text(inputs.get("message")),
    }


# ---- a follow-up message ("I have 3 years", "remote only", "also Bangalore", "look for data engineer roles")

def corrections(message: str) -> dict:
    """The few corrections a follow-up message can make, read deterministically. Unknown wording changes nothing."""
    msg = _text(message)
    low = msg.lower()
    out: dict = {}
    if not msg:
        return out
    m = re.search(r"\b(?:i have|i've got|with|actually)\s+(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)", low)
    if m:
        out["years"] = float(m.group(1))
    elif re.search(r"\b(i am|i'm) a fresher\b|\bno (work )?experience\b", low):
        out["years"] = 0.0
    if re.search(r"\bremote only\b|\bonly remote\b|\bfully remote\b", low):
        out["work_mode"] = "remote_only"
    elif re.search(r"\bprefer(?:ably)? remote\b|\bremote preferred\b", low):
        out["work_mode"] = "prefer_remote"
    elif re.search(r"\bno remote\b|\bon-?site only\b|\bin[- ]office\b", low):
        out["work_mode"] = "no_remote"
    m = re.search(r"\b(?:also|or|add)\s+(?:in\s+)?([a-z][a-z ]{2,30}?)(?:\s+(?:too|as well))?(?:[.,]|$)", low)
    if m and geo.canonical_city(m.group(1)):
        out["add_cities"] = [geo.canonical_city(m.group(1))]
    m = re.search(r"\b(?:only|just)\s+(?:in\s+)?([a-z][a-z ]{2,30}?)(?:[.,]|$)", low)
    if m and geo.canonical_city(m.group(1)):
        out["cities"] = [geo.canonical_city(m.group(1))]
    m = re.search(r"\b(?:look(?:ing)? for|search for|find(?: me)?|switch to|instead,?)\s+(?:an?\s+)?"
                  r"([a-z][a-z /&+-]{2,40}?)\s+(?:roles?|jobs?|positions?|internships?)\b", low)
    if m and not re.fullmatch(r"(more|other|similar|new|some|\d+)", m.group(1).strip()):
        out["roles"] = [m.group(1).strip().title()]
    m = re.search(r"\b(?:show|find|give me|get)\s+(?:me\s+)?(\d{1,2})\b", low)
    if m:
        out["count"] = max(MIN_COUNT, min(MAX_COUNT, int(m.group(1))))
    if re.search(r"\bstricter\b|\bstrict\b|\bonly (the )?best\b", low):
        out["strict"] = True
    return out
