"""Search results and page reads -> Listing, one schema whichever provider found it. Company, title and location
come from the provider's URL shape first (the most reliable), then the result title, then the page text."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from urllib.parse import urlsplit

import geo
import jd
from models import Listing
from providers.base import Provider
from providers.catalog import classify
from security import safe_url

SITE_NAMES = re.compile(r"\s*[|\-–—·]\s*(linkedin|naukri(?:\.com)?|indeed(?:\.com)?|glassdoor|wellfound|angellist|"
                        r"greenhouse|lever|ashby|workable|smartrecruiters|workday|foundit|instahyre|internshala|"
                        r"cutshort|hirist|careers?(?: page)?|jobs?(?: page)?|job board|apply now)\s*$", re.I)
_SPLIT = re.compile(r"\s+(?:[|–—]|-|@|at)\s+")
_LINKEDIN = re.compile(r"^(?P<company>.+?) hiring (?P<title>.+?)(?: in (?P<loc>.+?))?\s*(?:\|\s*LinkedIn)?$", re.I)
_GH = re.compile(r"^job application for (?P<title>.+?) at (?P<company>.+)$", re.I)
_TITLE_WORDS = re.compile(
    r"\b(engineer|developer|programmer|analyst|scientist|architect|consultant|designer|manager|lead|intern|"
    r"internship|administrator|specialist|associate|executive|officer|researcher|technician|tester|devops|sre|"
    r"head|director|trainee|strategist|coordinator|accountant|writer|marketer|representative|recruiter)\b", re.I)


_QUALIFIER = re.compile(r"(?:backend|back[- ]end|frontend|front[- ]end|full[- ]?stack|remote|hybrid|on-?site|contract|"
                        r"full[- ]time|part[- ]time|permanent|temporary|urgent|immediate joiner|wfh|india|usa|apac|emea|"
                        r"platform|infrastructure|data|ml|ai|genai|mobile|web|cloud|security|i{1,3}|iv|[a-z]{1,2})", re.I)


# job-copy services that post other companies' jobs from their own ATS account (jobs.lever.co/jobgether/...)
COPY_SITE_ACCOUNTS = {"jobgether", "jobgether-com", "remoterocketship", "hiringcafe", "crossover", "turing", "mercor"}
_COPY_SITE = re.compile(r"\b(?:is[- ]hiring|hiring[- ]for|jobs?[- ]in[- ][a-z]|job[- ]20\d\d|jobs[- ]20\d\d|vacanc(?:y|ies)|"
                        r"walk[- ]?in|off[- ]?campus|recruitment[- ]20\d\d|freshers?[- ]jobs|apply[- ]online[- ]now)\b", re.I)


_POINTS_ELSEWHERE = re.compile(
    r"continue to the official|official (?:employer|company)(?:'s)? (?:application|careers? page|site|website|posting)|"
    r"apply on (?:the )?(?:company|employer)(?:'s)? (?:site|website|careers page)|employers'? own sites?|"
    r"original (?:job )?(?:posting|listing)|view (?:the )?original|redirect(?:s|ed)? (?:you )?to the (?:employer|company)|"
    r"external application|this job (?:is|was) (?:sourced|aggregated|posted) from|via (?:linkedin|indeed|naukri)", re.I)
_HIRING_CO = re.compile(r"\b(?:at|in|@|for|by)\s+([A-Z][\w&.'-]+(?:\s+[A-Z][\w&.'-]+){0,3})|"
                        r"([A-Z][\w&.'-]+(?:\s+[A-Z][\w&.'-]+){0,3})\s+is\s+hiring|company\s*[:\-]\s*([A-Z][\w&.' -]{1,40})")
_NOT_COMPANIES = {"india", "remote", "the", "our", "a", "an", "location", "job", "jobs", "careers", "home", "office",
                  "bengaluru", "bangalore", "pune", "mumbai", "hyderabad", "chennai", "delhi", "noida", "gurugram"}


def copy_of_another_employer(domain_company: str | None, title: str, text: str = "") -> bool:
    """A site that lists another company's job, not the employer's own careers page: it names a different hiring
    company in its title ("AI Engineer at Capco | AIMates Jobs") or points to the employer's own page."""
    head = " ".join((text or "")[:1500].split())
    if _POINTS_ELSEWHERE.search(head):
        return True
    dom = _norm(domain_company)
    if not dom:
        return False
    for m in _HIRING_CO.finditer(title or ""):
        name = next(g for g in m.groups() if g).strip(" .-|")
        first = name.split()[0].lower().strip(",")
        if first in _NOT_COMPANIES or geo.find_cities(name) or geo.find_country(name):
            continue
        cand = _norm(name)
        if len(cand) >= 3 and cand not in dom and dom not in cand:
            return True
    return False


