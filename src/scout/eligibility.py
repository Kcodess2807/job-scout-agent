"""Stage A: hard constraints, in code. A posting is rejected only for a *confirmed* conflict with something the person
fixed (country, remote-only, internship vs job, employment type, a salary floor) or a requirement clearly beyond
them (years well over theirs, a degree level they do not have). What the posting leaves out is a concern, never a
rejection."""
from __future__ import annotations

import re

import geo
import jd
from config import Policy
from models import CandidateProfile, Listing, SearchPrefs
from profile import DEGREE_RANK

_SENIOR_TITLE = re.compile(r"\b(senior|sr\.?|staff|principal|lead|head|director|vp|vice president|architect|manager)\b", re.I)
_TOP_TITLE = re.compile(r"\b(staff|principal|head|director|vp|vice president|chief)\b", re.I)
_REGION_COUNTRIES = {"EU": {"DE", "FR", "NL", "IE", "ES", "IT", "SE", "PL", "PT", "BE", "DK", "FI", "AT", "CZ"},
                     "EMEA": None, "APAC": {"IN", "SG", "AU", "NZ", "JP", "KR", "HK", "MY", "PH", "ID", "VN", "TH"},
                     "LATAM": {"BR", "MX", "AR", "CL", "CO"}, "NA": {"US", "CA"}}


def within_years(lo: float, hi: float | None, years: float) -> bool:
    """The "at most my years" rule: a fresher role (minimum 0), a minimum below the person's years ("0-2" for 1),
    or a range that stays within them ("0-1" for 1). "1+" and "1-2" go above 1 year, so they are out."""
    if lo == 0 or lo < years:
        return True
    return hi is not None and hi <= years


def title_level(title: str) -> int | None:
    t = title.lower()
    if re.search(r"\b(intern|internship|trainee|apprentice)\b", t):
        return 0
    if re.search(r"\b(junior|jr\.?|graduate|entry|associate|fresher)\b", t):
        return 1
    if _TOP_TITLE.search(t):
        return 4
    if re.search(r"\b(senior|sr\.?|lead)\b", t):
        return 3
    return None


