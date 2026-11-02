"""The seam between finding jobs and applying to them: one row shape, one collection, one function. Today the rows
come from the `jobs` input; the discovery code, when it lands in this graph, calls `add(rows, "discovery")` and
routes to prepare. Nothing else in the agent knows where a row came from."""

from __future__ import annotations

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


def job_key(row: dict) -> str:
    """The posting's host and path, lowercased, without the query; else company:title."""
    u = urlparse(row.get("url") or "")
    if u.netloc:
        return (_host(row["url"]) + u.path.lower().rstrip("/"))[:500]
    return f"{_slug(row.get('company') or '')}:{_slug(row.get('title') or '')}"


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


async def add(rows: list[dict], source: str) -> list[dict]:
    """Write new rows to `jobs` under their key (a row already there is left as it is) and return the new ones."""
    coll, now = store.collection("jobs"), datetime.now(UTC).isoformat()
    fresh: dict[str, dict] = {}
    for i, r in enumerate(rows):
        r = {**dict.fromkeys(ROW_KEYS), **r}
        key = job_key(r)
        if key not in fresh:  # order: when it arrived, then its line, so the table keeps the candidate's order
            fresh[key] = {**r, "key": key, "domain": r.get("domain") or company_domain(r), "source": source,
                          "found_at": now, "order": f"{now}#{i:05d}"}
    known = set(coll.has_many(list(fresh)))
    for key, r in fresh.items():
        if key not in known:
            store.put_safely(coll, key, r)
    return [r for k, r in fresh.items() if k not in known]
