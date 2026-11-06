"""Is a found job worth applying to? The search already drops what a page says is closed; this looks at what a live
page cannot show: how long the role has really been listed, whether the company's own list of open roles carries it,
and layoffs or a hiring freeze in the news. Code reads the records; no model. A doubt is shown, never acted on: the job
stays on the board and the person decides."""

from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta

STALE_DAYS = 45      # posted this long ago: a doubt
DEAD_DAYS = 90       # a strong one
REPOST_DAYS = 60     # first seen this long before the date the board shows: re-posted
NEWS_DAYS = 180      # how far back a layoff counts
FULL_LIST = 5        # the company's list has at least this many roles: missing from it means more
PER_RUN = 8          # jobs checked a run: two built-in calls each, charged to the run's budget
LAYOFF = re.compile(r"\blay(?:s|ing|ed)?[- ]?offs?\b|\bhiring freeze\b|\bjob cuts\b|\bretrench|\bdownsiz", re.I)
STOP = {"and", "the", "for", "with", "senior", "junior", "lead", "sr", "jr", "ii", "iii"}


def first(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, "", [], {}):
            return d[k]
    return None


def records(got: dict) -> list[dict]:
    """A PredictLeads answer as flat records."""
    items = got.get("data") if isinstance(got.get("data"), list) else got.get("records") or got.get("items") or []
    return [it.get("attributes") if isinstance(it.get("attributes"), dict) else it for it in items if isinstance(it, dict)]


def day(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#]+", (title or "").lower()) if len(w) > 1 and w not in STOP}


def same_role(title: str, other: str, share: float = 0.6) -> bool:
    """`other` carries most of the words of `title`: "Backend Engineer" is "Senior Backend Engineer, Payments"."""
    want = words(title)
    return bool(want) and len(want & words(other)) / len(want) >= share


def reading(job: dict, openings: list[dict], news: list[dict], today: date) -> dict:
    """{level: solid | doubtful | unknown, why: [plain lines]}. Two soft doubts or one hard one make it doubtful; solid
    takes a positive sign and no doubt at all."""
    hard, soft, good = [], [], []
    verified, posted = job.get("verified"), day(job.get("posted"))
    age = (today - posted).days if posted else None
    if verified == "verified_active":
        good.append("open on the employer's own page, read this run")
    elif verified == "unverified":
        soft.append("nobody could confirm it is open")
    if age is not None and age >= DEAD_DAYS:
        hard.append(f"posted {age} days ago")
    elif age is not None and age >= STALE_DAYS:
        soft.append(f"posted {age} days ago")
    mine = [o for o in openings if same_role(job.get("title") or "", str(first(o, "title", "name") or ""))]
    if mine:
        good.append(f"on the company's own list of {len(openings)} open roles")
        seen = min((d for o in mine if (d := day(first(o, "first_seen_at", "found_at", "date")))), default=None)
        if seen and posted and (posted - seen).days >= REPOST_DAYS:
            hard.append(f"re-posted: first seen {seen.isoformat()}, the board says {posted.isoformat()}")
        elif seen and not posted and (today - seen).days >= DEAD_DAYS:
            soft.append(f"listed since {seen.isoformat()}")
    elif openings:
        (hard if len(openings) >= FULL_LIST else soft).append(f"not among the {len(openings)} open roles the company lists")
    for n in news:
        line = str(first(n, "summary", "title", "headline") or "")
        when = day(first(n, "found_at", "published_at", "date"))
        if LAYOFF.search(line) and (when is None or (today - when) <= timedelta(days=NEWS_DAYS)):
            hard.append(f"in the news{f' on {when.isoformat()}' if when else ''}: {line[:140]}")
            break
    doubts = hard + soft
    level = "doubtful" if hard or len(soft) >= 2 else "solid" if good and not doubts else "unknown"
    return {"level": level, "why": doubts + good}


def line(t: dict | None) -> str:
    """The reading as one line, for the row."""
    if not t:
        return ""
    head = {"doubtful": "Doubtful", "solid": "Looks real"}.get(t.get("level"), "Not sure")
    return f"{head}: {'; '.join(t.get('why') or []) or 'nothing to go on'}"


async def check_all(io, jobs: list[dict], today: date) -> dict[str, dict]:
    """{job key: reading} for the given jobs; a company's records are read once a run."""
    domains = {j["domain"] for j in jobs if j.get("domain")}

    async def company(d: str) -> tuple[str, list[dict], list[dict]]:
        roles, news = await asyncio.gather(
            io.built_in("predictleads.company_data", company_id_or_domain=d, dataset="job_openings", active_only=True, limit=100),
            io.built_in("predictleads.company_data", company_id_or_domain=d, dataset="news_events", limit=30))
        return d, records(roles), records(news)
    known = {d: (o, n) for d, o, n in await asyncio.gather(*(company(d) for d in domains))}
    return {j["key"]: reading(j, *known.get(j.get("domain"), ([], [])), today) for j in jobs}
