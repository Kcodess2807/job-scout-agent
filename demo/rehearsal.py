"""A stand-in model for the demo, so every node runs with no API key. It answers each structured call from the text
it is given (the resume, the posting, the reply), never from a script, so what you see is the agent's own code
deciding over plausible model output. Set DEMO_LLM_URL and DEMO_LLM_KEY to use a real model instead (server.py)."""

from __future__ import annotations

import json
import re

import candidate
import replies
import research
import tailor
import writer

SKILLS = ["Python", "Go", "Golang", "Postgres", "PostgreSQL", "Kubernetes", "Docker", "AWS", "GCP", "Kafka", "Redis",
          "React", "TypeScript", "JavaScript", "Java", "Rust", "SQL", "Terraform", "gRPC", "FastAPI", "Django",
          "Node.js", "Airflow", "Spark", "LangGraph", "Linux", "CI/CD"]
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"\+?\d[\d\s()-]{8,}\d")


def _found(text: str) -> list[str]:
    return [s for s in SKILLS if re.search(r"(?<![\w.])" + re.escape(s) + r"(?![\w])", text)]


def parse_resume(text: str) -> candidate.Profile:
    """Sections by their heading (EXPERIENCE, PROJECTS...), fields split on |, bullets on - or •."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    p = candidate.Profile(full_name=lines[0] if lines else "")
    head = " ".join(lines[1:3])
    p.email = (EMAIL.search(head) or EMAIL.search(text) or [""])[0] if EMAIL.search(text) else ""
    p.phone = PHONE.search(head).group(0).strip() if PHONE.search(head) else ""
    p.links = re.findall(r"https?://\S+", head)
    parts = [x.strip() for x in head.split("|")]
    p.location = next((x for x in parts if x and "@" not in x and not x.startswith("http") and not PHONE.fullmatch(x)), "")
    section, last = "", None
    for ln in lines[1:]:
        if ln.isupper() and len(ln) < 30:
            section, last = ln.lower(), None
            continue
        bullet = ln.lstrip("-•* ").strip() if ln[:1] in "-•*" else None
        cells = [c.strip() for c in ln.split("|")]
        if section.startswith("summary"):
            p.professional_summary = ln
        elif section.startswith("experience"):
            if bullet and last is not None:
                last.achievements.append(bullet)
            elif not bullet:
                last = candidate.Experience(role=cells[0], company=cells[1] if len(cells) > 1 else "",
                                            location=cells[2] if len(cells) > 3 else "", duration=cells[-1] if len(cells) > 2 else "")
                p.experiences.append(last)
        elif section.startswith("project"):
            if bullet and last is not None:
                last.achievements.append(bullet)
            elif not bullet:
                last = candidate.Project(title=cells[0], technologies=[t.strip() for t in ",".join(cells[1:]).split(",") if t.strip()])
                p.projects.append(last)
        elif section.startswith("education") and not bullet:
            p.education.append(candidate.Education(degree=cells[0], institution=cells[1] if len(cells) > 1 else "",
                                                   year=cells[2] if len(cells) > 2 else ""))
        elif section.startswith("skill") and ":" in ln:
            cat, items = ln.split(":", 1)
            p.skills.append(candidate.SkillGroup(category=cat.strip(), items=[i.strip() for i in items.split(",") if i.strip()]))
    return p


def _achievements(source: str) -> list[str]:
    """Result lines from the resume's text: verb first, no separators."""
    return [ln.strip() for ln in source.splitlines()
            if re.match(r"^(Cut|Built|Moved|Led|Shipped|Wrote|Handles|Reduced|Grew|Designed|Ran|Migrated|Added)\b", ln.strip())
            and "|" not in ln][:6]


def _section(user: str, name: str, until: str = "\n\n") -> str:
    i = user.find(name)
    if i < 0:
        return ""
    rest = user[i + len(name):]
    j = rest.find(until)
    return rest if j < 0 else rest[:j]


def _line(user: str, label: str) -> str:
    m = re.search(rf"^{re.escape(label)}\s*(.*)$", user, re.M)
    return m.group(1).strip() if m else ""


