"""Not a test: writes real results of the graph (fakes, no network) as JSON fixtures for the web view.
Run: .venv\\Scripts\\python -m pytest -q -p no:cacheprovider tests/dump_fixtures.py -k dump --fixtures-out <dir>
(or simply with FIXTURES_OUT=<dir> in the environment)."""
import json
import os
from pathlib import Path

import pytest

import test_graph
from test_graph import (FOLLOW, MEMORY, NOTE_EMAIL, ago, event, fu_tools, inbox_tools, prepared, put_app, reading, run,
                        sending, sent_and_applied)

import replies
import store
import writer

OUT = Path(os.environ.get("FIXTURES_OUT") or "local/fixtures")


@pytest.fixture
def wired(monkeypatch):
    """test_graph's `wired`, declared here too: pytest 8 does not see a fixture that was only imported."""
    fn = getattr(test_graph.wired, "_get_wrapped_function", None)
    return (fn() if fn else test_graph.wired)(monkeypatch)


def dump(name: str, out: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(out["result"], indent=1, ensure_ascii=False, default=str), encoding="utf-8")


async def test_dump_prepared(wired):
    memory, c = await prepared(wired)
    out = await run({"inputs": {}, "memory": memory, "actions": ["report"]})   # the board after Prepare, as a result
    # a found job on the board too
    store.collection("jobs").put("zentrix.ai/jobs/ml", {"key": "zentrix.ai/jobs/ml", "order": "0", "source": "discovery", "picked": False,
                                                        "company": "Zentrix", "title": "ML Platform Engineer", "url": "https://zentrix.ai/jobs/ml",
                                                        "score": 86, "why": "Python, LangChain and Kubernetes match; no Go", "verified": "open",
                                                        "posted": "2026-11-01", "location": "Pune"})
    out = await run({"inputs": {}, "memory": memory, "actions": ["report"]})
    dump("prepared", out)


async def test_dump_sent_and_replied(wired):
    memory, c = await sent_and_applied(wired)
    words = "Can we talk on Friday about the Backend Engineer role?"
    c.update({replies.Reading: reading("interview", words),
              replies.Reply: replies.Reply(body="Thank you for writing. I would be glad to talk. Could you share two or three times that suit you?")})
    wired(inbox_tools(), c)
    out = await run({"memory": memory, "inputs": event({"thread_id": "t1", "from": "Priya <hr@blacksmith.sh>", "subject": "Re: Application", "text": words})})
    dump("replied", out)


async def test_dump_follow_ups_and_report(wired):
    wired(fu_tools(), {writer.Body: FOLLOW})
    put_app("a", stage="sent", thread="t1", sent_at=ago(4), email=NOTE_EMAIL, contact={"email": "priya@blacksmith.sh", "name": "Priya Nair"})
    put_app("b", company="Nimbus", domain="nimbus.io", title="Platform Engineer", stage="replied", reply_kind="interview", sent_at=ago(6),
            replied_at=ago(1), email={"kind": "application", "to": "hr@nimbus.io"}, last_reply="Can we talk on Friday?")
    put_app("c", company="Orbit", domain="orbit.dev", title="SRE", stage="done", reply_kind="rejection", sent_at=ago(20), replied_at=ago(10),
            email={"kind": "application", "to": "jobs@orbit.dev"})
    out = await run({"memory": MEMORY, "inputs": {}, "actions": ["follow_ups"]})
    dump("follow_ups", out)
    out = await run({"memory": MEMORY, "inputs": {}, "actions": ["report"]})
    dump("report", out)
