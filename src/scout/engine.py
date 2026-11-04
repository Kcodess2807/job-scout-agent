"""The job scout engine: a bounded, adaptive search controller. This is the integration point for other components:

    outcome = await scout(profile, prefs, read=gravity.read, resume_text=text, model=model, embed=embed)

It takes a CandidateProfile and SearchPrefs (models.py), reads only through `read` (the manifest's `jobs` and
`pages` sources), and returns a ScoutOutcome: the ranked qualifying jobs with their matches, the best near misses,
per-source status and a funnel summary. It never raises for a source failure; it raises only for programming errors.

Each round:
  1. take the next batch of plan items (highest priority first, adjusted by what each source has yielded);
  2. search them concurrently; normalise results into listings; drop non-postings; dedupe across sources;
     harvest a few ATS board index pages for more postings (incremental retrieval);
  3. triage new listings cheaply (title relevance, confirmed title-level conflicts) so no page is spent on noise;
  4. read the most promising postings (verification + full text);
  5. reject closed postings and hard-constraint violations; score the rest (stages B and C);
  6. send borderline matches to the model (stage D, bounded);
  7. count jobs that pass the threshold with counted verification.
It stops at the first of: enough qualified jobs (the target, or the larger re-ranking pool while budget allows),
the plan exhausted, or a budget (time, rounds, searches, pages, candidates), or cancellation."""
from __future__ import annotations

import asyncio
import datetime as dt
import math
import re
import time

import dedupe as dd
import eligibility
import normalize
import ranking
from config import DEFAULT, ScoutConfig
import jd
from evaluator import LLMBudget, audit, evaluate
from models import CandidateProfile, Listing, Match, ScoutOutcome, SearchPrefs, SearchSummary, SourceReport, Verification
from dedupe import norm_company
from planner import Plan, build_plan, next_batch, places as places_of, yield_factor
from providers.catalog import build as build_providers, classify
from scoring import prefilter, rescore, score
from security import safe_url
from semantic import Embed, Semantic
from transport import Read, Transport
from verify import verify


class CancelToken:
    """Cooperative cancellation: set it from anywhere; the engine stops at the next step and returns what it has."""

    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


_MD_LINK = re.compile(r"\[([^\]]{3,160})\]\((https?://[^)\s]+)\)")
TITLE_LEVEL_REJECTIONS = ("employment_type", "seniority", "location", "work_mode", "experience")


def _counts(listing: Listing, prefs: SearchPrefs) -> bool:
    status = listing.verification.status
    return status == "verified_active" or (status == "likely_active" and prefs.include_likely_active)


def _unknowns(listing: Listing) -> bool:
    """Whether a hard requirement is still unknown on a read posting: years (with no seniority stated either), or
    the hiring country of a remote job."""
    req = listing.requirements
    unknown_years = req.min_years is None and req.stated_level is None
    unknown_remote = listing.work_mode == "remote" and not listing.remote_region and not listing.location_country
    return unknown_years or unknown_remote


def prefs_fingerprint(prefs: SearchPrefs, profile: CandidateProfile, policy) -> str:
    """What an eligibility verdict depends on: the same posting is re-judged when any of these change."""
    return "|".join(str(x) for x in (profile.years(), prefs.country_code, prefs.work_mode, prefs.internships,
                                     prefs.employment_type, policy.experience_at_most, profile.highest_degree))


def _bump(d: dict[str, int], reason: str):
    key = reason.split(":")[0]
    d[key] = d.get(key, 0) + 1


