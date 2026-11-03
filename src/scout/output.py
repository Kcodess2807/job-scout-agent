"""ScoutOutcome -> `result` (what the screen, chat and a teammate's component read) and `corpus` (what Gravity renders
for the `report` and `shortlist` outputs). Every value here was observed or computed this run; nothing is filled in.

result keys (stable contract, see README "Output contract"):
  headline, requested_count, returned_count, minimum_match_score, search_status, searched_sources,
  jobs[JobOut], shortlist[compact rows], leads[compact rows], profile, search_summary, sources, unavailable,
  warnings, assumptions, note"""
from __future__ import annotations

import re

from evaluator import computed_reason
from models import CandidateProfile, ExperienceRequired, JobOut, Listing, Match, ScoutOutcome, SearchPrefs

STATUS_WORDS = {"verified_active": "Verified open", "likely_active": "Likely open", "unverified": "Not verified",
                "closed": "Closed"}
MODE_WORDS = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site", "unknown": "Not stated"}


def _numbers_ok(text: str, allowed: str) -> bool:
    return all(n in allowed for n in re.findall(r"\d+(?:\.\d+)?", text))


def reason_for(listing: Listing, match: Match, written: dict[str, str]) -> str:
    computed = computed_reason(listing, match)
    for candidate in (written.get(listing.key), match.reason):
        if candidate and _numbers_ok(candidate, computed + listing.title + (listing.description or listing.snippet)):
            return candidate
    return computed


def job_out(rank: int, listing: Listing, match: Match, reason: str, seen: set[str]) -> JobOut:
    req = listing.requirements
    exp = ExperienceRequired(min_years=req.min_years, max_years=req.max_years) if req.min_years is not None else None
    return JobOut(
        rank=rank, job_id=listing.key, title=listing.title, company=listing.company, company_domain=listing.company_domain,
        location=listing.location, work_mode=listing.work_mode, employment_type=listing.employment_type,
        experience_required=exp, salary=listing.salary, required_skills=req.required_skills,
        preferred_skills=req.preferred_skills, matched_skills=match.matched_skills,
        transferable_skills=match.transferable_skills, missing_skills=match.missing_skills, match_score=match.score,
        score_breakdown=match.breakdown, match_confidence=match.confidence, match_reason=reason,
        eligibility_concerns=match.concerns, source=listing.source, source_url=listing.source_url,
        canonical_url=listing.canonical_url, application_url=listing.application_url,
        application_url_checked=listing.verification.application_url_checked, also_seen_at=listing.also_seen_at,
        posted_at=listing.posted_at, verified_at=listing.verification.checked_at,
        verification_status=listing.verification.status, verification_evidence=listing.verification.evidence,
        new=listing.key not in seen)


def compact(j: JobOut) -> dict:
    return {"rank": j.rank, "title": j.title, "company": j.company or "", "location": j.location or "",
            "work_mode": MODE_WORDS[j.work_mode], "score": j.match_score, "status": STATUS_WORDS[j.verification_status],
            "posted": j.posted_at or "", "why": j.match_reason, "url": j.application_url or j.canonical_url, "new": j.new}


def _exp(j: JobOut) -> str:
    e = j.experience_required
    if not e:
        return "Not stated"
    return f"{e.min_years:g}-{e.max_years:g} yrs" if e.max_years is not None else f"{e.min_years:g}+ yrs"


def _salary(j: JobOut) -> str:
    return j.salary.text if j.salary else "Not stated"


def profile_view(profile: CandidateProfile, prefs: SearchPrefs, info: dict) -> dict:
    return {
        "roles": prefs.target_roles, "role_from": prefs.role_provenance,
        "years_experience": profile.years(), "years_from": profile.years_experience.provenance,
        "seniority": profile.seniority.value, "primary_skills": profile.primary_skills[:10],
        "other_skills": profile.secondary_skills[:10], "recent_titles": profile.titles[:3],
        "location": profile.location.value, "country": prefs.country, "cities": prefs.cities,
        "work_mode": prefs.work_mode, "looking_for": "internships" if prefs.internships else "jobs",
        "asked": info.get("asked"), "notes": profile.notes[:4]}


def build(outcome: ScoutOutcome, prefs: SearchPrefs, profile: CandidateProfile, info: dict, resume: dict,
          written: dict[str, str], seen: set[str]) -> tuple[dict, dict]:
    near = set(outcome.near_misses)
    jobs, leads = [], []
    for listing in outcome.jobs:
        m = outcome.matches[listing.key]
        target = leads if listing.key in near else jobs
        target.append(job_out(len(target) + 1, listing, m, reason_for(listing, m, written), seen))
    kind = "internships" if prefs.internships else "jobs"
    role = prefs.target_roles[0]
    where = ", ".join(prefs.cities[:2] + ([prefs.country] if prefs.country else [])) or "anywhere"
    if jobs:
        headline = (f"{len(jobs)} of {outcome.requested_count} {role} {kind} in {where} matched your profile at "
                    f"{outcome.minimum_match_score}+ and are verified open.")
    elif outcome.search_status == "failed":
        headline = "The search could not run: the job search source did not answer."
    else:
        headline = (f"No {role} {kind} in {where} met the standard ({outcome.minimum_match_score}+ fit, verified open) "
                    "within this run's search budget.")
    if outcome.search_status == "cancelled":
        headline = "The search was stopped. " + headline
    unavailable = [{"source": r.source, "reason": r.reason or "not available"} for r in outcome.sources
                   if r.status == "unavailable"]
    note = resume.get("note")
    result = {
        "headline": headline,
        "requested_count": outcome.requested_count,
        "returned_count": len(jobs),
        "minimum_match_score": outcome.minimum_match_score,
        "search_status": outcome.search_status,
        "searched_sources": outcome.searched_sources,
        "shortlist": [compact(j) for j in jobs],
        "leads": [compact(j) for j in leads],
        "jobs": [j.model_dump(mode="json") for j in jobs],
        "lead_details": [j.model_dump(mode="json") for j in leads],
        "profile": profile_view(profile, prefs, info),
        "assumptions": prefs.assumptions + ([prefs.count_note] if prefs.count_note else []),
        "search_summary": outcome.summary.model_dump(mode="json"),
        "sources": [r.model_dump(mode="json") for r in outcome.sources],
        "unavailable": unavailable,
        "warnings": outcome.warnings,
        "resume": resume.get("source"),
        "note": note,
        "score_note": "The fit score is a heuristic for ranking, not a probability of being hired; it is not yet calibrated.",
    }
    return result, corpus(result, jobs, leads, prefs)


