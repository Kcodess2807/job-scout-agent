"""The v2 calls and the model, through Gravity. Environment is read inside the functions, never at import."""

from __future__ import annotations

import os

try:
    import gravity  # preinstalled on the platform; not on PyPI
except ImportError:  # gravity run, gravity dev, pytest
    gravity = None
if gravity is not None and not hasattr(gravity, "memory"):  # an unrelated PyPI package named gravity
    gravity = None


class Gravity:
    """The contract v2 calls this agent makes, by its manifest's names: sources `mail` (message.list) and `page`
    (web_page.get), writes `send` (message.send, approved by the person) and `draft` (message.create). The person
    picked the apps at setup; records come back in the shapes' own fields."""

    _tracker: bool | None = None  # whether the person set up the optional `tracker` write (a Google Sheet)

    async def track(self, rows: list[dict]) -> str | None:
        """The optional `tracker` write (row.upsert on the person's sheet, keyed on each row's first column): None
        when done or when they set no sheet up, else why it failed."""
        if self._tracker is None:
            self._tracker = "writes.tracker" in await gravity.capabilities()
        if not self._tracker:
            return None
        try:
            await gravity.act("tracker", rows=rows)
        except Exception as e:  # noqa: BLE001 - gravity.CallFailed: the run goes on, the result says so
            return str(e)[:200]
        return None

    async def page(self, url: str) -> str:
        """The page's text, or "" when the app could not read it."""
        try:
            got = await gravity.read("page", args={"url": url})
        except Exception:  # noqa: BLE001 - gravity.CallFailed: an unreadable page is an empty one
            return ""
        records = got.get("records") or []
        return (records[0].get("text") or "") if records else ""

    async def read(self, source: str, **args) -> dict:
        """A read of any declared source, as the search engine calls it (search, page, search_more, answers,
        similar). A source the person did not set up raises, which the engine treats as that channel being off."""
        return await gravity.read(source, **args)

    async def mail(self, folder: str) -> list[dict]:
        """The messages of one standard mailbox ("inbox" or "sent") inside the manifest's window (30 days), oldest
        first, as mail.messages records. The same words on Gmail and Outlook (the shape's `folder` filter, gravity-schema
        0.13.8); anything narrower (an address, a thread, a day) is the caller's to pick out of the records."""
        import mail
        return mail.messages(await gravity.read("mail", filters={"folder": folder}))

    async def send(self, to: str, subject: str, text: str, file: dict | None = None) -> dict:
        """{"id", "thread"} once sent; {"rejected": True} when the person said no or nobody answered. Raises
        RuntimeError with the reason when the app refused. `file`: {name, data (base64)}, the attachment."""
        try:
            got = await gravity.act("send", draft={"to": [to], "subject": subject, "text": text}, **({"file": file} if file else {}))
        except Exception as e:  # noqa: BLE001 - gravity.CallFailed
            if "APPROVAL_TIMEOUT" in str(e):
                return {"rejected": True}
            raise RuntimeError(str(e)[:300]) from None
        if got.get("rejected"):
            return {"rejected": True}
        record = got.get("record") or {}
        return {"id": record.get("id"), "thread": record.get("thread")}

    async def find_email(self, first: str, last: str, domain: str) -> dict:
        """{email, status} for a person at a company, from the `hunter.find_email` and `hunter.verify_email` built-ins
        (Gravity's account): {} when nothing was found, the address is invalid, or the built-ins are off. status: valid,
        accept_all, risky or unknown."""
        try:
            email = _dig(await gravity.built_in("hunter.find_email", domain=domain, first_name=first, last_name=last), "email")
            if not isinstance(email, str) or "@" not in email:
                return {}
            status = _dig(await gravity.built_in("hunter.verify_email", email=email), "status")
        except Exception:  # noqa: BLE001 - gravity.CallFailed: the built-in is not in this run, or Hunter failed
            return {}
        status = str(status or "unknown").lower()
        return {} if status == "invalid" else {"email": email.lower(), "status": status}

    async def built_in(self, name: str, **args) -> dict:
        """A read on Gravity's own accounts (Apollo, PredictLeads, a public LinkedIn profile): {} when it is off or failed,
        so a run with less data still runs."""
        try:
            got = await gravity.built_in(name, **args)
        except Exception:  # noqa: BLE001 - gravity.CallFailed
            return {}
        return got if isinstance(got, dict) else {}

    async def send_draft(self, draft_id: str) -> dict:
        """Sends a draft the agent kept, as the person left it (the `send_draft` write, under approval). {"id", "thread"}
        once sent; {"rejected": True} when the person said no or nobody answered; RuntimeError when the app refused
        (the draft is gone: they sent or deleted it themselves)."""
        try:
            got = await gravity.act("send_draft", draft={"id": draft_id})
        except Exception as e:  # noqa: BLE001 - gravity.CallFailed
            if "APPROVAL_TIMEOUT" in str(e):
                return {"rejected": True}
            raise RuntimeError(str(e)[:300]) from None
        if got.get("rejected"):
            return {"rejected": True}
        record = got.get("record") or {}
        return {"id": record.get("id"), "thread": record.get("thread")}

    async def draft(self, to: str, subject: str, text: str, thread: str | None = None, file: dict | None = None) -> dict:
        """A draft in the person's mailbox, in `thread` when given."""
        draft = {"to": [to], "subject": subject, "text": text, **({"thread": thread} if thread else {})}
        got = await gravity.act("draft", draft=draft, **({"file": file} if file else {}))
        return got.get("record") or {}


