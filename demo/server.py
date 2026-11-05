"""applyzer's demo, for us, for testing: the real graph (src/agent.py) runs in this process with a pretend mailbox,
sends you approve with a tap, a clock you can skip ahead, and a live picture of which part of the agent is working.
Nothing here is part of the agent; nothing is sent anywhere.

    .venv\\Scripts\\python demo\\server.py          then open the printed link here, or on your phone (same Wi-Fi)

A stand-in model answers by default (demo/rehearsal.py). For a real one, any OpenAI-compatible endpoint:

    $env:DEMO_LLM_URL="https://api.groq.com/openai/v1"; $env:DEMO_LLM_KEY="<key>"; $env:DEMO_LLM_MODEL="llama-3.3-70b-versatile"
"""

from __future__ import annotations

import asyncio
import base64
import html as htmllib
import json
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEMO = Path(__file__).resolve().parent
STATE_DIR = DEMO / ".state"
sys.path[:0] = [str(DEMO.parent / "src"), str(DEMO), str(DEMO.parent / "tests" / "scout")]  # his fixtures feed the demo's search
os.environ["GRAVITY_MEMORY_DIR"] = str(STATE_DIR / "memory")  # collections and PDFs live here (store.py, agent.save_local)
for _k, _v in {"GRAVITY_RUN_TOKEN": "demo", "GRAVITY_LLM_URL": "http://demo", "GRAVITY_GATEWAY_URL": "http://demo"}.items():
    os.environ.setdefault(_k, _v)

import httpx  # noqa: E402

import agent  # noqa: E402
import discover  # noqa: E402  (puts src/scout on the path)
import fakes as scout_fakes  # noqa: E402  (his FakeWeb: synthetic postings, so the search works offline)
import mail  # noqa: E402
import rehearsal  # noqa: E402
import samples  # noqa: E402
import stages  # noqa: E402
import store  # noqa: E402
import tailor  # noqa: E402
import writer  # noqa: E402
from candidate import Profile  # noqa: E402

import tooling  # noqa: E402

# The demo runs on the agent's own local stand-ins, as the tests do: the platform SDK (installed in this venv for
# `gravity run`) keeps its own local memory, which wants the manifest's collections declared to it.
tooling.gravity = store.gravity = None

KEY = secrets.token_urlsafe(9)
LOCK = threading.Lock()
EVENTS: list[dict] = []
LOOP = asyncio.new_event_loop()
PENDING: dict = {}            # the send waiting for a tap: {id, to, subject, text, file, future}
RUNNING = threading.Event()
ME = "you@your-gmail.example"


def _fresh() -> dict:
    return {"inputs": {"jobs": "", "resume_text": samples.SAMPLE_RESUME, "notice_days": 30,
                       "expected_ctc": "28 LPA", "work_auth": "can work there", "relocate": "yes",
                       "role": "AI engineer", "country": "India", "city": "Pune", "remote": "either", "count": "5",
                       "looking_for": "jobs", "employment_type": "any"},
            "memory": {}, "mailbox": {"sent": [], "drafts": [], "inbox": []}, "sheet": {}, "offset_days": 0, "result": None,
            "action": None, "seq": 0}


STATE = _fresh()


def save() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "demo.json").write_text(json.dumps({k: v for k, v in STATE.items() if k != "action"}, default=str),
                                         encoding="utf-8")


def load() -> None:
    f = STATE_DIR / "demo.json"
    if f.is_file():
        STATE.update(json.loads(f.read_text(encoding="utf-8")))


def now() -> datetime:
    return datetime.now(UTC) + timedelta(days=STATE["offset_days"])


def emit(kind: str, node: str, phase: str = "", detail: str = "") -> None:
    with LOCK:
        STATE["seq"] += 1
        EVENTS.append({"seq": STATE["seq"], "t": time.time(), "kind": kind, "node": node, "phase": phase,
                       "detail": str(detail)[:240]})
        del EVENTS[:-1500]


# ── the agent's v2 calls, against the pretend mailbox and the web ──────────────────────────────────────
def _text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", page)
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"(?s)<[^>]+>", " ", page))).strip()