def answer(schema, system: str, user: str):
    name = schema.__name__
    if name == "Profile":
        return parse_resume(_section(user, "---\n", "\n---"))
    if name == "Requirements":
        posting = user.split("POSTING:", 1)[-1]
        must_part, _, nice_part = posting.partition("Nice to have")
        title = re.search(r"hiring (?:an? )?([A-Z][\w ]*?(?:Engineer|Developer|Scientist|Analyst|Manager|Designer|Intern))", posting)
        fact = next((s.strip() for s in re.split(r"(?<=[.!?])\s+", posting) if s.strip().startswith("We ")), "")
        must = _found(must_part)
        return tailor.Requirements(company="", title=title.group(1) if title else "", must_have=must,
                                   nice_to_have=[s for s in _found(nice_part) if s not in must], keywords=must[:6],
                                   emails=EMAIL.findall(posting), company_fact=fact, closed=False)
    if name == "Pick":
        return tailor.Pick(indices=[0, 1, 2])
    if name == "Tailored":
        resume = json.loads(_section(user, "RESUME:\n", "\n\nGITHUB PROJECTS"))
        want = [w.lower() for w in _line(user, "MUST HAVE:").split(", ") if w]
        first = lambda b: -sum(w in b.lower() for w in want)  # noqa: E731 - what the posting asks for first
        sections = []
        exp = [tailor.Entry(title=x["role"], organisation=x["company"], dates=x["duration"], location=x.get("location") or "",
                            bullets=sorted(x["achievements"], key=first)) for x in resume.get("experiences") or []]
        if exp:
            sections.append(tailor.Section(heading="Experience", entries=exp))
        proj = [tailor.Entry(title=x["title"], tech=x.get("technologies") or [], bullets=sorted(x["achievements"], key=first))
                for x in resume.get("projects") or []]
        if proj:
            sections.append(tailor.Section(heading="Projects", entries=proj))
        edu = [tailor.Entry(title=x["degree"], organisation=x["institution"], dates=x["year"]) for x in resume.get("education") or []]
        if edu:
            sections.append(tailor.Section(heading="Education", entries=edu))
        summary = resume.get("professional_summary") or ""
        return tailor.Tailored(summary=(summary.rstrip(".") + ".") if summary else None, sections=sections)
    if name == "Letter":
        role = _line(user, "ROLE:")
        work = _line(user, "WHAT THE POSTING SAYS ABOUT THE WORK:")
        wins = _achievements(user) or ["Built and ran the systems listed on my resume"]
        filler = ("the work the posting describes is close to what i have been doing, and i would like to talk about "
                  "how i would approach it in the first few weeks.")
        paras = [f"I would like to apply for the {role} role. {work}".strip(),
                 " ".join(w.rstrip(".") + "." for w in wins[:3]) + " " + filler,
                 "I would start by learning how the team works today and where the slow parts are. " + filler + " "
                 + filler + " Could we find a time to talk about the role?"]
        body = "\n\n".join(paras)
        while len(body.split()) < 160:
            body += " " + filler
        return tailor.Letter(body=body)
    if name == "Body":
        wins = _achievements(user)
        if "Job Title:" in user:
            return writer.Body(body=f"Following up on my note about the {_line(user, 'Job Title:')} role. "
                                    f"{(wins or ['I am still keen on it'])[0].rstrip('.')}. Is the role still open?")
        role = _line(user, "ROLE:")
        bullets = (wins + ["Worked with " + ", ".join(_found(user)[:3])])[:3]
        return writer.Body(body=f"I'm applying for the {role} role.\n\n" + "\n".join(f"• {b}" for b in bullets)
                                + "\n\nCould we talk about the role?")
    if name == "People":
        m = re.search(r"PAGE (\S+)\n", user)
        hit = re.search(r"([A-Z][a-z]+ [A-Z][a-z]+), ((?:Head|Talent|Recruit|Engineering|Founder|CTO|People|Hiring)[^,.]*)", user)
        if not (m and hit):
            return research.People(people=[])
        return research.People(people=[research.Person(name=hit.group(1), title=hit.group(2).strip(), url=m.group(1),
                                                       quote=hit.group(0))])
    if name == "RepoOut":
        repo = _line(user, "Repository:")
        return candidate.RepoOut(title=repo.replace("-", " ").title(), description=_line(user, "Description:"),
                                 tech_stack=[x for x in [_line(user, "Primary Language:")] if x])
    if name == "Reading":
        words = user.split("\n\n", 1)[-1]
        first = re.split(r"(?<=[.!?])\s+", words.strip())[0] if words.strip() else ""
        low = words.lower()
        kind = ("offer" if "offer" in low else "rejection" if re.search(r"unfortunately|not (be )?moving forward|other candidates", low)
                else "not_hiring" if "not hiring" in low else "oa" if re.search(r"assessment|hackerrank|take-home|coding test", low)
                else "interview" if re.search(r"interview|talk|call|chat|schedule|meet", low) else "question" if "?" in words else "other")
        q = next((s for s in re.split(r"(?<=[.!?])\s+", words) if s.strip().endswith("?")), "")
        return replies.Reading(kind=kind, confidence=0.9, their_words=first, question=q)
    if name == "Reply":
        if "glad to talk" in system:
            body = "Thank you for writing. I would be glad to talk. Could you share two or three times that suit you?"
        elif "written offer" in system:
            body = "Thank you, I am glad to receive it. Could you send the written offer, and give me a few days to review it?"
        else:
            body = "Thank you for letting me know. Would you keep me in mind, or point me to another team that is hiring?"
        return replies.Reply(body=body)
    raise KeyError(f"the rehearsal model has no answer for {name}")


class Rehearsal:
    """Looks like a LangChain chat model to tooling.structured: with_structured_output(schema).ainvoke(messages)."""

    def __init__(self):
        self.notes: list[str] = []

    def with_structured_output(self, schema):
        class Bound:
            async def ainvoke(self, messages):
                system = next((m["content"] for m in messages if m["role"] == "system"), "")
                return answer(schema, system, messages[-1]["content"])
        return Bound()
