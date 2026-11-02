"""The person's Gmail as plain records, and everything code can decide about it without a model: the mailbox type
(3.5), the domains already emailed, the voice profile measured from sent mail (6.4), and the replies rules can
sort (6.1 step 4). Message shapes follow GMAIL_FETCH_EMAILS (messageId, threadId, sender, to, subject, messageText,
messageTimestamp, labelIds)."""

from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr
from statistics import median
from typing import Any

PERSONAL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "ymail.com",
    "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com", "aol.com", "gmx.com", "zoho.com", "yandex.com",
    "mail.com", "rediffmail.com",
})
ADDRESS = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
BOUNCE_FROM = re.compile(r"mailer-daemon|postmaster|mail delivery (subsystem|system)", re.I)
BOUNCE_TEXT = re.compile(r"delivery (status notification|has failed)|undeliverable|address not found|does not exist|"
                         r"user unknown|no such user|recipient rejected", re.I)
OOO = re.compile(r"^(automatic reply|auto(matic)?[- ]?reply|out of (the )?office|ooo\b|away from|on (annual )?leave)", re.I)
OPT_OUT = re.compile(r"\b(unsubscribe|remove me|take me off|stop (emailing|contacting)|do not (email|contact) me|"
                     r"don'?t (email|contact) me|opt(ed)? out)\b", re.I)
QUOTE_START = re.compile(r"^(on .{4,160} wrote:|-----original message-----|from: .+|sent from my \w+)", re.I)
GREETING = re.compile(r"^(hi|hey|hello|dear|good (morning|afternoon)|hiya)\b[^\n]{0,30}", re.I)


def messages(data: Any) -> list[dict]:
    """Gmail's shape (messageId, threadId, sender, messageText, messageTimestamp), the mail trigger's
    (id, thread_id, from, text, date) and contract v2's message records (gravity.read: id, thread, author {name,
    email}, text, time, labels) as one record."""
    items = (data.get("messages") or data.get("records")) if isinstance(data, dict) else data
    out = []
    for m in items or []:
        if not isinstance(m, dict):
            continue
        author = m.get("author") if isinstance(m.get("author"), dict) else {}
        frm = m.get("sender") or m.get("from") or (f"{author.get('name') or ''} <{author['email']}>".strip()
                                                    if author.get("email") else "")
        thread = m.get("threadId") or m.get("thread_id") or m.get("thread")
        out.append({"id": m.get("messageId") or m.get("id"), "thread": thread.get("id") if isinstance(thread, dict) else thread,
                    "from": frm, "to": m.get("to") if isinstance(m.get("to"), str) else "",
                    "subject": m.get("subject") or "", "text": m.get("messageText") or m.get("text") or "",
                    "when": when(m.get("messageTimestamp") or m.get("date") or m.get("time")),
                    "labels": [str(x) for x in m.get("labelIds") or m.get("labels") or []]})
    return sorted(out, key=lambda x: x["when"] or datetime.min.replace(tzinfo=UTC))


def when(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, int | float) or str(value).isdigit():
            n = int(value)
            return datetime.fromtimestamp(n / 1000 if n > 10**11 else n, UTC)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, OSError):
        return None


def address(header: str) -> str:
    return (parseaddr(header or "")[1] or "").lower()


def domain_of(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower() if "@" in (addr or "") else ""


def mailbox_type(email: str) -> str:
    """personal for a public provider's address (3.5): its domain is never the company, and cold sends are off."""
    return "personal" if domain_of(email) in PERSONAL_DOMAINS else "domain"


def recipients(m: dict) -> list[str]:
    return [a.lower() for _, a in getaddresses([m.get("to") or ""]) if a]


def known_domains(sent: list[dict], own: str) -> set[str]:
    """Every company domain the person already wrote to: never cold-emailed (3.5). Personal providers are left out."""
    found = {domain_of(a) for m in sent for a in recipients(m)}
    return {d for d in found if d and d != own and d not in PERSONAL_DOMAINS}


def typed_text(text: str) -> str:
    """What the person typed: the quoted thread and the signature cut off."""
    lines = []
    for line in (text or "").splitlines():
        if QUOTE_START.match(line.strip()) or line.startswith(">"):
            break
        if line.strip() in ("--", "-- "):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def business_like(m: dict) -> bool:
    """Counts for a personal mailbox's voice: written to a company address, not to a friend's personal one."""
    return any(domain_of(a) not in PERSONAL_DOMAINS for a in recipients(m))


def voice_profile(sent: list[dict], personal: bool) -> dict:
    """Measured in code from up to 60 sent emails (6.4): greeting, sign-off, length range, contractions,
    exclamation habit. Under 15 usable emails it says so and drafting uses the standard voice."""
    usable = [typed_text(m["text"]) for m in sent[-60:] if (business_like(m) or not personal)]
    usable = [t for t in usable if len(t.split()) >= 8]
    if len(usable) < 15:
        return {"standard": True, "emails": len(usable)}
    greet = Counter((GREETING.match(t.split("\n", 1)[0].strip()) or [""])[0].split(" ")[0].lower() for t in usable)
    signs = Counter(t.strip().splitlines()[-1].strip().rstrip(",").lower()[:30] for t in usable if t.strip())
    words = sorted(len(t.split()) for t in usable)
    contractions = sum(bool(re.search(r"\b\w+'(s|re|ll|ve|d|t|m)\b", t)) for t in usable) / len(usable)
    exclaims = sum("!" in t for t in usable) / len(usable)
    greeting = greet.most_common(1)[0][0]
    return {"standard": False, "emails": len(usable), "greeting": greeting or None,
            "sign_off": signs.most_common(1)[0][0] if signs else None,
            "words": [words[len(words) // 10], words[(len(words) * 9) // 10]], "median_words": int(median(words)),
            "contractions": contractions >= 0.4, "exclamations": exclaims >= 0.3}


def rule_reply(m: dict) -> str | None:
    """What code can say about an inbound message alone: bounce, out_of_office, opt_out; None leaves it to the model."""
    if BOUNCE_FROM.search(m["from"]) or (BOUNCE_TEXT.search(m["text"]) and "delivery" in m["subject"].lower()):
        return "bounce"
    if OOO.match(m["subject"].strip()):
        return "out_of_office"
    if OPT_OUT.search(typed_text(m["text"])):
        return "opt_out"
    return None


def edit_ratio(draft: str, sent: str) -> float:
    """How much of a draft the person rewrote before sending it: 0 for the same text, 1 for nothing in common."""
    a, b = (draft or "").split(), typed_text(sent or "").split()
    if not a or not b:
        return 1.0
    return round(1 - SequenceMatcher(None, a, b, autojunk=False).ratio(), 3)


def edited_words(ratio: float) -> str:
    return "not at all" if ratio < 0.05 else "a little" if ratio < 0.4 else "a lot" if ratio < 0.75 else "almost entirely"