def _as_copy(listing: Listing, providers: list[Provider]) -> Listing:
    agg = next((p for p in providers if p.kind == "aggregator"), None)
    return listing.model_copy(update={"source": agg.name if agg else listing.source, "source_kind": "aggregator",
                                      "authority": agg.authority if agg else 0.25})


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def clean_title(raw: str) -> str:
    t = " ".join((raw or "").split())
    t = re.split(r"\s*(?:\.{3}|…)", t, maxsplit=1)[0]
    for _ in range(3):
        t2 = SITE_NAMES.sub("", t)
        if t2 == t:
            break
        t = t2
    return t.strip(" -|–:,")


def title_company(raw_title: str, url_company: str | None) -> tuple[str, str | None]:
    t = clean_title(raw_title)
    m = _LINKEDIN.match(t) or _GH.match(t)
    if m:
        return m.group("title").strip(), m.group("company").strip()
    parts = [p.strip() for p in _SPLIT.split(t) if p.strip()]
    if len(parts) == 1:
        return parts[0], url_company
    if url_company:
        target = _norm(url_company)
        for i, p in enumerate(parts):
            if _norm(p) and (_norm(p) == target or _norm(p).startswith(target) or target.startswith(_norm(p))):
                rest = [q for j, q in enumerate(parts) if j != i]
                title = next((q for q in rest if _TITLE_WORDS.search(q)), rest[0])
                return title, re.sub(r"\s+(?:careers?|jobs?|hiring|recruitment)$", "", p, flags=re.I)
    title = next((p for p in parts if _TITLE_WORDS.search(p)), parts[0])
    others = [p for p in parts if p != title and not geo.find_cities(p) and not re.search(r"\d", p) and len(p) <= 50
              and not _QUALIFIER.fullmatch(p.strip())]
    return title, (url_company or (others[0] if others else None))


def _location_from(text: str) -> tuple[str | None, list[str], str | None]:
    m = re.search(r"(?:^|\n)\s*\**\s*(?:location|locations|job location|based in|office)\s*\**\s*[:\-]\s*\**\s*([^\n]{2,80})",
                  text or "", re.I)
    scope = m.group(1) if m else (text or "")[:600]
    cities = geo.find_cities(scope)
    country = geo.find_country(scope)
    if m:
        loc = " ".join(m.group(1).split()).strip(" *|")
    elif cities:
        loc = ", ".join(c for c, _ in cities[:3])
    else:
        loc = geo.country_name(country) or None
    return loc, [c for c, _ in cities], country


def title_from_url(url: str) -> str:
    """'/job/AI-Engineer-Intern-Bengaluru-Beyond-Technologies-g1iLkH1J' -> 'AI Engineer Intern Bengaluru Beyond
    Technologies'. Only used until the page is read; the page's own title replaces it."""
    segs = [s for s in urlsplit(url).path.split("/") if s]
    slug = max(segs, key=lambda s: s.count("-") + s.count("_"), default="")
    words = [w for w in re.split(r"[-_]+", slug) if w and not re.fullmatch(r"[0-9a-f]{6,}|\d+|(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z0-9]{6,}", w)]
    return " ".join(w if w.isupper() else w.capitalize() for w in words)[:120]


def listing_key(provider: Provider, canonical: str, url: str) -> str:
    return provider.key(url) or "url:" + hashlib.sha1(canonical.encode()).hexdigest()[:16]