async def scout(profile: CandidateProfile, prefs: SearchPrefs, *, read: Read, resume_text: str = "", model=None,
                embed: Embed | None = None, config: ScoutConfig = DEFAULT, cancel: CancelToken | None = None,
                llm: LLMBudget | None = None, pages_available: bool = True, today: dt.date | None = None,
                clock=time.monotonic, sleep=asyncio.sleep, rng=None, known: dict | None = None) -> ScoutOutcome:
    cfg = config.with_threshold(prefs.min_score).counting_likely_active(prefs.include_likely_active)
    budget, policy = cfg.budget, cfg.policy
    today = today or dt.date.today()
    start = clock()
    providers = build_providers()
    pmap = {p.name: p for p in providers}
    plan: Plan = build_plan(prefs, profile, providers)
    transport = Transport(read, budget, sleep=sleep, clock=clock, rng=rng, pages_available=pages_available)
    sem = Semantic(profile.evidence_lines, embed, budget.embed_batch)
    await sem.warm()
    llm = llm or LLMBudget(budget.max_llm_calls)
    dedup = dd.Deduper()
    s = SearchSummary()
    triaged: set[str] = set()
    read_keys: set[str] = set()
    queue: dict[str, float] = {}             # key -> read priority
    matches: dict[str, Match] = {}
    qualified: set[str] = set()
    harvest: list[tuple[str, str]] = []      # (board index url, provider)
    harvested: set[str] = set()
    audited: set[str] = set()
    queries: list[str] = []
    remember: dict[str, dict] = {}
    known = known or {}
    fingerprint = prefs_fingerprint(prefs, profile, policy)
    stop = ""
    levels = plan.levels()
    level_i = 0
    target = prefs.count
    pool = max(target, math.ceil(target * policy.pool_factor))

    def elapsed() -> float:
        return clock() - start

    def add_listing(listing: Listing):
        if len(dedup.items) >= budget.max_candidates:
            return
        seen_before = known.get(listing.key)
        if seen_before and seen_before.get("why") == "listed":
            s.skipped_known += 1   # already on the person's list from an earlier run: never counted twice
            return
        if seen_before and (seen_before.get("why") == "closed" or seen_before.get("fp") == fingerprint):
            s.skipped_known += 1   # closed, or not a fit for these same preferences, in a run of the last few days
            return
        key, _ = dedup.add(listing)
        pmap[listing.source].stats["postings"] = pmap[listing.source].stats.get("postings", 0) + 1
        kept = dedup.items.get(key)
        if kept is not None and kept.closed_elsewhere and kept.verification.status != "closed":
            close(key, kept)
        elif kept is not None and key in qualified and not kept.copy_checked and copy_to_check(kept):
            qualified.discard(key)          # a new LinkedIn/board copy of a counted job: look at it first
            copy_pending[key] = None

    def close(key: str, listing: Listing):
        """Another copy says the job no longer takes applications: it is closed, and never counted or shown."""
        listing = listing.model_copy(update={"verification": Verification(
            status="closed", checked_at=listing.verification.checked_at, evidence=[listing.closed_elsewhere or "closed"])})
        dedup.replace(listing)
        if key in qualified:
            qualified.discard(key)
            st = pmap[listing.source].stats
            st["qualified"] = max(0, st.get("qualified", 0) - 1)
        if key in matches or key in read_keys:
            s.inactive_jobs_rejected += 1
            _bump(s.rejection_reasons, "closed")
        matches.pop(key, None)
        queue.pop(key, None)
        copy_pending.pop(key, None)
        remember[key] = {"why": "closed", "fp": ""}

    def copy_to_check(listing: Listing) -> str | None:
        """One LinkedIn or job-board copy of this job that can be opened (LinkedIn first: it shows
        "No longer accepting applications" when the hiring is over)."""
        if listing.copy_checked:
            return None
        urls = [listing.source_url] if listing.source_kind == "board" else []
        if listing.source_kind == "board" and listing.verification.checked_at:
            urls = []                        # the board copy itself was just read
        urls += list(listing.also_seen_at)
        best = None
        for u in urls:
            prov = classify(u, providers)
            if prov is None or prov.kind != "board" or not prov.fetchable:
                continue
            if prov.name == "linkedin":
                return u
            best = best or u
        return best

    async def read_and_score(keys: list[str]):
        pages = await asyncio.gather(*[transport.page(dedup.items[k].source_url) for k in keys])
        for k, page in zip(keys, pages):
            listing = dedup.items.get(k)
            if listing is None:
                continue
            read_keys.add(k)
            provider = pmap.get(listing.source)
            if page.status == "ok" and page.record:
                listing = normalize.with_page(listing, page.record, provider, today)
            listing = listing.model_copy(update={"verification": verify(listing, page, provider, policy, today)})
            dedup.replace(listing)
            await _score(listing)

    async def _score(listing: Listing):
        if listing.verification.status == "closed":
            s.inactive_jobs_rejected += 1
            _bump(s.rejection_reasons, "closed")
            return
        rej, con = eligibility.check(listing, profile, prefs, policy)
        if rej:
            s.eligibility_rejections += 1
            for r in rej:
                _bump(s.rejection_reasons, r)
            matches[listing.key] = Match(eligible=False, rejections=rej, concerns=con)
            return
        req = listing.requirements
        sem_val, _ = await sem.align(req.required_lines + req.responsibility_lines + req.preferred_lines
                                     or [listing.snippet])
        matches[listing.key] = score(listing, profile, prefs, cfg, related=plan.related, semantic=sem_val,
                                     rejections=[], concerns=con)

    # ---------------------------------------------------------------- the work pool
    # One pool of `concurrency` calls, refilled the moment a call finishes: page reads (they are what verifies a job)
    # take most slots, discovery (plan searches, other channels, board indexes) always keeps a few, and the model's
    # checks run as one task in the pool. Results are processed as they arrive, so no slot waits on a slow call.
    conc = max(2, budget.concurrency)
    running: dict[asyncio.Task, tuple[str, object]] = {}
    disc_q: list[tuple[float, str, object]] = []          # (priority, kind, payload): non-plan discovery
    border_pending: dict[str, None] = {}
    audit_pending: dict[str, None] = {}
    resolved: set[str] = set()
    rescued: set[str] = set()   # strong board finds whose employer page was moved to the front of the queue
    similar_asked: set[str] = set()
    unread: set[str] = set()                               # leads not worth a page read (board copies)
    copy_pending: dict[str, None] = {}                     # jobs about to count whose LinkedIn/board copy is opened first
    plan_turn = 0

    def core_done() -> bool:
        return not any(not i.done for i in plan.items if i.level <= 1)

    def stop_now() -> str:
        if cancel is not None and cancel.cancelled:
            return "cancelled"
        if elapsed() >= budget.max_seconds:
            return "time_budget"
        if len(qualified) >= pool:
            return "target_met"
        if len(qualified) >= target and (core_done() or elapsed() >= 0.6 * budget.max_seconds):
            return "target_met"   # quality vs latency: the target is met and the core searches are done
        if len(dedup.items) >= budget.max_candidates:
            return "candidate_budget"
        return ""

    def next_plan_item():
        nonlocal level_i
        while level_i < len(levels):
            batch = next_batch(plan, levels[level_i], pmap, size=1)
            if batch:
                return batch[0]
            level_i += 1
        return None

    def best_read() -> str | None:
        cands = [k for k in queue if k in dedup.items and k not in read_keys]
        return max(cands, key=lambda k: (queue[k], k)) if cands else None

    # ---- discovery channels beyond the plan
    years = profile.years()
    exp_phrase = ("for freshers (0-1 years of experience)" if years is not None and years < 1 else
                  f"for candidates with up to {years:g} years of experience" if years is not None else "")
    places = [w for w, _ in places_of(prefs)][:2]
    for role in (prefs.target_roles[:1] + plan.related[:1]):
        for where in places:
            kind = "internship" if prefs.internships else "job"
            disc_q.append((0.62, "answer", f"Currently open {role} {kind} postings {exp_phrase} in {where}. List each "
                                           f"with a direct link to the posting on the company's careers page, Greenhouse, "
                                           f"Lever, Ashby, Workable, LinkedIn, Naukri, Wellfound or Cutshort."))

    async def run_search(item):
        """The core searches (levels 0-1) go to both search apps when the setup has two, and the results are joined:
        each engine indexes different postings. Later levels alternate between them to spread the budget."""
        both = transport.available("jobs_more") and item.level <= 1
        if both:
            first, second = await asyncio.gather(transport.search(item.query, "jobs"),
                                                 transport.search(item.query, "jobs_more"))
            if not transport.available("jobs_more") and not second:
                return item, first           # the second app turned out not to be set up
            return item, (first or []) + (second or []) if (first is not None or second is not None) else None
        source = "jobs_more" if transport.available("jobs_more") and plan_turn % 2 == 1 else "jobs"
        records = await transport.search(item.query, source)
        if source == "jobs_more" and not records and not transport.available("jobs_more"):
            records = await transport.search(item.query, "jobs")   # the second app is not set up: no item is lost
        return item, records

    async def run_llm(border: list[str], aud: list[str]):
        evals = {}
        if model is not None and resume_text and border and llm.calls < llm.max_calls - 2:
            items = [(dedup.items[k], matches[k]) for k in border if k in dedup.items and k in matches]
            evals = await evaluate(model, items[:8], profile, resume_text, llm, policy.llm_adjust_max)
        found = {}
        if model is not None and aud and llm.calls < llm.max_calls - 1:
            found = await audit(model, [dedup.items[k] for k in aud if k in dedup.items][:12], llm)
        return border, aud, evals, found

    def launch(kind: str, payload, coro):
        running[asyncio.ensure_future(coro)] = (kind, payload)

    def schedule():
        nonlocal plan_turn
        while len(running) < conc and not stop_now():
            n_reads = sum(1 for k, _ in running.values() if k in ("page", "copy"))
            n_disc = sum(1 for k, _ in running.values() if k in ("search", "answer", "similar", "resolve"))
            llm_busy = any(k == "llm" for k, _ in running.values())
            # the model's checks: one task, when enough is waiting or the pool is quiet
            if not llm_busy and model is not None and (len(border_pending) >= 4 or len(audit_pending) >= 3 or
                                                        ((border_pending or audit_pending) and len(running) <= 2)):
                b, a = list(border_pending), list(audit_pending)
                border_pending.clear(), audit_pending.clear()
                launch("llm", None, run_llm(b, a))
                continue
            key = best_read()
            plan_left = level_i < len(levels)
            can_disc = transport.search_calls < budget.max_search_calls and (disc_q or plan_left) and \
                (transport.available("jobs") or transport.available("answers") or transport.available("similar"))
            if copy_pending and transport.can_page() and n_reads < conc - 1:
                ck = next(iter(copy_pending))
                copy_pending.pop(ck)
                lst = dedup.items.get(ck)
                url = copy_to_check(lst) if lst is not None else None
                if lst is None or url is None:
                    continue
                dedup.replace(lst.model_copy(update={"copy_checked": True}))
                launch("copy", (ck, url), transport.page(url))
            elif harvest and transport.can_page() and n_reads < conc - 1:
                url, pname = harvest.pop(0)
                launch("harvest", (url, pname), transport.page(url))
            elif key and transport.can_page() and (n_reads < conc - 2 or not can_disc or n_disc >= 2):
                read_keys.add(key)
                queue.pop(key, None)
                launch("page", key, transport.page(dedup.items[key].source_url))
            elif can_disc:
                item = None
                top = max(disc_q, key=lambda x: x[0]) if disc_q else None
                peek = next_plan_item() if plan_left else None
                plan_pri = (peek.weight * yield_factor(pmap[peek.provider])) if peek else -1
                if top and (top[0] >= plan_pri or peek is None):
                    disc_q.remove(top)
                    _, kind, payload = top
                    if kind == "answer":
                        launch("answer", payload, transport.answer(payload))
                    elif kind == "similar":
                        launch("similar", payload, transport.similar(payload))
                    else:
                        launch("resolve", payload, transport.search(payload, "jobs"))
                    continue
                if peek is None:
                    break
                item = peek
                item.done = True
                plan_turn += 1
                s.rounds += 1
                launch("search", item, run_search(item))
            elif key and transport.can_page():
                read_keys.add(key)
                queue.pop(key, None)
                launch("page", key, transport.page(dedup.items[key].source_url))
            else:
                break

    def ingest(records, label: str, provider_name: str | None):
        if provider_name:
            p = pmap.get(provider_name)
            if p is not None:
                p.stats["queries"] = p.stats.get("queries", 0) + 1
                p.stats["results"] = p.stats.get("results", 0) + len(records or [])
        if records is None:
            return
        queries.append(label)
        for rec in records:
            s.total_candidates_discovered += 1
            url = safe_url(rec.get("url"))
            owner = classify(url, providers) if url else None
            if owner is not None and owner.kind == "ats" and owner.is_listing(url) and url not in harvested \
                    and len(harvested) < budget.max_board_pages:
                harvested.add(url)
                harvest.append((url, owner.name))
            listing, why = normalize.from_search(rec, providers, label, today)
            if listing is None:
                s.not_job_postings += 1
                continue
            add_listing(listing)
        triage()

    def triage():
        for key, listing in list(dedup.items.items()):
            if key in triaged:
                continue
            triaged.add(key)
            rel = prefilter(listing, prefs, plan.related)
            if listing.found_by.startswith("answer:") and rel < policy.prefilter_min_title:
                # an AI answer chose this page for the role, but cites only its URL, so the title (taken from the
                # URL) may say nothing: read it, and judge it on the posting's own title and text
                rel = 0.5
            if rel < policy.prefilter_min_title:
                s.prefilter_rejections += 1
                _bump(s.rejection_reasons, "title not relevant")
                continue
            # confirmed conflicts visible before any page is spent: title level, type, place, years in the snippet
            rej, _ = eligibility.check(listing, profile, prefs, policy)
            rej = [r for r in rej if r.startswith(TITLE_LEVEL_REJECTIONS)]
            if rej:
                s.eligibility_rejections += 1
                s.rejected_before_reading += 1
                for r in rej:
                    _bump(s.rejection_reasons, r)
                matches[key] = Match(eligible=False, rejections=rej)
                remember[key] = {"why": "ineligible", "fp": fingerprint}
                continue
            provider = pmap[listing.source]
            can_verify = listing.source_kind in ("ats", "careers")
            if listing.source_kind == "board" and listing.company and len(resolved) < budget.max_resolve \
                    and norm_company(listing.company) not in resolved:
                # a board copy can never verify a job: look for the same opening on the employer's own site
                resolved.add(norm_company(listing.company))
                disc_q.append((0.8 * rel, "resolve", f"{listing.company} {listing.title} job careers apply"))
            if can_verify or prefs.include_likely_active:
                weight = 1.0 if can_verify else 0.8
                queue[key] = rel * weight * (0.6 + 0.4 * listing.authority) * (1.0 if provider.fetchable else 0.1)
            else:
                unread.add(key)   # a lead only: it cannot count, so no page read is spent on it

    def rescue(listing: Listing, m: Match):
        """A strong match whose board copy (LinkedIn, Naukri) was read but couldn't be confirmed open (too old, no
        date): the same opening on the employer's own site is what can confirm it, so that search goes first."""
        name = norm_company(listing.company) if listing.company else ""
        if not name or name in rescued or len(rescued) >= budget.max_rescue \
                or m.score < policy.min_score - policy.borderline_below:
            return
        rescued.add(name)
        q = f"{listing.company} {listing.title} job careers apply"
        for i, (_, kind, payload) in enumerate(disc_q):
            if kind == "resolve" and payload == q:
                disc_q[i] = (2.0, kind, payload)
                return
        resolved.add(name)
        disc_q.append((2.0, "resolve", q))

    def after_read(key: str):
        m = matches.get(key)
        listing = dedup.items.get(key)
        if m is None or listing is None:
            return
        if not m.eligible:
            remember[key] = {"why": "ineligible", "fp": fingerprint}
            return
        if listing.source_kind == "board" and listing.verification.status == "unverified":
            rescue(listing, m)
        lo, hi = policy.min_score - policy.borderline_below, policy.min_score + policy.borderline_above
        if listing.full_text and m.cap is None and not m.llm_evidence and lo <= m.score <= hi:
            border_pending[key] = None
        if m.score >= policy.min_score and _counts(listing, prefs) and listing.full_text and _unknowns(listing) \
                and key not in audited:
            audit_pending[key] = None
        count(key)

    def count(key: str):
        listing, m = dedup.items.get(key), matches.get(key)
        if not listing or not m or key in qualified:
            return
        if m.eligible and m.score >= policy.min_score and _counts(listing, prefs) and \
                (key in audited or not _unknowns(listing) or model is None or llm.calls >= llm.max_calls):
            if copy_to_check(listing) and transport.can_page():
                copy_pending[key] = None     # opened next; it counts once the copy shows no closed notice
                return
            qualified.add(key)
            pmap[listing.source].stats["qualified"] = pmap[listing.source].stats.get("qualified", 0) + 1
            if len(similar_asked) < budget.max_similar and listing.source_url not in similar_asked:
                similar_asked.add(listing.source_url)   # more like a good match: same company, similar roles
                disc_q.append((0.7, "similar", listing.source_url))

    def apply_llm(border, aud, evals, found):
        for k, ev in evals.items():
            if k in matches:
                m = rescore(matches[k], ev["adjustment"], cfg)
                matches[k] = m.model_copy(update={"llm_evidence": ev["checks"], "reason": ev["explanation"] or None,
                                                  "concerns": list(dict.fromkeys(m.concerns + ev["concerns"]))})
        for k in aud:
            audited.add(k)
            lst, hit = dedup.items.get(k), found.get(k)
            if lst is not None and hit:
                req, notes = jd.apply_audit(lst.requirements, hit)
                desc = lst.description
                if hit.get("location"):   # put the quoted hiring location where the location rule reads it
                    desc = f"Location: {hit['location']}\n{desc}"
                lst = lst.model_copy(update={"requirements": req, "description": desc})
                if hit.get("location"):
                    loc, cities, country = normalize._location_from(f"Location: {hit['location']}")
                    if country:
                        lst = lst.model_copy(update={"location_country": country, "location_cities": cities or lst.location_cities})
                dedup.replace(lst)
                rej, con = eligibility.check(lst, profile, prefs, policy)
                if rej:
                    s.eligibility_rejections += 1
                    for r in rej:
                        _bump(s.rejection_reasons, r)
                    matches[k] = Match(eligible=False, rejections=[f"{r} (found on re-check)" for r in rej], concerns=con)
                    remember[k] = {"why": "ineligible", "fp": fingerprint}
                elif notes and k in matches:
                    matches[k] = matches[k].model_copy(update={"concerns": matches[k].concerns + notes})
        for k in set(border) | set(aud):
            count(k)

    async def process(kind: str, payload, result):
        if kind == "search":
            item, records = result
            ingest(records, item.query, item.provider)
        elif kind in ("answer", "similar", "resolve"):
            ingest(result, f"{kind}: {str(payload)[:120]}", None)
            st = pmap["careers"].stats   # report the extra channels in the source table
            st.setdefault("channels", {}).setdefault(kind, 0)
            st["channels"][kind] += 1
            s.channel_calls[kind] = s.channel_calls.get(kind, 0) + 1
        elif kind == "harvest":
            url, pname = payload
            page = result
            if page.status == "ok" and page.record:
                text = str(page.record.get("text") or "")
                links = [(a, u) for a, u in _MD_LINK.findall(text)]
                links += [("", u) for u in (page.record.get("links") or []) if isinstance(u, str)]
                for anchor, link in links[:300]:
                    link = safe_url(link)
                    prov = classify(link, providers) if link else None
                    if not prov or prov.name != pname or not prov.is_posting(link) or not anchor:
                        continue
                    cand, _ = normalize.from_search({"title": anchor, "url": link, "snippet": ""}, providers,
                                                    f"board index {url}", today)
                    if cand and prefilter(cand, prefs, plan.related) >= policy.prefilter_min_title:
                        s.total_candidates_discovered += 1
                        add_listing(cand)
                triage()
        elif kind == "page":
            key = payload
            listing = dedup.items.get(key)
            if listing is None:
                return
            page = result
            provider = pmap.get(listing.source)
            if page.status == "ok" and page.record:
                listing = normalize.with_page(listing, page.record, provider, today)
            listing = listing.model_copy(update={"verification": verify(listing, page, provider, policy, today)})
            dedup.replace(listing)
            await _score(listing)
            if listing.verification.status == "closed":
                remember[key] = {"why": "closed", "fp": ""}
            after_read(key)
        elif kind == "copy":
            key, url = payload
            listing = dedup.items.get(key)
            if listing is None:
                return
            page = result
            prov = classify(url, providers)
            text = str((page.record or {}).get("text") or "") if page.status == "ok" else ""
            shut = jd.closed_signal(text, prov.closed_words if prov else ()) if text else None
            if shut:
                listing = listing.model_copy(update={
                    "closed_elsewhere": f"the {prov.label if prov else 'other'} copy says \"{shut}\" ({url})"})
                close(key, listing)
            else:
                count(key)
        elif kind == "llm":
            apply_llm(*result)

    schedule()
    while running:
        done, _ = await asyncio.wait(list(running), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            kind, payload = running.pop(task)
            try:
                result = task.result()
            except asyncio.CancelledError:
                continue
            except Exception as exc:  # noqa: BLE001 - one failed call never stops the session
                s.task_errors += 1
                if kind == "page" and payload in dedup.items:
                    unread.add(payload)
                _ = exc
                continue
            await process(kind, payload, result)
        stop = stop_now()
        if stop:
            break
        schedule()
    if not stop:
        stop = ("search_source_unavailable" if not transport.available("jobs") and not qualified else
                "search_budget" if transport.search_calls >= budget.max_search_calls else "plan_exhausted")
    for task in running:   # time, target or cancel: what is still in flight is abandoned
        task.cancel()
    if running:
        await asyncio.gather(*running, return_exceptions=True)
    if border_pending or audit_pending:   # checks still owed on jobs that would otherwise count unchecked
        if stop != "cancelled" and model is not None and llm.calls < llm.max_calls:
            apply_llm(*(await run_llm(list(border_pending), list(audit_pending))))

    # leads that were never read (budget, sites that refuse reads, no page source): scored on the snippet, capped
    if stop != "cancelled":
        for k in list(queue) + sorted(unread):
            listing = dedup.items.get(k)
            if listing is None or k in matches:
                continue
            listing = listing.model_copy(update={"verification": verify(listing, None, pmap.get(listing.source), policy, today)})
            dedup.replace(listing)
            await _score(listing)

    # copies still owed a look (the search stopped first): open them now, so no closed job is shown
    if stop != "cancelled":
        owed = [k for k, m in matches.items() if k in dedup.items and m.eligible and m.score >= policy.min_score
                and _counts(dedup.items[k], prefs) and copy_to_check(dedup.items[k])]
        owed = sorted(owed, key=lambda k: -matches[k].score)[:max(16, 2 * target)]
        pairs = [(k, copy_to_check(dedup.items[k])) for k in owed if transport.can_page()]
        for k, _ in pairs:
            dedup.replace(dedup.items[k].model_copy(update={"copy_checked": True}))
        pages = await asyncio.gather(*[transport.page(u) for _, u in pairs], return_exceptions=True)
        for (k, u), page in zip(pairs, pages):
            if not isinstance(page, BaseException):
                await process("copy", (k, u), page)

    # final: dedupe again (page reads can reveal that two copies are one job), rank, diversify
    live = {k: m for k, m in matches.items() if k in dedup.items and not dedup.items[k].closed_elsewhere}
    finalists, extra_dups = dd.dedupe_all([dedup.items[k] for k in live if live[k].eligible])
    shut = [x for x in finalists if x.closed_elsewhere]   # two copies found to be one job only now
    for x in shut:
        s.inactive_jobs_rejected += 1
        _bump(s.rejection_reasons, "closed")
        remember[x.key] = {"why": "closed", "fp": ""}
    finalists = [x for x in finalists if not x.closed_elsewhere]
    fin_map = {x.key: x for x in finalists}
    s.duplicates_removed = dedup.duplicates + extra_dups
    eligible = [(x, live[x.key]) for x in finalists if x.key in live]
    ranked = ranking.rank(eligible, today)
    passing = [(lst, m) for lst, m in ranked if m.score >= policy.min_score and _counts(lst, prefs)]
    final = ranking.diversify(passing, target, policy.max_per_company)
    for lst, m in final:   # say plainly what neither the parser nor the re-check could confirm
        note = None
        if lst.requirements.min_years is None and lst.requirements.stated_level is None:
            note = ("No experience requirement found in the posting (also re-checked)" if lst.key in audited
                    else "No experience requirement found in the posting; confirm it before applying")
        if note and note not in m.concerns:
            m.concerns.append(note)
    final_keys = {lst.key for lst, _ in final}
    near = [lst.key for lst, m in ranked if lst.key not in final_keys and m.score >= 55][:5]

    s.below_threshold_rejections = sum(1 for lst, m in eligible if m.score < policy.min_score)
    s.unverified_not_counted = sum(1 for lst, m in eligible if m.score >= policy.min_score and not _counts(lst, prefs))
    s.qualified = len(passing)
    s.search_calls, s.page_calls, s.llm_calls = transport.search_calls, transport.page_calls, llm.calls
    s.elapsed_seconds = round(elapsed(), 2)
    s.stop_reason = stop

    warnings: list[str] = []
    health = transport.report()
    if health["jobs"]["open"]:
        warnings.append(f"The job search source stopped answering: {health['jobs']['reason']}")
    if not pages_available or health["pages"]["open"]:
        warnings.append("Postings could not be opened"
                        + (f" ({health['pages']['reason']})" if health["pages"]["reason"] else "")
                        + ", so none could be verified as open; only verified jobs are counted.")
    if sem.failed:
        warnings.append("Semantic matching fell back to word overlap (embeddings were unavailable).")
    if len(final) < target and stop != "cancelled":
        short = target - len(final)
        why = {"time_budget": "the time budget ran out", "search_budget": "the search budget was used up",
               "round_budget": "the search rounds were used up", "plan_exhausted": "every planned search was tried",
               "candidate_budget": "the candidate limit was reached",
               "search_source_unavailable": "the search source stopped answering"}.get(stop, stop)
        warnings.append(f"Found {len(final)} of {target} at a score of {policy.min_score}+ with "
                        f"{'verified' if not prefs.include_likely_active else 'verified or likely'} active status; "
                        f"{why}, so the other {short} could not be found at that standard. Nothing was lowered to fill the list.")
    if s.unverified_not_counted:
        warnings.append(f"{s.unverified_not_counted} more jobs scored {policy.min_score}+ but could not be verified as open, "
                        "so they are not counted (they are listed as leads).")
    for name in ("jobs", "pages"):
        for w in health[name]["warnings"][:2]:
            warnings.append(f"{name}: {w}")

    reports = []
    for p in providers:
        st = p.stats
        if not st:
            continue
        status = "ok" if st.get("postings") else ("degraded" if st.get("queries") else "not_used")
        reports.append(SourceReport(source=p.name, queries=st.get("queries", 0), results=st.get("results", 0),
                                    postings=st.get("postings", 0), qualified=st.get("qualified", 0), status=status))
    for name, label in (("jobs_more", "second web search"), ("answers", "AI web search (cited pages)"),
                        ("similar", "similar-postings search")):
        if name in health:
            h = health[name]
            # optional channels: one that is not set up is skipped quietly, never reported as a failure
            reports.append(SourceReport(source=label, queries=h["calls"], failures=h["failures"],
                                        status="not_used" if h["open"] else "ok",
                                        reason=("not set up: " + (h["reason"] or "")[:120]) if h["open"] else None))
    if health["jobs"]["open"]:
        reports.append(SourceReport(source="jobs (web search)", status="unavailable", reason=health["jobs"]["reason"],
                                    failures=health["jobs"]["failures"]))
    if health["pages"]["open"]:
        reports.append(SourceReport(source="pages (posting reader)", status="unavailable", reason=health["pages"]["reason"],
                                    failures=health["pages"]["failures"]))

    if stop == "cancelled":
        status = "cancelled"
    elif not final and health["jobs"]["open"] and not dedup.items:
        status = "failed"
    elif not final:
        status = "no_results"
    else:
        status = "complete" if len(final) >= target else "partial"
    jobs = [lst for lst, _ in final] + [fin_map[k] for k in near]
    return ScoutOutcome(
        requested_count=target, minimum_match_score=policy.min_score, search_status=status, jobs=jobs,
        matches={lst.key: live[lst.key] for lst in jobs}, near_misses=near,
        searched_sources=sorted({p.name for p in providers if p.stats.get("queries")}), sources=reports, summary=s,
        warnings=warnings, queries=queries, remember=remember)
