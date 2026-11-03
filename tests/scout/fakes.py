"""A fake world for deterministic tests: a web search, a page reader and a model. The postings are synthetic
fixtures (made-up companies), written in the shapes the real sources return (web_result and web_page records)."""
from __future__ import annotations

import json
import re
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"


def resume(name: str = "resume_ai_engineer.txt") -> str:
    return (FIX / name).read_text()


ABOUT = ("\n\n## About the company\nWe are a product company that helps teams make better decisions every day. Our "
         "people care about craft, kindness and shipping things customers love. We value curiosity, ownership and "
         "clear writing, and we invest in learning budgets, mentoring and a calm pace of work. Everyone owns problems "
         "end to end and works closely with customers and designers to understand what matters most to them.\n\n"
         "## Equal opportunity\nWe welcome applicants of every background and do not discriminate on any basis.\n")


def _page(title: str, body: str) -> dict:
    return {"title": title, "text": body + ABOUT}


GH_APPLIED = "https://boards.greenhouse.io/nimbusai/jobs/4012345"
LEVER_LLM = "https://jobs.lever.co/quantumleaf/3f1c2a9e-1b2c-4d5e-8f90-112233445566"
LI_APPLIED = "https://www.linkedin.com/jobs/view/applied-ai-engineer-at-nimbusai-3998877665?trk=public_jobs"
ASHBY_CLOSED = "https://jobs.ashbyhq.com/brightlayer/9a8b7c6d-1111-2222-3333-444455556666"
GH_STAFF = "https://boards.greenhouse.io/deepforge/jobs/5011111"
GH_BERLIN = "https://boards.greenhouse.io/eurolabs/jobs/5022222"
NAUKRI = "https://www.naukri.com/job-listings-python-ai-developer-cognisoft-pune-2-to-5-years-251020500123"
GH_PHD = "https://boards.greenhouse.io/visionary/jobs/5033333"
CAREERS = "https://careers.zentrix.ai/jobs/ai-backend-engineer-2231"
LEVER_ANALYST = "https://jobs.lever.co/quantumleaf/aa1c2a9e-1b2c-4d5e-8f90-112233445599"
GH_BOARD = "https://boards.greenhouse.io/orbitworks"
GH_BOARD_JOB = "https://boards.greenhouse.io/orbitworks/jobs/5044444"

APPLIED_TEXT = """# Applied AI Engineer
**Location:** Pune, India (Hybrid)
Full-time

## About NimbusAI
We build AI copilots for finance teams. Benefits include remote Fridays.

## What you'll do
- Build retrieval-augmented generation services with LangChain and FastAPI
- Ship LLM features to production on AWS

## Requirements
- 2+ years of professional experience with Python
- Experience with LangChain, RAG and vector databases
- Experience building REST APIs with FastAPI
- Familiarity with Docker and AWS

## Nice to have
- Kubernetes
- Experience with fine-tuning

Apply for this job
"""

LLM_TEXT = """LLM Engineer
Remote - India
Full time

What you will do
- Prototype and productionise LLM applications in Python

Requirements:
- 1-3 years of experience building with LLMs in Python
- Vector databases (pgvector, Pinecone) and RAG
- Docker; CI/CD with GitHub Actions

Nice to have:
- LlamaIndex

APPLY FOR THIS JOB
"""

CLOSED_TEXT = """Generative AI Engineer
Pune, India
Requirements
- Python, LangChain, LLM
This job is no longer accepting applications
"""

STAFF_TEXT = """Senior Staff ML Engineer
Bengaluru, India
Requirements:
- 10+ years of experience in machine learning
- Python, PyTorch, distributed systems
Apply for this job
"""

BERLIN_TEXT = """AI Engineer
Location: Berlin, Germany (On-site)
Requirements:
- 2+ years of experience with Python and LLMs
- LangChain, RAG
Apply for this job
"""

