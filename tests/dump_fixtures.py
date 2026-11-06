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


async def test_dump_tracking(wired):
    """Every state a tracked row can be in, with its history and what is next, beside two jobs not yet out."""
    wired(fu_tools(), {})
    app = {"kind": "application", "subject": "Application for Backend Engineer at Blacksmith", "body": "Hello, I built the billing service."}
    put_app("a", stage="sent", sent_by="gravity", thread="t1", sent_at=ago(9), follow_ups=1, email={**app, "to": "hr@blacksmith.sh"},
            contact={"email": "hr@blacksmith.sh", "kind": "application", "how": "given with the job"},
            follow_up_drafts=[{"n": 1, "at": ago(2), "text": "Hello again, one more thing since I wrote.", "id": "d1", "sent_at": ago(1), "edit_ratio": 0.2}])
    put_app("b", company="Nimbus", domain="nimbus.io", title="Platform Engineer", stage="replied", reply_kind="interview", sent_by="gravity",
            thread="t2", sent_at=ago(6), replied_at=ago(1), email={**app, "to": "hr@nimbus.io"},
            contact={"email": "hr@nimbus.io", "kind": "application", "how": "given with the job"}, last_reply="Can we talk on Friday about the role?",
            reply_draft={"id": "d2", "text": "Thank you for writing. Friday works; could you share a time?", "to": "hr@nimbus.io", "kind": "interview", "at": ago(1)})
    put_app("c", company="Lumen", domain="lumen.dev", title="Data Engineer", stage="sent", sent_by="you", edit_ratio=0.5, thread="t3", sent_at=ago(5),
            follow_ups=1, email={**NOTE_EMAIL, "to": "sam@lumen.dev"}, contact={"email": "sam@lumen.dev", "name": "Sam Rao"},
            draft_kept_at=ago(6), follow_up_drafts=[{"n": 1, "at": ago(1), "text": "Hello Sam, is the role still open?", "id": "d3"}])
    put_app("d", company="Kite", domain="kite.in", title="SRE", stage="applied", applied_on=ago(3)[:10])
    put_app("e", company="Orbit", domain="orbit.dev", title="Backend Engineer", stage="done", reply_kind="no_answer", sent_by="gravity", thread="t5",
            sent_at=ago(22), closed_at=ago(1), follow_ups=2, email={**app, "to": "jobs@orbit.dev"},
            contact={"email": "jobs@orbit.dev", "kind": "application", "how": "given with the job"},
            follow_up_drafts=[{"n": 1, "at": ago(15), "text": "Hello again.", "id": "d4", "sent_at": ago(15), "sent_by": "gravity", "edit_ratio": 0.0},
                              {"n": 2, "at": ago(8), "text": "Is the role still open?", "id": "d5"}])
    put_app("f", company="Quill", domain="quill.so", title="Platform Engineer", stage="docs_ready", email={**app, "to": "hr@quill.so"},
            contact={"email": "hr@quill.so", "kind": "application", "how": "given with the job"})
    store.collection("jobs").put("zentrix.ai/jobs/ml", {"key": "zentrix.ai/jobs/ml", "order": "g", "source": "discovery", "picked": False,
                                                        "company": "Zentrix", "title": "ML Platform Engineer", "url": "https://zentrix.ai/jobs/ml",
                                                        "score": 86, "why": "Python and Kubernetes match; no Go", "verified": "open"})
    jobs_coll = store.collection("jobs")
    jobs_coll.put("zentrix.ai/jobs/ml", {**jobs_coll.get("zentrix.ai/jobs/ml"), "trust": {"level": "doubtful", "why": ["posted 120 days ago", "not among the 14 open roles the company lists"]}})
    quill = store.collection("applications").get("f")
    store.collection("applications").put("f", {**quill, "trust": {"level": "solid", "why": ["open on the employer's own page, read this run"]}, "form": [
        {"q": "First Name", "a": "Aditya", "how": "from your details"}, {"q": "Why Quill?", "a": "I built the billing service.", "how": "drafted from your resume: read it first"},
        {"q": "Gender", "a": "", "how": "yours to answer"}]})
    store.collection("people").put("b", {"key": "b", "company": "Nimbus", "title": "Platform Engineer", "domain": "nimbus.io", "people": [
        {"name": "Ira Sen", "title": "Platform Engineer", "linkedin": "https://linkedin.com/in/ira", "common": "you both studied at RV College of Engineering",
         "email": "ira@nimbus.io", "how": "Apollo, verified", "note": "Hi Ira,\n\nWould you be open to referring me?\n\nBest,\nAditya", "draft_id": "d8"},
        {"name": "Sam Rao", "title": "Team Lead", "linkedin": "https://linkedin.com/in/sam", "common": "", "email": "", "how": "", "note": "Hi Sam,\n\nCould you say who is hiring for this?\n\nBest,\nAditya"}]})
    for i in range(4):
        put_app(f"x{i}", company=f"Quiet{i}", domain=f"quiet{i}.io", stage="sent", sent_by="gravity", thread=f"tx{i}", sent_at=ago(10), gaps=["Kubernetes"], follow_ups=1,
                email={**app, "to": f"hr@quiet{i}.io"}, contact={"email": f"hr@quiet{i}.io", "kind": "application", "how": "given with the job"})
    out = await run({"memory": MEMORY, "inputs": {}, "actions": ["report"]})
    dump("tracking", out)
