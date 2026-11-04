"""Is the posting open? Classified from the strongest evidence this run actually observed:

- verified_active: the employer's own page (an ATS board or its careers site) was read just now, shows this job,
  shows an application control, and says nothing about being closed;
- likely_active: the page was read and shows the job but the evidence is weaker (a board re-post, or no apply
  control visible), and, for a board, it was posted recently;
- unverified: the page was not read (budget, a site that refuses reads, a failed read) or did not show the job;
- closed: the page says so, its deadline passed, or the employer's posting URL is gone (404/410 or a redirect to the
  board's index).

A posting's age alone never makes it active or closed; only a read page does. Nothing is claimed about a link that
was not read."""
from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlsplit

import jd
from config import Policy
from models import Listing, Verification
from providers.base import Provider
from transport import PageResult

AUTHORITATIVE = ("ats", "careers")


def _iso(ts: float | None) -> str | None:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds") if ts else None


def _shows_title(title: str, text: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9+#]+", title.lower()) if len(w) > 2 and w not in ("and", "the", "for", "with")]
    if not words:
        return True
    low = " ".join((text or "").lower().split())[:12000]
    return sum(w in low for w in words) / len(words) >= 0.6


def _age_days(listing: Listing, today: dt.date) -> int | None:
    if not listing.posted_at:
        return None
    try:
        return (today - dt.date.fromisoformat(listing.posted_at[:10])).days
    except ValueError:
        return None


def verify(listing: Listing, page: PageResult | None, provider: Provider | None, policy: Policy,
           today: dt.date) -> Verification:
    kind = listing.source_kind
    if listing.closed_elsewhere:
        return Verification(status="closed", checked_at=_iso(page.read_at) if page and page.read_at else None,
                            evidence=[listing.closed_elsewhere])
    if page is None or page.status == "skipped":
        why = "not read" if page is None else f"not read ({page.error or 'skipped'})"
        if provider is not None and not provider.fetchable:
            why = f"not read: {provider.label} pages refuse automated reads"
        return Verification(status="unverified", evidence=[why])
    checked = _iso(page.read_at)
    if page.status == "missing":
        if kind in AUTHORITATIVE:
            return Verification(status="closed", checked_at=checked,
                                evidence=["the employer's posting URL answered 'not found'"])
        return Verification(status="unverified", checked_at=checked, evidence=["the page answered 'not found'"])
    if page.status in ("failed", "empty"):
        return Verification(status="unverified", checked_at=checked,
                            evidence=[f"the page could not be read ({page.error or page.status})"])

    rec = page.record or {}
    text = str(rec.get("text") or "")
    closed = jd.closed_signal(text, provider.closed_words if provider else ())
    if closed:
        return Verification(status="closed", checked_at=checked, evidence=[f"the page says \"{closed}\""])
    deadline = jd.valid_through(text)
    if deadline and deadline < today:
        return Verification(status="closed", checked_at=checked, evidence=[f"its application deadline {deadline} has passed"])
    final = str(rec.get("url") or listing.source_url)
    if provider is not None and kind == "ats" and final and urlsplit(final).path != urlsplit(listing.source_url).path \
            and not provider.is_posting(final):
        return Verification(status="closed", checked_at=checked,
                            evidence=["the posting URL now redirects to the company's job board"])
    if not _shows_title(listing.title, f"{rec.get('title') or ''}\n{text}"):
        return Verification(status="unverified", checked_at=checked,
                            evidence=["the page that loaded does not show this job's title"])

    apply = jd.apply_signal(text)
    evidence = [f"page read {checked}"]
    age = _age_days(listing, today)
    if kind in AUTHORITATIVE:
        evidence.insert(0, "the employer's own posting page is live" if kind == "ats" else "the company's careers page shows it")
        if apply:
            evidence.append(f"it shows \"{apply}\"")
            return Verification(status="verified_active", checked_at=checked, evidence=evidence,
                                application_url_checked=listing.application_url == listing.canonical_url)
        if kind == "ats":
            # ATS boards take a closed posting off its URL (Greenhouse redirects to the board, Lever and Ashby answer
            # "not found"), so the live page at its own URL, showing this job, is the employer's own evidence
            evidence += ["the posting is live at its own URL with no closed notice",
                         "the apply button is not in the page text this reader returns"]
            return Verification(status="verified_active", checked_at=checked, evidence=evidence)
        evidence.append("no application button was visible in the page text")
        if age is not None and age > policy.careers_fresh_days:
            evidence.append(f"posted about {age} days ago, not confirmed open")
            return Verification(status="unverified", checked_at=checked, evidence=evidence)
        return Verification(status="likely_active", checked_at=checked, evidence=evidence)
    # a board's page: live and showing the job, but not the employer's own listing
    evidence.insert(0, f"the {listing.source} page is live")
    if apply:
        evidence.append(f"it shows \"{apply}\"")
    if kind == "aggregator":
        return Verification(status="unverified", checked_at=checked, evidence=evidence + ["an aggregator's copy"])
    fresh = provider.fresh_days if provider is not None and provider.fresh_days else policy.stale_after_days
    if age is not None and age <= fresh:
        evidence.append(f"posted {age} days ago")
        return Verification(status="likely_active", checked_at=checked, evidence=evidence,
                            application_url_checked=bool(apply) and listing.application_url == listing.canonical_url)
    evidence.append("no posting date to show it is recent" if age is None
                    else f"posted about {age} days ago (older than {fresh} days), not confirmed open")
    return Verification(status="unverified", checked_at=checked, evidence=evidence)