def corpus(result: dict, jobs: list[JobOut], leads: list[JobOut], prefs: SearchPrefs) -> dict:
    kind = "internships" if prefs.internships else "jobs"
    columns = ["#", "Job", "Company", "Location", "Mode", "Experience", "Salary", "Fit", "Status", "Posted",
               "Matched skills", "Missing or unsure", "Why", "Apply"]

    def row(j: JobOut) -> list[str]:
        missing = j.missing_skills[:4] + j.eligibility_concerns[:1]
        return [str(j.rank), j.title, j.company or "", j.location or "", MODE_WORDS[j.work_mode], _exp(j), _salary(j),
                str(j.match_score), STATUS_WORDS[j.verification_status], j.posted_at or "Not stated",
                ", ".join(j.matched_skills[:6]), "; ".join(missing), j.match_reason, j.application_url or j.canonical_url]

    sections = [{
        "heading": f"Your {len(jobs)} best-fit {kind}" if jobs else f"No {kind} met the standard",
        "body": ("Ranked by fit to your resume, then by how sure the evidence is that each is open. "
                 if jobs else "Nothing was lowered to fill the list. ") + " ".join(result["warnings"][:2]),
        "table": {"columns": columns, "rows": [row(j) for j in jobs]},
    }]
    if leads:
        sections.append({
            "heading": "Leads that did not qualify",
            "body": "Close matches that fell below the score or could not be verified as open. Check them yourself.",
            "table": {"columns": columns, "rows": [row(j) for j in leads]}})
    details = []
    for j in jobs:
        bullets = [f"Fit {j.match_score}/100 ({j.match_confidence} confidence): {j.match_reason}"]
        if j.matched_skills:
            bullets.append("Matched: " + ", ".join(j.matched_skills))
        if j.transferable_skills:
            bullets.append("Related experience: " + ", ".join(j.transferable_skills))
        if j.missing_skills:
            bullets.append("Missing: " + ", ".join(j.missing_skills))
        bullets += [f"Check: {c}" for c in j.eligibility_concerns[:3]]
        bullets.append(f"{STATUS_WORDS[j.verification_status]}: " + "; ".join(j.verification_evidence))
        bullets.append(f"Apply: {j.application_url or j.canonical_url}"
                       + ("" if j.application_url_checked else " (the posting page was read; the application form itself was not opened)"))
        if j.also_seen_at:
            bullets.append("Also listed at: " + ", ".join(j.also_seen_at[:3]))
        details.append({"heading": f"{j.rank}. {j.title}" + (f" at {j.company}" if j.company else ""), "bullets": bullets})
    if details:
        sections.append({"heading": "Each match in detail", "body": "", "sections": details})
    p = result["profile"]
    sections.append({
        "heading": "What the search used",
        "bullets": [f"Roles: {', '.join(p['roles'])} ({p['role_from']})",
                    f"Experience: {p['years_experience']:g} years ({p['years_from']})" if p["years_experience"] is not None
                    else "Experience: unknown",
                    f"Skills: {', '.join(p['primary_skills']) or 'none read'}",
                    f"Where: {', '.join(p['cities']) or 'any city'}{', ' + p['country'] if p['country'] else ''}; "
                    f"work arrangement: {p['work_mode'].replace('_', ' ')}"] + result["assumptions"] + p["notes"]})
    ss = result["search_summary"]
    sections.append({
        "heading": "How the search went",
        "facts": [{"label": "Results read", "value": ss["total_candidates_discovered"], "source": "s1"},
                  {"label": "Duplicates removed", "value": ss["duplicates_removed"], "source": "s1"},
                  {"label": "Closed postings rejected", "value": ss["inactive_jobs_rejected"], "source": "s1"},
                  {"label": "Rejected on a hard requirement", "value": ss["eligibility_rejections"], "source": "s1"},
                  {"label": "Below the fit threshold", "value": ss["below_threshold_rejections"], "source": "s1"},
                  {"label": "Qualified", "value": ss["qualified"], "source": "s1"},
                  {"label": "Searches", "value": ss["search_calls"], "source": "s1"},
                  {"label": "Postings opened", "value": ss["page_calls"], "source": "s1"}],
        "bullets": [f"{r['source']}: {r['queries']} searches, {r['postings']} postings, {r['qualified']} qualified"
                    + (f" ({r['status']}: {r['reason']})" if r["reason"] else "") for r in result["sources"]][:12]})
    return {"title": f"{prefs.target_roles[0]} {kind} matched to your profile",
            "summary": result["headline"] + " " + result["score_note"],
            "sections": sections,
            "sources": [{"id": "s1", "title": "Web search results and posting pages read through Gravity during this run"}]}