def _dig(obj, key: str):
    """The first value under `key` anywhere in a nested answer (Hunter's comes wrapped in `data`)."""
    if isinstance(obj, dict):
        if key in obj and not isinstance(obj[key], (dict, list)):
            return obj[key]
        for v in obj.values():
            if (got := _dig(v, key)) is not None:
                return got
    elif isinstance(obj, list):
        for v in obj:
            if (got := _dig(v, key)) is not None:
                return got
    return None


def make_embedder():
    """The search's embeddings (its gateway.py), or None without a model endpoint."""
    from gateway import make_embedder as his  # src/scout, on the path once discover is imported

    return his()


def make_io() -> Gravity:
    if gravity is None:
        raise RuntimeError("contract v2 needs the gravity SDK: on the platform it is preinstalled; for `gravity run`, "
                           "uv pip install -e <core repo>/packages/gravity-sdk into this agent's .venv")
    return Gravity()


class Model:
    """The chat model, plus the notes a run collects about it (a spent budget). ChatOpenAI itself takes no new
    attributes, so the notes live here."""

    def __init__(self, llm):
        self.llm, self.notes = llm, []

    def with_structured_output(self, schema):
        return self.llm.with_structured_output(schema)


def make_model(max_tokens: int = 4000) -> Model:  # 1500 cut a cover letter off on the first real run (a reasoning model spends part of it)
    from langchain_openai import ChatOpenAI

    return Model(ChatOpenAI(model="openai/gpt-6-luna", base_url=os.environ["GRAVITY_LLM_URL"],
                            api_key=os.environ["GRAVITY_RUN_TOKEN"], max_tokens=max_tokens, max_retries=2))


BUDGET_NOTE = "the run's budget ran out, so the model stopped reading pages and drafting; what code found is shown"


async def structured(model, schema, system: str, user: str):
    """One structured model call; None when it fails (a failed call drops the prose, not the run). A spent budget
    is noted once on the model (`model.notes`) for the node's result."""
    try:
        return await model.with_structured_output(schema).ainvoke(
            [{"role": "system", "content": system}, {"role": "user", "content": user}])
    except Exception as e:  # noqa: BLE001 - the caller falls back to code or leaves the field blank
        notes = getattr(model, "notes", None)
        if notes is None:
            try:
                model.notes = notes = []
            except Exception:  # noqa: BLE001 - a model object that takes no attributes: the note is lost
                notes = []
        note = BUDGET_NOTE if "BUDGET_EXCEEDED" in str(e) else f"a model call failed ({type(e).__name__}): {str(e)[:200]}"
        if note not in notes:
            notes.append(note)   # the result's could_not says why a field is blank, once per reason
        return None
