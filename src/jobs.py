"""The seam between finding jobs and applying to them: one row shape, one collection, one function. Today the rows
come from the `jobs` input; the discovery code, when it lands in this graph, calls `add(rows, "discovery")` and
routes to prepare. Nothing else in the agent knows where a row came from."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from urllib.parse import urlparse

import store
from research import NOT_COMPANIES

ROW_KEYS = ("company", "title", "url", "domain", "location", "description", "hr_email", "source", "found_at")
# job boards and applicant tracking systems: a posting there says nothing about the company's own domain
BOARD_HOSTS = NOT_COMPANIES + ("greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkday.com",
                               "smartrecruiters.com", "icims.com", "workable.com", "naukri.com", "instahyre.com",
                               "internshala.com", "foundit.in", "monster.com", "ziprecruiter.com", "recruitee.com",
                               "bamboohr.com", "jobvite.com", "breezy.hr", "keka.com", "darwinbox.in", "zohorecruit.com")
EMAIL = re.compile(r"^[\w.+'-]+@[\w-]+(\.[\w-]+)+$")
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}


def _host(url: str | None) -> str:
    return (urlparse(url or "").netloc or "").lower().split(":")[0].removeprefix("www.")


def registrable(host: str) -> str:
    """careers.acme.com -> acme.com; jobs.acme.co.in -> acme.co.in."""
    labels = host.split(".")
    n = 3 if len(labels) >= 3 and labels[-2] in SECOND_LEVEL and len(labels[-1]) == 2 else 2
    return ".".join(labels[-n:])


def company_domain(row: dict) -> str | None:
    host = _host(row.get("url"))
    if not host or "." not in host or any(host == b or host.endswith("." + b) for b in BOARD_HOSTS):
        return None
    return registrable(host)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


LINKEDIN_JOB = re.compile(r"/jobs/view/(?:[\w%-]*?-)?(\d{8,})/?$")


def _legacy_key(row: dict) -> str:
    u = urlparse(row.get("url") or "")
    if u.netloc:
        return (_host(row["url"]) + u.path.lower().rstrip("/"))[:500]
    return f"{_slug(row.get('company') or '')}:{_slug(row.get('title') or '')}"


def job_key(row: dict) -> str:
    """A LinkedIn posting by its id, whichever regional host or slug the link has (in.linkedin.com/jobs/view/
    software-engineer-at-acme-4454193413 and linkedin.com/jobs/view/4454193413 are one job); else the posting's host
    and path, lowercased, without the query; else company:title."""
    host, path = _host(row.get("url")), urlparse(row.get("url") or "").path
    m = LINKEDIN_JOB.search(path) if host == "linkedin.com" or host.endswith(".linkedin.com") else None
    if m:
        return f"linkedin.com/jobs/view/{m.group(1)}"
    return _legacy_key(row)


ITEM_BYTES = 30_000  # the jobs collection's max_item_kb is 32: a record over it is refused whole (MemoryQuotaError)


def _size(row: dict) -> int:
    # escaped JSON is the largest a record can serialise to: "•" is 6 bytes as \u2022, 3 as UTF-8
    return len(json.dumps(row, ensure_ascii=True).encode("utf-8"))


def fit_item(row: dict, limit: int = ITEM_BYTES) -> dict:
    """The row cut to fit one memory item: the description shortens (the other fields are small), never dropped
    while there is room; a row that still does not fit is returned as it is and its write reports the failure."""
    if _size(row) <= limit or not row.get("description"):
        return row
    text = row["description"]
    lo, hi = 0, len(text)
    while lo < hi:  # the longest prefix that fits
        mid = (lo + hi + 1) // 2
        if _size({**row, "description": text[:mid]}) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return {**row, "description": text[:lo]}


def parse_lines(text: str) -> list[dict]:
    """One row per line: a bare link, or `Company | Role | link | hr@company.com` with any part left out."""
    rows = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if not any(parts):
            continue
        row = dict.fromkeys(ROW_KEYS)
        if len(parts) == 1:
            row["url"] = parts[0] if parts[0].lower().startswith("http") else None
            row["company"] = None if row["url"] else parts[0]
        else:
            for p in parts:
                if p.lower().startswith("http"):
                    row["url"] = p
                elif EMAIL.match(p):
                    row["hr_email"] = p.lower()
                elif p and row["company"] is None:
                    row["company"] = p
                elif p and row["title"] is None:
                    row["title"] = p
        rows.append(row)
    return rows


async def add(rows: list[dict], source: str) -> dict:
    """Write new rows to `jobs` under their key (a row already there is left as it is). What happened to each row:
    {"new": rows written, "known": rows already on the list (under this key or the key an older version gave it),
    "declined": rows the person said no to, "failed": rows the memory refused, "merged": rows that were another row's
    duplicate}. Every row given lands in exactly one of them, so a count shown is a count of rows stored."""
    coll, now = store.collection("jobs"), datetime.now(UTC).isoformat()
    fresh: dict[str, dict] = {}
    merged = 0
    for i, r in enumerate(rows):
        r = {**dict.fromkeys(ROW_KEYS), **r}
        key = job_key(r)
        if key in fresh:
            merged += 1
            continue
        # order: when it arrived, then its line, so the table keeps the candidate's order
        fresh[key] = fit_item({**r, "key": key, "domain": r.get("domain") or company_domain(r), "source": source,
                               "found_at": now, "order": f"{now}#{i:05d}",
                               "picked": r.get("picked", source != "discovery")})  # a found job waits for the person's pick
    legacy = {key: _legacy_key(r) for key, r in fresh.items() if _legacy_key(r) != key}
    if any(k.startswith("linkedin.com/jobs/view/") for k in fresh):
        # before 0.7.3 a LinkedIn job was keyed by its host and slug: find it under that key by its id
        by_id = {m.group(1): k for k, _ in coll.find(limit=store.FIND_CAP)
                 if "linkedin.com/jobs/view/" in k and not k.startswith("linkedin.com/") and (m := LINKEDIN_JOB.search("/" + k.split("/", 1)[1]))}
        for k in fresh:
            if k.startswith("linkedin.com/jobs/view/") and (old := by_id.get(k.rsplit("/", 1)[1])):
                legacy[k] = old
    known_any = set(coll.has_many([*fresh, *legacy.values()]))
    declined_any = set(store.collection("declined").has_many([*fresh, *legacy.values()]))  # "not applying" holds, from any source
    out: dict = {"new": [], "known": [], "declined": [], "failed": [], "merged": merged}
    for key, r in fresh.items():
        old = legacy.get(key)
        if key in declined_any or old in declined_any:
            out["declined"].append(r)
        elif key in known_any or old in known_any:
            out["known"].append({**r, "key": key if key in known_any else old})
        elif store.put_safely(coll, key, r):
            out["new"].append(r)
        else:
            out["failed"].append(r)
    return out