class DemoIo:
    async def page(self, url: str) -> str:
        emit("tool", "web", "start", url)
        text = samples.PAGES.get(url)
        if text is None:
            try:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                             headers={"User-Agent": "Mozilla/5.0 applyzer-demo"}) as http:
                    r = await http.get(url)
                    text = _text(r.text) if r.status_code == 200 else ""
            except httpx.HTTPError:
                text = ""
        emit("tool", "web", "end", f"{len(text):,} characters" if text else "nothing readable")
        return text

    async def read(self, source: str, **args) -> dict:
        """The search's sources. `search` answers from his synthetic postings (tests/scout/fixtures), so the demo
        works offline; `page` tries those first, then the real web. The optional channels are off."""
        if source == "search":
            emit("tool", "search", "start", f"searching: {args.get('text', '')[:80]}")
            got = await scout_fakes.FakeWeb()("jobs", **args)
            emit("tool", "search", "end", f"{len(got.get('records') or [])} results")
            return got
        if source == "page":
            url = (args.get("args") or {}).get("url") or args.get("id") or ""
            try:
                return await scout_fakes.FakeWeb()("pages", **args)
            except RuntimeError:
                text = await self.page(url)
                return {"records": [{"url": url, "text": text}] if text else [], "complete": True, "warnings": []}
        raise RuntimeError(f"this run has no sources.{source}")

    async def find_email(self, first: str, last: str, domain: str) -> dict:
        """The Hunter built-ins: not in the demo (nothing leaves the machine), so a person's address comes from the
        company's pages or not at all."""
        return {}

    async def mail(self, folder: str) -> list[dict]:
        emit("tool", "mail", "start", f"reading {folder}")
        found = STATE["mailbox"]["sent" if folder == "sent" else "inbox"]
        emit("tool", "mail", "end", f"{len(found)} message{'s' if len(found) != 1 else ''}")
        return mail.messages(found)

    async def send(self, to: str, subject: str, text: str, file: dict | None = None) -> dict:
        future = LOOP.create_future()
        PENDING.update({"id": uuid.uuid4().hex[:8], "to": to, "subject": subject, "text": text,
                        "file": (file or {}).get("name"), "future": future})
        emit("gate", "you", "start", f"waiting for your yes: {subject}")
        decision = await future
        PENDING.clear()
        emit("gate", "you", "end", "approved" if decision == "approve" else "kept as a draft")
        if decision != "approve":
            return {"rejected": True}
        emit("tool", "mail", "start", f"sending to {to}")
        thread = "t-" + uuid.uuid4().hex[:8]
        STATE["mailbox"]["sent"].append({"messageId": "m-" + uuid.uuid4().hex[:8], "threadId": thread, "sender": ME,
                                         "to": to, "subject": subject, "messageText": text,
                                         "messageTimestamp": now().isoformat(), "attachment": (file or {}).get("name")})
        emit("tool", "mail", "end", "sent" + (f" with {file['name']}" if file else ""))
        return {"id": "m-" + thread, "thread": thread}

    async def track(self, rows: list[dict]) -> str | None:
        """The optional Google Sheet, pretend: one row per application, updated by its link."""
        emit("tool", "sheet", "start", f"{len(rows)} row{'s' if len(rows) != 1 else ''} to your sheet")
        sheet = STATE.setdefault("sheet", {})
        for r in rows:
            sheet[r["Link"]] = {**sheet.get(r["Link"], {}), **r}
        emit("tool", "sheet", "end", "updated")
        return None

    async def send_draft(self, draft_id: str) -> dict:
        """The kept draft goes out as it is: it moves from the pretend Drafts to Sent, after the same yes as a send."""
        box = STATE["mailbox"]
        d = next((x for x in box["drafts"] if x.get("id") == draft_id), None)
        if d is None:
            raise RuntimeError("message.send_draft: the draft is gone")
        future = LOOP.create_future()
        PENDING.update({"id": uuid.uuid4().hex[:8], "to": d["to"], "subject": d["subject"], "text": d["text"],
                        "file": d.get("attachment"), "future": future})
        emit("gate", "you", "start", f"waiting for your yes: {d['subject']}")
        if not await future:
            emit("gate", "you", "end", "not approved")
            return {"rejected": True}
        emit("gate", "you", "end", "approved")
        box["drafts"].remove(d)
        thread = d.get("thread") or "t-" + uuid.uuid4().hex[:6]
        box["sent"].append({"messageId": "m-" + uuid.uuid4().hex[:8], "threadId": thread, "sender": ME, "to": d["to"],
                            "subject": d["subject"], "messageText": d["text"], "messageTimestamp": now().isoformat(), "attachment": d.get("attachment")})
        return {"id": box["sent"][-1]["messageId"], "thread": thread}

    async def draft(self, to: str, subject: str, text: str, thread: str | None = None, file: dict | None = None) -> dict:
        emit("tool", "mail", "start", f"drafting to {to}")
        did = "d-" + uuid.uuid4().hex[:8]
        STATE["mailbox"]["drafts"].append({"id": did, "to": to, "subject": subject, "text": text, "thread": thread,
                                           "attachment": (file or {}).get("name"), "at": now().isoformat()})
        emit("tool", "mail", "end", "draft saved" + (" in its thread" if thread else ""))
        return {"id": did}