NAUKRI_TEXT = """Python AI Developer - Cognisoft - Pune
Posted 3 days ago
Job description
Location: Pune
Key Skills: Python, LangChain, FastAPI, LLM, RAG, Docker, AWS
Experience: 2 - 5 years
Apply
I'm interested
"""

PHD_TEXT = """AI Engineer (Computer Vision)
Location: Pune, India (Hybrid)
Requirements:
- PhD required in computer vision or a related field
- 2+ years of experience with Python and PyTorch
- Computer vision, OpenCV, C++
Apply for this job
"""

CAREERS_TEXT = """AI Backend Engineer
Location: Pune, India
Workplace type: Hybrid
Employment type: Full-time

Responsibilities
- Design Python services that serve LLM features

Requirements
- 2+ years of experience with Python and FastAPI
- PostgreSQL and REST APIs
- Experience with LLM APIs, LangChain or RAG
- Docker, AWS

Preferred
- Kubernetes

Apply now
"""

BOARD_TEXT = """# OrbitWorks jobs
[AI Engineer, LLM Platform](https://boards.greenhouse.io/orbitworks/jobs/5044444)
[Office Manager](https://boards.greenhouse.io/orbitworks/jobs/5044445)
"""

BOARD_JOB_TEXT = """AI Engineer, LLM Platform
Location: Pune, India - Hybrid
Requirements
- 2+ years of experience with Python
- LangChain, RAG, FastAPI, AWS, Docker
Apply for this job
"""

RESULTS = [
    # (keywords that make a query return it, record)
    (("greenhouse",), {"title": "Job Application for Applied AI Engineer at NimbusAI", "url": GH_APPLIED,
                       "snippet": "Pune, India (Hybrid). Build RAG services with LangChain and FastAPI.", "published": "2026-10-20"}),
    (("lever",), {"title": "QuantumLeaf - LLM Engineer", "url": LEVER_LLM, "snippet": "Remote - India. Build LLM apps in Python."}),
    (("linkedin",), {"title": "NimbusAI hiring Applied AI Engineer in Pune, Maharashtra, India | LinkedIn", "url": LI_APPLIED,
                     "snippet": "Build RAG services with LangChain and FastAPI. Hybrid."}),
    (("ashby",), {"title": "Generative AI Engineer @ BrightLayer", "url": ASHBY_CLOSED, "snippet": "Pune, India"}),
    (("greenhouse",), {"title": "Job Application for Senior Staff ML Engineer at DeepForge", "url": GH_STAFF,
                       "snippet": "Bengaluru, India. 10+ years."}),
    (("greenhouse",), {"title": "Job Application for AI Engineer at EuroLabs", "url": GH_BERLIN, "snippet": "Berlin, Germany"}),
    (("naukri",), {"title": "Python AI Developer - Cognisoft - Pune - Naukri.com", "url": NAUKRI,
                   "snippet": "2-5 years. Python, LangChain, FastAPI. Pune"}),
    (("greenhouse",), {"title": "Job Application for AI Engineer (Computer Vision) at Visionary", "url": GH_PHD,
                       "snippet": "Pune, India. Computer vision, PyTorch."}),
    (("careers",), {"title": "AI Backend Engineer | Zentrix Careers", "url": CAREERS, "snippet": "Pune, India. Hybrid. FastAPI, LLM."}),
    (("careers",), {"title": "How to become an AI engineer in 2026", "url": "https://medium.com/@someone/ai-engineer-guide",
                    "snippet": "A blog post"}),
    (("linkedin",), {"title": "AI Engineer jobs in Pune | LinkedIn", "url": "https://www.linkedin.com/jobs/search/?keywords=ai%20engineer",
                     "snippet": "1,000+ jobs"}),
    (("lever",), {"title": "QuantumLeaf - Data Analyst", "url": LEVER_ANALYST, "snippet": "Pune. SQL and dashboards."}),
    (("greenhouse",), {"title": "Jobs at OrbitWorks", "url": GH_BOARD, "snippet": "Open roles at OrbitWorks"}),
]

