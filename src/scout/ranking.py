"""Final ranking, deterministic. Order of precedence:

1. hard eligibility (ineligible jobs never reach the list);
2. relevance: the match score, in 3-point bands so near-ties are settled by evidence rather than noise;
3. evidence quality (match confidence), then active-status confidence (verified before likely);
4. the exact score (preferences are already inside it: location, work mode);
5. freshness (posted within 14 days, 45 days, older/unknown) - only a tie-breaker, never a reason on its own;
6. source authority (the employer's ATS over a board copy);
7. the listing key, so the same input always gives the same order.

Then a light diversity pass: at most `max_per_company` per employer, relaxed only if the list would otherwise be
short of the requested count (the extra jobs still passed the threshold, so relevance is not traded away)."""
from __future__ import annotations

import datetime as dt

from dedupe import norm_company
from models import Listing, Match

VERIFICATION_RANK = {"verified_active": 0, "likely_active": 1, "unverified": 2, "closed": 3}
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def freshness_bucket(listing: Listing, today: dt.date) -> int:
    if not listing.posted_at:
        return 2
    try:
        age = (today - dt.date.fromisoformat(listing.posted_at[:10])).days
    except ValueError:
        return 2
    return 0 if age <= 14 else 1 if age <= 45 else 2


def sort_key(listing: Listing, match: Match, today: dt.date) -> tuple:
    return (0 if match.eligible else 1, -(match.score // 3), CONFIDENCE_RANK[match.confidence],
            VERIFICATION_RANK[listing.verification.status], -match.score, freshness_bucket(listing, today),
            -listing.authority, listing.key)


def rank(items: list[tuple[Listing, Match]], today: dt.date) -> list[tuple[Listing, Match]]:
    return sorted(items, key=lambda lm: sort_key(lm[0], lm[1], today))


def diversify(ranked: list[tuple[Listing, Match]], count: int, max_per_company: int) -> list[tuple[Listing, Match]]:
    picked, overflow, per = [], [], {}
    for lm in ranked:
        company = norm_company(lm[0].company) or lm[0].key
        if per.get(company, 0) < max_per_company:
            per[company] = per.get(company, 0) + 1
            picked.append(lm)
        else:
            overflow.append(lm)
        if len(picked) >= count:
            break
    if len(picked) < count:
        picked.extend(overflow[: count - len(picked)])
    order = {id(lm[0]): i for i, lm in enumerate(ranked)}
    return sorted(picked, key=lambda lm: order[id(lm[0])])
