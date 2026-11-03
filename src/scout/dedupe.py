"""Cross-source duplicates. The same opening on LinkedIn, Indeed and the employer's Greenhouse board is one job:
the most authoritative copy is kept and the others are listed under `also_seen_at`.

Matched on, in order: the provider's posting id (greenhouse:123 also matches a careers page with ?gh_jid=123),
the canonical URL, then company + normalised title + place. Two postings with different ids on the same ATS are
different openings (a company hiring the same title in two teams), and two copies whose full texts differ clearly
are kept apart too."""
from __future__ import annotations

import re

from models import Listing

_LEVEL_WORDS = re.compile(r"\b(sr|senior|jr|junior|lead|principal|staff|i{1,3}|iv|1|2|3)\b")


def norm_company(c: str | None) -> str:
    c = re.sub(r"\b(inc|llc|ltd|limited|pvt|private|corp|corporation|co|gmbh|technologies|technology|labs|"
               r"software|solutions|india|global)\b\.?", " ", (c or "").lower())
    return re.sub(r"[^a-z0-9]+", "", c)


def norm_title(t: str | None) -> str:
    t = (t or "").lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    t = re.sub(r"\b(remote|hybrid|on-?site|full[- ]time|part[- ]time|contract|urgent|hiring|immediate joiners?)\b", " ", t)
    t = t.replace("sr.", "senior").replace("jr.", "junior")
    return " ".join(re.findall(r"[a-z0-9+#]+", t))


def norm_place(listing: Listing) -> str:
    if listing.work_mode == "remote":
        return "remote"
    if listing.location_cities:
        return listing.location_cities[0].lower()
    return (listing.location_country or "").lower()


def fuzzy_key(listing: Listing) -> tuple[str, str, str] | None:
    company = norm_company(listing.company)
    title = norm_title(listing.title)
    if not company or not title:
        return None
    return company, title, norm_place(listing)


def _shingles(text: str, n: int = 5) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())[:600]
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def text_similarity(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _posting_id(listing: Listing) -> tuple[str, str] | None:
    if listing.key.startswith("url:"):
        return None
    provider, _, pid = listing.key.partition(":")
    return provider, pid


def same_opening(a: Listing, b: Listing) -> bool:
    if a.key == b.key or a.canonical_url == b.canonical_url:
        return True
    ia, ib = _posting_id(a), _posting_id(b)
    if ia and ib and ia[0] == ib[0] and ia[1] != ib[1]:
        return False  # two ids on one ATS or board: two openings
    fa, fb = fuzzy_key(a), fuzzy_key(b)
    if not fa or fa != fb:
        # a place may be unknown on one copy: company + title equal and one place empty still matches
        if not (fa and fb and fa[:2] == fb[:2] and (not fa[2] or not fb[2])):
            return False
    if a.full_text and b.full_text and len(a.description) > 600 and len(b.description) > 600:
        return text_similarity(a.description, b.description) >= 0.35
    return True


def _better(a: Listing, b: Listing) -> tuple[Listing, Listing]:
    rank = lambda x: (x.authority, x.full_text, x.verification.status == "verified_active", -len(x.key))  # noqa: E731
    return (a, b) if rank(a) >= rank(b) else (b, a)


def merge(a: Listing, b: Listing) -> Listing:
    """The better copy, with the other's links and any field it lacks."""
    keep, other = _better(a, b)
    upd: dict = {}
    seen = [u for u in (*keep.also_seen_at, other.source_url, *other.also_seen_at) if u != keep.source_url]
    upd["also_seen_at"] = list(dict.fromkeys(seen))[:8]
    for f in ("company", "location", "salary", "posted_at", "posted_text", "company_domain"):
        if getattr(keep, f) in (None, "") and getattr(other, f) not in (None, ""):
            upd[f] = getattr(other, f)
    if keep.work_mode == "unknown" and other.work_mode != "unknown":
        upd["work_mode"] = other.work_mode
    if keep.employment_type == "unknown" and other.employment_type != "unknown":
        upd["employment_type"] = other.employment_type
    if not keep.location_cities and other.location_cities:
        upd["location_cities"] = other.location_cities
    # a closed notice on any copy closes the opening (LinkedIn "No longer accepting applications" while the
    # employer's page is still up means the hiring is over, or soon will be)
    shut = keep.closed_elsewhere or other.closed_elsewhere or (
        f"the {other.source} copy says it is closed ({'; '.join(other.verification.evidence[:1])})"
        if other.verification.status == "closed" else None)
    if shut:
        upd["closed_elsewhere"] = shut
    upd["copy_checked"] = keep.copy_checked or other.copy_checked
    return keep.model_copy(update=upd)


class Deduper:
    """Incremental: each new listing is checked against what is already kept."""

    def __init__(self):
        self.items: dict[str, Listing] = {}
        self._by_url: dict[str, str] = {}
        self._by_fuzzy: dict[tuple[str, str], list[str]] = {}
        self.duplicates = 0

    def _candidates(self, listing: Listing) -> list[str]:
        out = []
        if listing.key in self.items:
            out.append(listing.key)
        if listing.canonical_url in self._by_url:
            out.append(self._by_url[listing.canonical_url])
        fk = fuzzy_key(listing)
        if fk:
            out.extend(self._by_fuzzy.get(fk[:2], []))
        return list(dict.fromkeys(out))

    def _index(self, listing: Listing):
        self._by_url[listing.canonical_url] = listing.key
        for u in listing.also_seen_at:
            self._by_url.setdefault(u, listing.key)
        fk = fuzzy_key(listing)
        if fk and listing.key not in self._by_fuzzy.setdefault(fk[:2], []):
            self._by_fuzzy[fk[:2]].append(listing.key)

    def add(self, listing: Listing) -> tuple[str, bool]:
        """(the key it is kept under, whether it is a new opening)."""
        for key in self._candidates(listing):
            existing = self.items.get(key)
            if existing and same_opening(existing, listing):
                merged = merge(existing, listing)
                self.duplicates += 1
                if merged.key != key:  # the new copy is the better one: re-key
                    del self.items[key]
                    for k, v in list(self._by_url.items()):
                        if v == key:
                            self._by_url[k] = merged.key
                    for lst in self._by_fuzzy.values():
                        if key in lst:
                            lst[lst.index(key)] = merged.key
                self.items[merged.key] = merged
                self._index(merged)
                return merged.key, False
        self.items[listing.key] = listing
        self._index(listing)
        return listing.key, True

    def replace(self, listing: Listing):
        """After a page read changed a listing's fields."""
        self.items[listing.key] = listing
        self._index(listing)


def dedupe_all(listings: list[Listing]) -> tuple[list[Listing], int]:
    d = Deduper()
    for item in listings:
        d.add(item)
    return list(d.items.values()), d.duplicates
