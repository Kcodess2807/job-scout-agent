"""The copied modules' rules, carried over from signal-leads so they stay pinned here: the quote check, mailbox
type, rule-sorted replies, the edit ratio, the import guard, the budget note."""

from datetime import UTC, datetime, timedelta

import mail
from evidence import Fact, checked, quote_on_page


def test_a_fact_is_kept_only_when_its_exact_words_are_on_the_page():
    page = "Acme automates reconciliation for finance teams at mid-market SaaS companies.  Pricing starts at $499/month."
    assert quote_on_page("automates reconciliation for finance teams", page)
    assert quote_on_page("Pricing  starts at $499 / month", page)                     # spacing and punctuation
    assert not quote_on_page("automates bookkeeping for finance teams", page)         # reworded: dropped
    assert not quote_on_page("$499", page)                                              # too short to prove anything
    facts = [Fact(value="$499/month", url="u", quote="Pricing starts at $499/month"),
             Fact(value="SOC 2", url="u", quote="SOC 2 Type II certified since 2021")]
    kept, dropped = checked(facts, {"u": page})
    assert [f.value for f in kept] == ["$499/month"] and dropped == 1

def test_a_personal_mailbox_and_the_domains_already_emailed():
    assert mail.mailbox_type("raj@gmail.com") == "personal" and mail.mailbox_type("raj@acme.com") == "domain"
    sent = [{"to": "Priya <priya@nestcap.io>, mum@gmail.com"}, {"to": "ops@acme.com"}]
    assert mail.known_domains(sent, own="acme.com") == {"nestcap.io"}


def _sent(text, to="lead@corp.io"):
    return {"to": to, "text": text}

def test_rules_sort_bounces_out_of_office_and_opt_outs_before_any_model():
    m = lambda **k: {"from": "a@b.io", "subject": "", "text": "", **k}  # noqa: E731
    assert mail.rule_reply(m(**{"from": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>"})) == "bounce"
    assert mail.rule_reply(m(subject="Automatic reply: back on the 10th")) == "out_of_office"
    assert mail.rule_reply(m(text="Please remove me from your list.\n\nOn Mon, raj wrote:\n> hi")) == "opt_out"
    assert mail.rule_reply(m(text="Sounds interesting, can you share pricing?")) is None

def test_the_real_model_carries_the_runs_notes(monkeypatch):
    from tooling import make_model
    monkeypatch.setenv("GRAVITY_LLM_URL", "http://x")
    monkeypatch.setenv("GRAVITY_RUN_TOKEN", "t")
    m = make_model()
    assert m.notes == [] and callable(m.with_structured_output)
    m.notes.append("x")                                  # never raises, whatever the model class allows
    assert m.notes == ["x"]

def test_the_edit_ratio_is_zero_for_the_same_text_and_about_half_when_half_is_rewritten():
    from mail import edit_ratio
    draft = "hi priya,\n\nsaw nestcap is hiring its first controller. worth a short look once they start?\n\nthanks, raj"
    assert edit_ratio(draft, draft) == 0.0
    assert edit_ratio(draft, draft.replace("worth a short look once they start?", "could we grab twenty minutes next week?")) < 0.5
    half = "hi priya,\n\nsaw nestcap is hiring its first controller. " + "completely different second half here about budgets and audits. " * 2
    assert 0.3 <= edit_ratio(draft, half) <= 0.7
    assert edit_ratio("", "anything") == 1.0

def test_a_quote_with_a_number_must_match_exactly():
    page = "Pricing starts at $499/month, billed annually, for finance teams of any size."
    assert quote_on_page("Pricing starts at $499/month, billed annually", page)
    assert not quote_on_page("Pricing starts at $599/month, billed annually", page)        # a changed number: dropped
    assert quote_on_page("Pricing starts at $499/month billed annually for finance teams", page)

def test_an_unrelated_pypi_package_named_gravity_is_not_the_platform(monkeypatch):
    import importlib
    import sys
    import types
    monkeypatch.setitem(sys.modules, "gravity", types.ModuleType("gravity"))       # no .memory, no .tools
    import tooling
    importlib.reload(tooling)
    assert tooling.gravity is None
    monkeypatch.delitem(sys.modules, "gravity")
    importlib.reload(tooling)


def test_under_gravity_run_collections_live_in_the_memory_folder_the_cli_shows(monkeypatch, tmp_path):
    import json

    import store
    monkeypatch.setenv("GRAVITY_MEMORY_DIR", str(tmp_path))
    store._LOCAL.clear()
    store.collection("applications").put("acme.com/jobs/1", {"stage": "sent"})
    store._LOCAL.clear()                                   # the next `gravity run` is a new process
    assert store.collection("applications").get("acme.com/jobs/1") == {"stage": "sent"}
    on_disk = json.loads((tmp_path / "applications.json").read_text(encoding="utf-8"))
    assert on_disk["acme.com/jobs/1"]["value"] == {"stage": "sent"}   # what `gravity memory show` prints


def test_a_v2_message_record_reads_as_the_same_record():
    [m] = mail.messages({"records": [{"id": "m1", "thread": "t1", "subject": "Re: Application", "text": "Can we talk?",
                                      "time": "2026-11-02T10:00:00Z", "author": {"name": "Priya Nair", "email": "Priya@Acme.com"},
                                      "labels": ["INBOX"]}]})
    assert (m["id"], m["thread"], m["subject"], m["text"]) == ("m1", "t1", "Re: Application", "Can we talk?")
    assert mail.address(m["from"]) == "priya@acme.com" and m["from"].startswith("Priya Nair")
    assert m["when"] == datetime(2026, 11, 2, 10, tzinfo=UTC)


async def test_the_v2_page_read_passes_the_url_under_args_as_the_engine_maps_it(monkeypatch):
    """gravity-connect renders web_page.get's URL from `{args.url}`: a read with url= as a bare keyword reads nothing."""
    import tooling
    calls = []

    class G:
        async def read(self, source, **kw):
            calls.append((source, kw))
            return {"records": [{"url": kw.get("args", {}).get("url"), "text": "the posting " * 50}]}
    monkeypatch.setattr(tooling, "gravity", G())
    text = await tooling.Gravity().page("https://acme.com/jobs/1")
    assert calls == [("page", {"args": {"url": "https://acme.com/jobs/1"}})] and text.startswith("the posting")