PAGES = {
    GH_APPLIED: _page("Job Application for Applied AI Engineer at NimbusAI", APPLIED_TEXT),
    LEVER_LLM: _page("QuantumLeaf - LLM Engineer", LLM_TEXT),
    LI_APPLIED.split("?")[0]: _page("NimbusAI hiring Applied AI Engineer in Pune | LinkedIn", APPLIED_TEXT.replace("Apply for this job", "Easy Apply") + "\nPosted 2 days ago"),
    ASHBY_CLOSED: _page("Generative AI Engineer", CLOSED_TEXT),
    GH_STAFF: _page("Senior Staff ML Engineer", STAFF_TEXT),
    GH_BERLIN: _page("AI Engineer", BERLIN_TEXT),
    NAUKRI: _page("Python AI Developer - Cognisoft", NAUKRI_TEXT),
    GH_PHD: _page("AI Engineer (Computer Vision)", PHD_TEXT),
    CAREERS: _page("AI Backend Engineer", CAREERS_TEXT),
    LEVER_ANALYST: _page("Data Analyst", "Data Analyst\nRequirements: SQL, Tableau\nApply for this job\n" + "x " * 200),
    GH_BOARD: _page("Jobs at OrbitWorks", BOARD_TEXT + "Open roles " * 40),
    GH_BOARD_JOB: _page("AI Engineer, LLM Platform", BOARD_JOB_TEXT),
}


class FakeWeb:
    """`read(source, **args)` as gravity.read: the `jobs` and `pages` sources over the fixtures."""

    def __init__(self, results=RESULTS, pages=PAGES, *, fail_search: str | None = None, fail_pages: str | None = None,
                 fail_after: int | None = None, on_call=None):
        self.results, self.pages = results, pages
        self.fail_search, self.fail_pages, self.fail_after = fail_search, fail_pages, fail_after
        self.calls: list[tuple[str, dict]] = []
        self.on_call = on_call

    async def __call__(self, source: str, **args):
        self.calls.append((source, args))
        if self.on_call:
            self.on_call(self)
        if source == "jobs":
            if self.fail_search and (self.fail_after is None or len(self.calls) > self.fail_after):
                raise RuntimeError(self.fail_search)
            q = args["text"].lower()
            recs = [dict(r) for keys, r in self.results if any(k in q for k in keys)]
            return {"records": recs[:10], "complete": True, "warnings": []}
        if source == "pages":
            if self.fail_pages:
                raise RuntimeError(self.fail_pages)
            url = args["args"]["url"]
            base = url.split("?")[0].rstrip("/")
            page = self.pages.get(url) or self.pages.get(base)
            if page is None:
                raise RuntimeError("TOOL_FAILED: 404 not found")
            return {"records": [{"url": url, **page}], "complete": True, "warnings": []}
        raise RuntimeError(f"this run has no sources.{source}")

    def count(self, source: str) -> int:
        return sum(1 for s, _ in self.calls if s == source)


class _Structured:
    def __init__(self, schema, answer):
        self.schema, self.answer = schema, answer

    async def ainvoke(self, messages):
        out = self.answer(self.schema, messages) if callable(self.answer) else self.answer
        if isinstance(out, Exception):
            raise out
        return self.schema.model_validate(out) if isinstance(out, dict) else out


class FakeModel:
    """with_structured_output(Schema).ainvoke(messages) -> answers[Schema.__name__] (a dict, a callable or an error)."""

    def __init__(self, answers: dict | None = None):
        self.answers = answers or {}
        self.calls: list[str] = []

    def with_structured_output(self, schema):
        self.calls.append(schema.__name__)
        answer = self.answers.get(schema.__name__, {})
        return _Structured(schema, answer)


def payload_ids(messages) -> list[str]:
    text = messages[-1]["content"]
    return re.findall(r'"id": "([^"]+)"', text)


def jsonl(path: str) -> list[dict]:
    return [json.loads(line) for line in (FIX / path).read_text().splitlines() if line.strip()]
