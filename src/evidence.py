"""No source, no fact (spec 5.2): every fact is a value, the page it came from, the exact words on that page and when
it was seen. A quote the page does not hold is dropped before anyone sees it."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

QUOTE_MIN_SCORE = 92  # rapidfuzz partial_ratio on normalised text: tolerates whitespace and punctuation, not rewording


class Fact(BaseModel):
    value: str | int | None
    url: str
    quote: str  # the exact words on the page
    seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_rank: int = 1  # 1 own site / job boards, 2 reputable news, 3 forums, 4 data provider (no quote)


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"[‘’“”]", "'", text)
    return re.sub(r"\s+", " ", re.sub(r"[^\w@.'%$ -]+", " ", text)).strip()


def quote_on_page(quote: str, page_text: str) -> bool:
    """The quote is on the page, word for word up to spacing and punctuation. Short quotes must match exactly."""
    q, page = normalise(quote), normalise(page_text)
    if len(q) < 8 or not page:
        return False
    if q in page:
        return True
    if any(ch.isdigit() for ch in q):  # a number must be the page's number: no fuzz for "$599" against "$499"
        return False
    return len(q) >= 24 and fuzz.partial_ratio(q, page) >= QUOTE_MIN_SCORE


def checked(facts: list[Fact], pages: dict[str, str]) -> tuple[list[Fact], int]:
    """(the facts whose quote is on their page, how many were dropped). A provider fact (rank 4) carries no quote."""
    kept = [f for f in facts if f.source_rank == 4 or quote_on_page(f.quote, pages.get(f.url, ""))]
    return kept, len(facts) - len(kept)


def cite(fact: Fact | None) -> dict | None:
    """A fact as a result cell shows it: the value, and where it came from."""
    if fact is None:
        return None
    return {"value": fact.value, "quote": fact.quote, "url": fact.url, "seen": fact.seen_at.date().isoformat()}