# ── the model, watched: each structured call lights its subagent ───────────────────────────────────────
AGENT_OF = {"Profile": "profile", "RepoOut": "github", "Requirements": "posting", "Pick": "picker", "Tailored": "tailor",
            "Letter": "letter", "People": "contact", "Body": "writer", "Reading": "sorter", "Reply": "replier"}


class Live:
    def __init__(self, llm):
        self.llm, self.notes = llm, []

    def with_structured_output(self, schema):
        return self.llm.with_structured_output(schema, method=os.environ.get("DEMO_LLM_METHOD", "function_calling"))


class Watched:
    def __init__(self, inner):
        self.inner, self.notes = inner, inner.notes

    def with_structured_output(self, schema):
        bound = self.inner.with_structured_output(schema)
        node = AGENT_OF.get(schema.__name__, "matcher" if STATE.get("action") in ("discover", "prepare") else "orchestrator")
        if node == "writer" and STATE.get("action") == "follow_ups":
            node = "followup"

        class Bound:
            async def ainvoke(self, messages):
                emit("agent", node, "start")
                try:
                    return await bound.ainvoke(messages)
                finally:
                    emit("agent", node, "end")
        return Bound()


def model_mode() -> str:
    return "live: " + os.environ.get("DEMO_LLM_MODEL", "llama-3.3-70b-versatile") if os.environ.get("DEMO_LLM_URL") \
        and os.environ.get("DEMO_LLM_KEY") else "rehearsal model"


def make_model():
    if model_mode().startswith("live"):
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=os.environ.get("DEMO_LLM_MODEL", "llama-3.3-70b-versatile"), base_url=os.environ["DEMO_LLM_URL"],
                         api_key=os.environ["DEMO_LLM_KEY"], max_tokens=1500, max_retries=2)
        return Watched(Live(llm))
    return Watched(rehearsal.Rehearsal())


# ── code's own checks, watched too ─────────────────────────────────────────────────────────────────────
_grounded, _email_checks, _can_send = tailor.grounded, writer.email_checks, stages.can_send


def grounded(doc, source, terms=()):
    emit("check", "facts", "start", "every resume line against your own resume")
    out, dropped = _grounded(doc, source, terms)
    emit("check", "facts", "end", f"{dropped} invented line{'s' if dropped != 1 else ''} removed" if dropped else "nothing invented")
    return out, dropped


def email_checks(*args, **kwargs):
    emit("check", "facts", "start", "the email: length, claims, links, tone")
    problems = _email_checks(*args, **kwargs)
    emit("check", "facts", "end", "; ".join(problems) or "clean")
    return problems


def can_send(*args, **kwargs):
    emit("check", "gate", "start", "daily cap and one email per company")
    why = _can_send(*args, **kwargs)
    emit("check", "gate", "end", why or "clear to ask you")
    return why


