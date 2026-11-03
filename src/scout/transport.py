"""The only I/O of the search engine: two manifest sources, `jobs` (web_result.search) and `pages` (web_page.get),
read through gravity.read (passed in as `read`, so tests fake it). Around each call:

- a budget (calls per session), checked before the call;
- bounded concurrency and a minimum interval per source (rate limit);
- retries with exponential backoff and jitter, on transient failures only;
- a circuit breaker per source: a permanent refusal (NEEDS_RECONNECT, NOT_GRANTED, a missing pick, the run budget)
  opens it at once, three failures in a row open it too; an open source is skipped and named in the result;
- a per-session cache (the same query or URL is read once). Nothing is cached across sessions, so a page read is
  never presented as fresher than it is.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from config import Budget

Read = Callable[..., Awaitable[dict]]

PERMANENT = ("NEEDS_RECONNECT", "NOT_GRANTED", "MISSING_PICK", "INVALID_PICK", "UNKNOWN_CAPABILITY", "REFUSED",
             "this run has no", "BUDGET_EXCEEDED", "CAPABILITY_BLOCKED", "INSTANCE_MISMATCH", "NOT_ALLOWED",
             "401", "403 Forbidden", "invalid api key", "unauthorized", "No connected account",
             "isn't connected", "not connected")
TRANSIENT = ("timeout", "timed out", "429", "rate limit", "too many requests", "temporar", "502", "503", "504", "500",
             "connection", "reset by peer", "unavailable", "overloaded", "try again", "session terminated")
MISSING = ("404", "410", "not found", "no such", "does not exist")
NO_RESULTS = ("hasn't returned any results", "has not returned any results", "no results", "returned no results")


def classify_error(message: str) -> str:
    low = message.lower()
    if any(p in low for p in NO_RESULTS):
        return "empty"   # the search ran and found nothing: an answer, not a failure
    if any(p.lower() in low for p in PERMANENT):
        return "permanent"
    if any(p in low for p in MISSING):
        return "missing"
    if any(p in low for p in TRANSIENT):
        return "transient"
    return "failed"


@dataclass
class Health:
    calls: int = 0
    failures: int = 0
    consecutive: int = 0
    open: bool = False
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    last_call: float = 0.0


@dataclass
class PageResult:
    status: str                    # ok | empty | missing | failed | skipped
    record: dict | None = None
    error: str | None = None
    read_at: float | None = None   # wall-clock seconds (time.time) of the read


class Transport:
    def __init__(self, read: Read, budget: Budget, *, sleep=asyncio.sleep, clock=time.monotonic, wall=time.time,
                 rng: random.Random | None = None, pages_available: bool = True):
        self.read, self.budget, self.sleep, self.clock, self.wall = read, budget, sleep, clock, wall
        self.rng = rng or random.Random()
        self.health: dict[str, Health] = {"jobs": Health(), "pages": Health(
            open=not pages_available, reason=None if pages_available else "not set up for this run")}
        self.search_calls = 0
        self.page_calls = 0
        self._search_cache: dict[str, list[dict]] = {}
        self._page_cache: dict[str, PageResult] = {}
        self._sem = asyncio.Semaphore(max(1, budget.concurrency))
        self._locks: dict[str, asyncio.Lock] = {}

    # ---- budget
    def _h(self, source: str) -> Health:
        return self.health.setdefault(source, Health())

    def available(self, source: str) -> bool:
        return not self._h(source).open

    def can_search(self, source: str = "jobs") -> bool:
        return self.search_calls < self.budget.max_search_calls and self.available(source)

    def can_page(self) -> bool:
        return self.page_calls < self.budget.max_page_calls and not self.health["pages"].open

    async def _paced(self, source: str):
        h = self._h(source)
        async with self._locks.setdefault(source, asyncio.Lock()):
            wait = self.budget.min_interval - (self.clock() - h.last_call)
            if wait > 0:
                await self.sleep(wait)
            h.last_call = self.clock()

    async def _call(self, source: str, **args) -> dict:
        """One read with retries. Raises RuntimeError(kind: message) when it finally fails."""
        h = self._h(source)
        attempt = 0
        while True:
            await self._paced(source)
            h.calls += 1
            try:
                async with self._sem:
                    answer = await self.read(source, **args)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - gravity.CallFailed and transport errors, classified below
                msg = str(exc)[:300]
                kind = classify_error(msg)
                if kind == "transient" and attempt < self.budget.retries:
                    attempt += 1
                    await self.sleep(self.budget.backoff_base * (2 ** (attempt - 1)) * (1 + self.rng.random()))
                    continue
                if kind == "empty":
                    h.consecutive = 0
                    return {"records": [], "complete": True, "warnings": []}
                if kind != "missing":
                    h.failures += 1
                    h.consecutive += 1
                    if kind == "permanent" or h.consecutive >= self.budget.breaker_threshold:
                        h.open, h.reason = True, msg[:200]
                raise RuntimeError(f"{kind}: {msg}") from exc
            h.consecutive = 0
            if not isinstance(answer, dict):
                h.failures += 1
                raise RuntimeError("failed: the source answered something that is not a record list")
            for w in answer.get("warnings") or []:
                w = str(w)[:200]
                if w not in h.warnings and len(h.warnings) < 10:
                    h.warnings.append(w)
            return answer

    # ---- the two reads
    async def search(self, text: str, source: str = "jobs") -> list[dict] | None:
        """Web results for one query on a web_result.search source, or None when that source is unavailable or the
        discovery budget is used up."""
        return await self._discover(source, text, dict(text=text, limit=10))

    async def similar(self, url: str, source: str = "similar") -> list[dict] | None:
        """Pages like a good posting (web_result.find_similar): other openings at the company and similar roles."""
        return await self._discover(source, f"similar:{url}", dict(args={"url": url}, limit=10))

    async def answer(self, question: str, source: str = "answers") -> list[dict] | None:
        """An AI web search (web_answer.get): the pages its answer cites, as web results (title from the URL)."""
        got = await self._discover(source, f"answer:{question}", dict(id=question, text=question))
        if got is None:
            return None
        out = []
        for rec in got:
            for url in rec.get("citations") or []:
                if isinstance(url, str):
                    out.append({"url": url, "title": "", "snippet": "", "cited": True})
        return out

    async def _discover(self, source: str, key: str, args: dict) -> list[dict] | None:
        key = f"{source}|{' '.join(key.lower().split())}"
        if key in self._search_cache:
            return self._search_cache[key]
        if not self.can_search(source):
            return None
        self.search_calls += 1
        try:
            answer = await self._call(source, **args)
        except RuntimeError:
            self._search_cache[key] = []
            return []
        records = [r for r in (answer.get("records") or []) if isinstance(r, dict)]
        self._search_cache[key] = records
        return records

    async def page(self, url: str) -> PageResult:
        """The page's text (web_page.get). `skipped` when the source is unavailable or out of budget."""
        if url in self._page_cache:
            return self._page_cache[url]
        if not self.can_page():
            return PageResult("skipped", error=self.health["pages"].reason or "page-read budget used up")
        self.page_calls += 1
        try:
            answer = await self._call("pages", id=url, args={"url": url})
        except RuntimeError as exc:
            kind, _, msg = str(exc).partition(": ")
            result = PageResult("missing" if kind == "missing" else "failed", error=msg[:200], read_at=self.wall())
        else:
            recs = [r for r in (answer.get("records") or []) if isinstance(r, dict)]
            rec = recs[0] if recs else None
            text = (rec or {}).get("text") or ""
            result = PageResult("ok" if len(text.strip()) >= 40 else "empty", record=rec, read_at=self.wall(),
                                error=None if text.strip() else "the page came back without text")
        self._page_cache[url] = result
        return result

    def report(self) -> dict:
        return {name: {"calls": h.calls, "failures": h.failures, "open": h.open, "reason": h.reason,
                       "warnings": h.warnings} for name, h in self.health.items()}