def check(listing: Listing, profile: CandidateProfile, prefs: SearchPrefs, policy: Policy) -> tuple[list[str], list[str]]:
    """(rejections, concerns)."""
    rej: list[str] = []
    con: list[str] = []
    years = profile.years()
    req = listing.requirements

    # employment type: internships vs jobs, and the type the person fixed
    etype = listing.employment_type
    if prefs.internships and etype not in ("internship", "unknown") and not re.search(r"\bintern", listing.title, re.I):
        rej.append("employment_type: not an internship")
    if not prefs.internships and (etype == "internship" or re.search(r"\binterns?(hip)?\b", listing.title, re.I)):
        rej.append("employment_type: an internship, not a job")
    if prefs.employment_type != "any" and etype not in ("unknown", "internship") and etype != prefs.employment_type:
        rej.append(f"employment_type: {etype.replace('_', '-')}, you asked for {prefs.employment_type.replace('_', '-')}")

    # work arrangement
    mode = listing.work_mode
    if prefs.work_mode == "remote_only":
        if mode in ("onsite", "hybrid"):
            rej.append(f"work_mode: {mode}, you asked for remote only")
        elif mode == "unknown":
            said = f"{listing.title}\n{listing.location or ''}\n{listing.snippet}\n{listing.description or ''}"
            if (listing.location_cities or listing.location) and not re.search(r"\bremote\b|work from home|\bwfh\b",
                                                                              said, re.I):
                # a job tied to a city that never says remote is, in practice, on-site
                rej.append("work_mode: names an office location and never says remote; you asked for remote only")
            else:
                con.append("The posting does not say whether it is remote.")
    if prefs.work_mode == "no_remote" and mode == "remote":
        con.append("This one is remote; you asked for on-site or hybrid.")

    # location: the country is a hard constraint; a remote job restricted to another region is too
    want = prefs.country_code
    if want:
        if mode == "remote":
            region = listing.remote_region
            allowed = _REGION_COUNTRIES.get(region or "", {region} if region else None)
            head = f"{listing.location or ''}\n{listing.snippet}\n{(listing.description or '')[:2500]}"
            # only statements about where THIS role hires count ("remote - worldwide", "work from anywhere",
            # "open to candidates in India"); a company blurb about "global teams" or "teams anywhere" does not
            open_to_you = bool(re.search(
                rf"remote\s*[-–(:,/]?\s*(?:\()?\s*(?:worldwide|global|anywhere|{re.escape(prefs.country or 'xx')})\b|"
                rf"(?:work|working|hire|hiring|based)\s+(?:from\s+)?(?:anywhere|worldwide)\b|anywhere in the world|"
                rf"open to (?:candidates|applicants|people)\s+(?:from|in|across)\s+(?:any|all|{re.escape(prefs.country or 'xx')})|"
                rf"location\W{{0,6}}(?:remote\W{{0,4}})?(?:worldwide|anywhere|global|{re.escape(prefs.country or 'xx')})\b|"
                rf"\b{re.escape(prefs.country or 'xx')}\b[^\n.]{{0,40}}\bremote\b",
                head, re.I))
            if region and allowed is not None and want not in allowed:
                rej.append(f"location: remote only for {region}")
            elif not region:
                based = listing.location_country or next(
                    (geo.city_country(c) for c in listing.location_cities if geo.city_country(c)), None)
                if based and based != want and not open_to_you:
                    # a remote job based in another country is, unless it says otherwise, hiring there
                    rej.append(f"location: remote from {geo.country_name(based) or based}, not open to {prefs.country}")
                elif not based and not open_to_you:
                    con.append("Remote, but the posting does not say which countries it hires in.")
        else:
            country = listing.location_country or (geo.find_country(listing.location or "") if listing.location else None)
            city_countries = {geo.city_country(c) for c in listing.location_cities} - {None}
            if want in city_countries:
                country = want   # "London or Pune": one of its places is in the wanted country
            if country and country != want:
                if not (prefs.work_mode in ("either", "prefer_remote", "remote_only") and mode == "unknown"
                        and re.search(r"remote", f"{listing.location} {listing.snippet}", re.I)):
                    rej.append(f"location: in {geo.country_name(country) or country}, not {prefs.country}")
            elif not country and not listing.location_cities:
                con.append("The posting does not name its location.")
    if prefs.cities and mode != "remote" and listing.location_cities and not set(listing.location_cities) & set(prefs.cities):
        con.append(f"In {', '.join(listing.location_cities[:2])}, not one of your cities.")

    # experience: well beyond the person's years is a rejection, a little beyond is a penalty (scoring)
    if req.min_years is not None:
        if years is None:
            con.append(f"Asks for {req.min_years:g}+ years; your experience is unknown.")
        else:
            gap = req.min_years - years
            tolerance = max(policy.experience_tolerance, policy.experience_tolerance_share * req.min_years)
            ask = (f"{req.min_years:g}-{req.max_years:g}" if req.max_years is not None else f"{req.min_years:g}+")
            if gap > tolerance:
                rej.append(f"experience: asks for {ask} years, you have {years:g}")
            elif policy.experience_at_most and not within_years(req.min_years, req.max_years, years):
                rej.append(f"experience: asks for {ask} years, which goes above your {years:g}")
    said = req.stated_level
    if years is not None and req.min_years is None and said is not None:
        if said >= 3 and years < 3:
            rej.append("seniority: the posting asks for a senior engineer")
        elif said == 1 and years < 1:
            rej.append("seniority: the posting is not hiring new graduates")
    lvl = title_level(listing.title)
    if years is not None and lvl is not None and req.min_years is None:
        if lvl >= 4 and years < 6:
            rej.append("seniority: a staff/principal/director-level title")
        elif lvl == 3 and years < 2.5:
            rej.append("seniority: a senior title")

    # degree level stated as required
    if req.degree_required and profile.highest_degree:
        if DEGREE_RANK[profile.highest_degree] < DEGREE_RANK.get(req.degree_required, 0):
            rej.append(f"qualification: requires a {req.degree_required} degree")
    elif req.degree_required and profile.resume_present:
        con.append(f"Requires a {req.degree_required} degree; your resume does not show your degree level.")

    # salary: only when both sides give a number in the same currency
    if prefs.min_salary and listing.salary:
        lo, hi = jd.annual(listing.salary)
        cur = geo.currency(prefs.country_code)
        if hi and listing.salary.currency and cur and listing.salary.currency == cur and hi < prefs.min_salary:
            rej.append(f"salary: up to {listing.salary.text}, under your minimum")
        elif listing.salary.currency and cur and listing.salary.currency != cur:
            con.append(f"Salary is in {listing.salary.currency}, so it was not compared with your minimum.")
    elif prefs.min_salary and not listing.salary:
        con.append("No salary given, so your minimum could not be checked.")

    for line in req.authorization[:1]:
        con.append(f"Work authorisation: \"{line}\"")
    return rej, con