tailor.grounded, writer.email_checks, stages.can_send = grounded, email_checks, can_send
agent.make_io, agent.make_model, agent.utcnow = DemoIo, make_model, now
agent.make_embedder = lambda: None  # the search's embeddings: lexical fallback in the demo


# ── runs ────────────────────────────────────────────────────────────────────────────────────────────────
async def run(action: str, action_inputs: dict | None = None, event: list | None = None) -> None:
    STATE["action"] = action
    inputs = dict(STATE["inputs"])
    if event:
        inputs["event"] = {"emails": event}
    state = {"inputs": inputs, "memory": STATE["memory"], "actions": [] if action == "mail_trigger" else [action],
             "action_inputs": action_inputs or {}}
    emit("run", "orchestrator", "start", action)
    try:
        graph = await agent.graph()
        last = None
        async for mode, data in graph.astream(state, stream_mode=["debug", "values"]):
            if mode == "values":
                last = data
            elif data.get("type") in ("task", "task_result"):
                emit("node", data["payload"]["name"], "start" if data["type"] == "task" else "end")
        STATE["result"] = (last or {}).get("result")
        STATE["memory"] = (last or {}).get("memory") or STATE["memory"]
        emit("run", "orchestrator", "end", (STATE["result"] or {}).get("summary", "done"))
    except Exception as e:  # noqa: BLE001 - shown in the page, the full trace in this terminal
        traceback.print_exc()
        emit("error", "orchestrator", "end", f"{type(e).__name__}: {e}")
    finally:
        STATE["action"] = None
        RUNNING.clear()
        save()


def start(action: str, **kw) -> None:
    if RUNNING.is_set():
        raise ValueError("a run is already going: wait for it, or answer the approval")
    RUNNING.set()
    asyncio.run_coroutine_threadsafe(run(action, **kw), LOOP)


def app_at(row: int) -> dict:
    keys = (STATE["memory"] or {}).get("last_rows") or []
    if not 0 < row <= len(keys):
        raise ValueError("no such row")
    found = store.collection("applications").get(keys[row - 1])
    if not found:
        raise ValueError("that application is gone")
    return found


def simulate_reply(row: int, kind: str) -> None:
    """A message from the company, as the mail trigger would bring it: in the application's thread from its
    contact, or (for a form application) from the ATS, naming the company."""
    a = app_at(row)
    text = samples.REPLY_TEMPLATES[kind]
    contact = a.get("contact") or {}
    if a.get("thread") and kind != "auto_ack":
        frm = f"{contact.get('name') or 'Hiring team'} <{contact.get('email') or (a.get('email') or {}).get('to')}>"
        thread, subject = a["thread"], "Re: " + ((a.get("email") or {}).get("subject") or a["title"])
    else:
        frm, thread = "Greenhouse <no-reply@us.greenhouse-mail.io>", "t-" + uuid.uuid4().hex[:8]
        subject, text = f"Your application to {a['company']}", f"{text} ({a['company']}, {a['title']})"
    msg = {"messageId": "r-" + uuid.uuid4().hex[:8], "threadId": thread, "sender": frm, "to": ME, "subject": subject,
           "messageText": text, "messageTimestamp": now().isoformat()}
    STATE["mailbox"]["inbox"].append(msg)
    event = {"id": msg["messageId"], "thread_id": thread, "from": frm, "subject": subject, "text": text, "date": msg["messageTimestamp"]}
    start("mail_trigger", event=[event])


def reset() -> None:
    if RUNNING.is_set():
        raise ValueError("wait for the run to finish")
    shutil.rmtree(STATE_DIR, ignore_errors=True)
    store._LOCAL.clear()
    STATE.clear()
    STATE.update(_fresh())
    with LOCK:
        EVENTS.clear()
    save()


