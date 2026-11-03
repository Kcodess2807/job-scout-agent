"""The provider interface. A provider is a *discovery target* (Greenhouse, LinkedIn, a company careers page), not an
API client: in contract v2 every read goes through the manifest's sources, so a provider only knows

- how to aim a web search at its postings (query text; `site:` works on search apps that honour it, and results
  are classified by URL either way),
- which URLs are single postings (not search or listing pages), their stable posting id and canonical URL,
- the company and application URL it can read from the URL or page,
- how authoritative it is (an employer's own ATS beats a re-post on a board),
- extra closed-posting wording its pages use.

Adding a source is a new subclass registered in providers/catalog.py; nothing in matching or ranking changes."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from security import strip_tracking


@dataclass(frozen=True)
class QueryContext:
    role: str
    where: str                  # "Pune, India", "India", "remote"
    internships: bool = False
    years: float | None = None
    remote_only: bool = False
    country_code: str | None = None


@dataclass
class Provider:
    name: str
    label: str
    kind: str = "board"                         # ats | board | careers | aggregator
    authority: float = 0.5                      # 0..1, how much a live page here proves the job is open
    domains: tuple[str, ...] = ()               # host suffixes
    countries: frozenset[str] | None = None     # None = everywhere
    posting: re.Pattern | None = None           # path (+query) of a single posting; group "id" if it has one
    listing: re.Pattern | None = None           # a company's board index page worth harvesting
    fetchable: bool = True                      # worth a page read (some sites refuse every datacenter address)
    priority: float = 1.0                       # initial search priority
    site: str | None = None                     # the site: operator target
    closed_words: tuple[str, ...] = ()
    internships_only: bool = False
    stats: dict = field(default_factory=dict)

    # ---- classification
    def matches(self, host: str) -> bool:
        host = host.lower()
        return any(host == d or host.endswith("." + d) for d in self.domains)

    def serves(self, country_code: str | None, internships: bool) -> bool:
        if self.internships_only and not internships:
            return False
        return self.countries is None or (country_code in self.countries)

    def _target(self, url: str) -> str:
        p = urlsplit(url)
        return p.path + (("?" + p.query) if p.query else "")

    def is_posting(self, url: str) -> bool:
        return bool(self.posting and self.posting.search(self._target(url)))

    def is_listing(self, url: str) -> bool:
        return bool(self.listing and self.listing.search(urlsplit(url).path) and not self.is_posting(url))

    def posting_id(self, url: str) -> str | None:
        m = self.posting.search(self._target(url)) if self.posting else None
        found = m and (m.groupdict().get("id") or m.groupdict().get("pid"))
        if found:
            return found.lower()
        return None

    def canonical(self, url: str) -> str:
        clean = strip_tracking(url)
        p = urlsplit(clean)
        host = p.netloc.lower().removeprefix("www.")
        return f"https://{host}{p.path.rstrip('/') or '/'}" + (f"?{p.query}" if p.query else "")

    def key(self, url: str) -> str | None:
        pid = self.posting_id(url)
        return f"{self.name}:{pid}" if pid else None

    def company(self, url: str, title: str) -> str | None:
        return None

    def application_url(self, url: str) -> str:
        return self.canonical(url)

    # ---- query text
    def query(self, ctx: QueryContext) -> str:
        kind = "internship" if ctx.internships else "job"
        site = f"site:{self.site} " if self.site else ""
        return f"{site}{ctx.role} {kind} {ctx.where}".strip()   # no quotes: they make search apps return nothing

    def capabilities(self) -> dict:
        return {"name": self.name, "label": self.label, "kind": self.kind, "authority": self.authority,
                "domains": list(self.domains), "countries": sorted(self.countries) if self.countries else "all",
                "page_reads": self.fetchable, "pagination": "query diversification" + (
                    "; board index pages are harvested for more postings" if self.listing else ""),
                "transport": "web_result.search + web_page.get (contract v2 sources)"}


def query_param(url: str, name: str) -> str | None:
    vals = parse_qs(urlsplit(url).query).get(name)
    return vals[0] if vals else None


def slug_company(slug: str | None) -> str | None:
    if not slug:
        return None
    words = re.split(r"[-_]+", slug.strip("/"))
    words = [w for w in words if w and not w.isdigit()]
    return " ".join(w.capitalize() if not w.isupper() else w for w in words) or None