def from_search(record: dict, providers: list[Provider], query: str, today: dt.date) -> tuple[Listing | None, str]:
    """(listing, why-not). A result that is not a single job posting returns (None, reason)."""
    url = safe_url(record.get("url"))
    if url and not record.get("title") and record.get("cited"):
        record = {**record, "title": title_from_url(url)}   # an AI answer cites URLs only: the slug is the title
    if not url or not record.get("title"):
        return None, "no usable link"
    provider = classify(url, providers)
    if provider is None:
        return None, "not a job site"
    if not provider.is_posting(url):
        return None, "listing page" if provider.is_listing(url) else "not a single posting"
    first_seg = next((seg.lower() for seg in urlsplit(url).path.split("/") if seg), "")
    if (provider.kind == "careers" and _COPY_SITE.search(f"{record.get('title') or ''} {urlsplit(url).path}")) or \
            (provider.kind == "ats" and first_seg in COPY_SITE_ACCOUNTS):
        # "Zensar Technologies is hiring...", "AI Engineer Jobs in Bengaluru": a site listing other companies' jobs,
        # not the employer's own careers page, so it can never verify a job
        provider = next((p for p in providers if p.kind == "aggregator"), provider)
    canonical = provider.canonical(url)
    url_company = provider.company(url, record.get("title") or "")
    title, company = title_company(str(record["title"]), url_company)
    snippet = " ".join(str(record.get("snippet") or "").split())[:600]
    shut = jd.closed_signal(f"{record.get('title') or ''} {snippet}", provider.closed_words)
    loc, cities, country = _location_from(f"{record.get('title')}\n{snippet}")
    m = _LINKEDIN.match(clean_title(str(record["title"])))
    if m and m.group("loc"):
        loc = m.group("loc").strip()
        cities = [c for c, _ in geo.find_cities(loc)] or cities
        country = geo.find_country(loc) or country
    published = record.get("published")
    posted_at = None
    if isinstance(published, str) and re.match(r"20\d\d-\d\d-\d\d", published):
        d = dt.date.fromisoformat(published[:10])
        posted_at = d.isoformat() if d <= today else None
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    listing = Listing(
        key=listing_key(provider, canonical, url), title=title[:160] or "Untitled", company=company,
        company_domain=host if provider.kind == "careers" else None, location=loc, location_cities=cities,
        location_country=country, work_mode=jd.work_mode(snippet, title, loc or ""),
        employment_type=jd.employment_type(snippet, title), source=provider.name, source_kind=provider.kind,
        authority=provider.authority, source_url=url, canonical_url=canonical,
        application_url=provider.application_url(url), snippet=snippet, posted_at=posted_at,
        closed_elsewhere=(f"the {provider.label} search result says \"{shut}\"" if shut else None),
        posted_text="search result date" if posted_at else None, found_by=query)
    listing.requirements = jd.parse(snippet, title)
    if listing.source_kind == "careers" and copy_of_another_employer(url_company, str(record["title"]), snippet):
        listing = _as_copy(listing, providers)
    return listing, ""


def with_page(listing: Listing, page: dict, provider: Provider | None, today: dt.date) -> Listing:
    """Fill a listing from its page text. Facts already read from the URL are kept; the page fills the gaps."""
    text = str(page.get("text") or "")
    page_title = str(page.get("title") or "")
    upd: dict = {"description": text[:30000], "full_text": len(text) >= 400}
    if page_title:
        t, c = title_company(page_title, listing.company)
        if t and _TITLE_WORDS.search(t) and len(t) <= 120:
            upd["title"] = t
        if not listing.company and c:
            upd["company"] = c
    loc, cities, country = _location_from(text)
    if loc and (not listing.location or len(cities) > len(listing.location_cities)):
        upd.update(location=loc[:120], location_cities=cities or listing.location_cities,
                   location_country=country or listing.location_country)
    title = upd.get("title", listing.title)
    mode = jd.work_mode(text, title, upd.get("location", listing.location) or "")
    if mode != "unknown":
        upd["work_mode"] = mode
    if mode == "remote":
        upd["remote_region"] = jd.remote_region(text)
    etype = jd.employment_type(text, title)
    if etype != "unknown":
        upd["employment_type"] = etype
    upd["salary"] = jd.salary(text, listing.location_country) or listing.salary
    posted_at, posted_text = jd.posted(text, today)
    if posted_at:
        upd.update(posted_at=posted_at, posted_text=posted_text)
    upd["requirements"] = jd.parse(text, title)
    if listing.source_kind == "careers" and (provider is None or provider.kind == "careers") and \
            copy_of_another_employer(provider.company(listing.source_url, "") if provider else listing.company,
                                     page_title, text):
        upd.update(source="aggregator", source_kind="aggregator", authority=0.25)   # a copy: never verifies
    return listing.model_copy(update=upd)