def snapshot(after: int) -> dict:
    with LOCK:
        events = [e for e in EVENTS if e["seq"] > after]
    box = STATE["mailbox"]
    pending = {k: v for k, v in PENDING.items() if k != "future"} if PENDING else None
    return {"events": events, "running": RUNNING.is_set(), "action": STATE.get("action"), "approval": pending,
            "result": STATE["result"], "inputs": STATE["inputs"], "model": model_mode(),
            "clock": {"days": STATE["offset_days"], "date": now().date().isoformat()},
            "mailbox": {k: list(reversed(v))[:30] for k, v in box.items()},
            "sheet": list((STATE.get("sheet") or {}).values())}


# ── HTTP ───────────────────────────────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):  # quiet: the page shows what happened
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, default=str).encode("utf-8"), "application/json")

    def _ok(self, q: dict) -> bool:
        if (q.get("k") or [""])[0] == KEY or self.headers.get("X-Demo-Key") == KEY:
            return True
        self._send(403, b"Open the link the demo printed (it carries the key).", "text/plain")
        return False

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if not self._ok(q):
            return
        if url.path == "/":
            return self._send(200, (DEMO / "index.html").read_bytes(), "text/html; charset=utf-8")
        if url.path == "/api/state":
            return self._json(200, snapshot(int((q.get("after") or ["0"])[0])))
        if url.path == "/api/pdf":
            try:
                a = app_at(int((q.get("row") or ["0"])[0]))
                p = Profile(**(STATE["memory"] or {}).get("profile") or {})
                if (q.get("kind") or ["resume"])[0] == "letter":
                    data, name = tailor.letter_pdf(p, a["company"], a.get("letter") or ""), tailor.pdf_name(p, a["company"], a["title"], "Cover_Letter")
                else:
                    data, name = tailor.pdf_bytes(tailor.ResumeDoc(**a["resume"])), tailor.pdf_name(p, a["company"], a["title"])
            except (ValueError, KeyError, TypeError) as e:
                return self._send(404, f"No PDF: {e}".encode(), "text/plain")
            return self._send(200, data, "application/pdf", {"Content-Disposition": f'inline; filename="{name}"'})
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        url = urlparse(self.path)
        if not self._ok(parse_qs(url.query)):
            return
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 200_000)) or b"{}")
            if url.path == "/api/inputs":
                STATE["inputs"].update({k: v for k, v in body.items() if k in (
                    "jobs", "resume_text", "github_username", "notice_days", "current_ctc", "expected_ctc", "work_auth", "relocate", "links",
                    "role", "country", "city", "count")})
                save()
            elif url.path == "/api/run":
                action = body.get("action")
                if action not in ("prepare", "paste_posting", "send_email", "mark_applied", "not_applying",
                                  "check_replies", "follow_ups", "report", "discover", "pick"):
                    raise ValueError("unknown action")
                ai = {k: body[k] for k in ("text", "applied_on") if body.get(k)}
                if body.get("row"):
                    ai["rows"] = [str(body["row"])]
                start(action, action_inputs=ai)
            elif url.path == "/api/approve":
                if not PENDING:
                    raise ValueError("nothing is waiting for approval")
                LOOP.call_soon_threadsafe(PENDING["future"].set_result, "approve" if body.get("decision") == "approve" else "reject")
            elif url.path == "/api/reply":
                if body.get("kind") not in samples.REPLY_TEMPLATES:
                    raise ValueError("unknown reply kind")
                simulate_reply(int(body.get("row") or 0), body["kind"])
            elif url.path == "/api/clock":
                STATE["offset_days"] = max(0, STATE["offset_days"] + int(body.get("days") or 0))
                emit("clock", "orchestrator", "", f"today is now {now().date().isoformat()}")
                save()
            elif url.path == "/api/reset":
                reset()
            else:
                return self._send(404, b"not found", "text/plain")
            self._json(200, {"ok": True})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            self._json(400, {"error": str(e)})


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    port = int(os.environ.get("DEMO_PORT") or 8765)
    load()
    threading.Thread(target=LOOP.run_forever, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"\n  applyzer demo  ({model_mode()})\n")
    print(f"  this machine   http://127.0.0.1:{port}/?k={KEY}")
    print(f"  your phone     http://{lan_ip()}:{port}/?k={KEY}   (same Wi-Fi; allow Python through the firewall once)\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
