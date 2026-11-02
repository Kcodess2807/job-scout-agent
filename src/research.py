"""Reading company pages and finding a person to write to (from signal-leads' research.py, cut to contract v2: pages
come through the agent's `page` source, and an address only from the company's own pages). Models only read text
into fields; a person is kept only when the page's own words name them."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from evidence import quote_on_page
from tooling import structured

MAX_URLS_PER_FETCH = 10


def domain(url_or_domain: str) -> str:
    s = (url_or_domain or "").strip().lower()
    host = urlparse(s if "://" in s else f"https://{s}").netloc or s
    return host.removeprefix("www.").split(":")[0]


async def read_pages(page, urls: list[str], chars: int = 6000) -> dict[str, str]:
    """{url: text} for the pages that could be read (`page(url) -> text`, the agent's v2 `page` source); a page that
    could not be read is simply absent."""
    texts = await asyncio.gather(*(page(u) for u in urls))
    # a JavaScript shell is not a page that was read
    return {u: text[:chars] for u, text in zip(urls, texts, strict=True) if len(text or "") >= 200}


# ── profile ─────────────────────────────────────────────────────────────────────────────────────────────


NOT_COMPANIES = ("linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com", "medium.com",
                 "substack.com", "crunchbase.com", "g2.com", "capterra.com", "wikipedia.org", "github.com", "reddit.com",
                 "producthunt.com", "glassdoor.com", "indeed.com", "angel.co", "wellfound.com", "notion.site")


def is_company_host(host: str) -> bool:
    """A company's own domain, never a social site, a directory or a review site that a search returns."""
    h = (host or "").lower().removeprefix("www.")
    return bool(h) and "." in h and not any(h == n or h.endswith("." + n) for n in NOT_COMPANIES)



class Person(BaseModel):
    name: str = Field(description="the person's full name, exactly as on the page")
    title: str = Field(description="their job title, as on the page")
    url: str = Field(description="the page it is on")
    quote: str = Field(description="the exact words on the page naming them and their role")


class People(BaseModel):
    people: list[Person] = []


PEOPLE_SYSTEM = ("You read a company's team or about page and list the people it names with their job titles. Copy "
                 "names and titles exactly. Leave out anyone the page does not name.")


async def contact(page, model, d: str, personas: list[str], company: str = "") -> dict:
    """The person matching a persona, named on a page the company controls, and their address only when one of
    those pages prints it (no address finder runs under contract v2)."""
    pages = await read_pages(page, [f"https://{d}{p}" for p in ("/team", "/about", "/about-us", "/company", "/people", "/leadership")], 12000)
    out = await structured(model, People, PEOPLE_SYSTEM, "\n\n".join(f"PAGE {u}\n{t}" for u, t in pages.items())) if pages else None
    people = [p for p in (out.people if out else []) if quote_on_page(p.quote, pages.get(p.url, ""))]
    if not people:
        return {"note": "nobody named on the company's own pages" if pages else "no team page could be read"}
    want = [w for p in personas for w in p.lower().split() if len(w) > 3]
    person = max(people, key=lambda p: sum(w in p.title.lower() for w in want))
    who = {"name": person.name, "title": person.title, "url": person.url}
    seen = re.findall(r"[\w.+-]+@" + re.escape(d), " ".join(pages.values()))
    first, *rest = person.name.split()
    mine = [e for e in seen if e.split("@")[0].lower() in (first.lower(), f"{first}.{rest[-1] if rest else ''}".lower())]
    if mine:
        return {**who, "email": mine[0].lower(), "email_source": "published", "tier": "published", "sendable": True}
    return {**who, "note": "no address for them on the company's pages"}


def now() -> datetime:
    return datetime.now(UTC)
