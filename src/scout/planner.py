"""The search plan: which roles, which places and which sources to search, in expansion levels, with priorities that
adapt to what each source has yielded so far. Bounded: the plan is a finite list built once; the controller takes
batches from it until the target or a budget is reached. No model is involved.

Related roles are added only when the person's own skills back them: a Python + LangChain profile expands
"AI Engineer" to "LLM Engineer", but a single shared skill never pulls in an unrelated family."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from models import CandidateProfile, SearchPrefs, Strength
from providers.base import Provider, QueryContext
from scoring import title_similarity

Gate = tuple[str, ...]
FAMILIES: list[tuple[str, list[tuple[str, Gate]]]] = [
    (r"\b(ai|generative ai|genai|gen ai|llm|applied ai|nlp|ml|machine learning|deep learning)\b",
     [("AI Engineer", ()), ("Generative AI Engineer", ("llm", "langchain", "rag")), ("LLM Engineer", ("llm", "langchain", "rag")),
      ("Applied AI Engineer", ("llm", "machine learning")), ("Machine Learning Engineer", ("machine learning", "pytorch", "tensorflow", "scikit-learn")),
      ("AI Backend Engineer", ("fastapi", "django", "flask", "rest apis")), ("Python AI Developer", ("python",)),
      ("NLP Engineer", ("nlp", "hugging face"))]),
    (r"\bdata scien", [("Data Scientist", ()), ("Machine Learning Engineer", ("machine learning", "pytorch", "scikit-learn")),
                       ("Applied Scientist", ("machine learning", "statistics")), ("Data Analyst", ("sql", "data analysis"))]),
    (r"\bdata analy|\bbusiness analy|\bbi (analyst|developer)|\banalytics\b|\bmis\b",
     [("Data Analyst", ()), ("Business Analyst", ("excel", "sql")), ("BI Analyst", ("power bi", "tableau", "looker")),
      ("Product Analyst", ("sql", "a/b testing")), ("Analytics Engineer", ("dbt",))]),
    (r"\bdata engineer|\betl\b|big data", [("Data Engineer", ()), ("Analytics Engineer", ("dbt",)),
                                           ("Big Data Engineer", ("spark", "hadoop")), ("ETL Developer", ("etl",))]),
    (r"\b(backend|back-end|back end|python developer|java developer|golang|node(\.js)? developer|api developer)\b",
     [("Backend Engineer", ()), ("Software Engineer", ()), ("Python Developer", ("python",)), ("Java Developer", ("java", "spring")),
      ("Node.js Developer", ("node.js",)), ("Golang Developer", ("golang",))]),
    (r"\b(frontend|front-end|front end|react developer|ui developer|web developer)\b",
     [("Frontend Engineer", ()), ("React Developer", ("react",)), ("UI Developer", ("html", "css")), ("Web Developer", ("javascript",))]),
    (r"full[- ]?stack|mern|mean stack", [("Full Stack Developer", ()), ("Software Engineer", ()),
                                         ("MERN Stack Developer", ("react", "node.js", "nosql"))]),
    (r"\bsoftware (engineer|developer)|\bsde\b|\bswe\b|\bprogrammer\b",
     [("Software Engineer", ()), ("Software Developer", ()), ("Backend Engineer", ("rest apis", "django", "spring", "node.js", "fastapi")),
      ("Full Stack Developer", ("react", "node.js"))]),
    (r"devops|\bsre\b|site reliability|cloud engineer|platform engineer|infrastructure engineer",
     [("DevOps Engineer", ()), ("Site Reliability Engineer", ("kubernetes", "linux")), ("Cloud Engineer", ("aws", "gcp", "azure")),
      ("Platform Engineer", ("kubernetes", "terraform"))]),
    (r"android|\bios\b|mobile|flutter|react native", [("Mobile Developer", ()), ("Android Developer", ("android", "kotlin")),
                                                      ("iOS Developer", ("ios", "swift")), ("Flutter Developer", ("flutter",))]),
    (r"\b(qa|test|tester|sdet|quality)\b", [("QA Engineer", ()), ("SDET", ("testing",)), ("Automation Test Engineer", ("testing",))]),
    (r"product manager|product owner|\bapm\b", [("Product Manager", ()), ("Associate Product Manager", ()), ("Product Owner", ("agile",))]),
    (r"designer|ui/ux|\bux\b", [("Product Designer", ()), ("UI/UX Designer", ()), ("UX Designer", ("ui/ux",))]),
    (r"security|soc analyst|penetration|cyber", [("Security Engineer", ()), ("SOC Analyst", ("cybersecurity",)),
                                                 ("Cybersecurity Analyst", ("cybersecurity",))]),
    (r"marketing|\bseo\b|growth", [("Digital Marketing Executive", ()), ("SEO Specialist", ("seo",)),
                                   ("Growth Marketer", ("digital marketing",)), ("Content Marketing Specialist", ("content marketing",))]),
]
MAX_RELATED = 4


def related_roles(prefs: SearchPrefs, profile: CandidateProfile) -> list[str]:
    have = {s.skill for s in profile.skills if s.strength != Strength.inferred} or set()
    implied = {s.skill for s in profile.skills}
    out: list[tuple[int, str]] = []
    for target in prefs.target_roles:
        for pattern, titles in FAMILIES:
            if not re.search(pattern, target, re.I):
                continue
            for title, gate in titles:
                if any(title_similarity(title, t) >= 0.9 for t in prefs.target_roles):
                    continue
                if gate and not profile.resume_present:
                    continue  # no resume: only the family's own default titles
                hits = len(set(gate) & have) + 0.5 * len(set(gate) & (implied - have))
                if gate and hits < 1:
                    continue
                out.append((-int(hits * 10), title))
            break  # the first family that matches a target is its family
    seen, result = set(), []
    for _, title in sorted(out, key=lambda x: x[0]):
        if title.lower() not in seen:
            seen.add(title.lower())
            result.append(title)
    return result[:MAX_RELATED]


def seniority_variants(prefs: SearchPrefs, profile: CandidateProfile) -> list[str]:
    years = profile.years()
    out = []
    base = prefs.target_roles[0]
    if prefs.internships:
        if not re.search(r"\bintern", base, re.I):
            out.append(f"{base} Intern")
        return out
    if years is not None and years < 2 and not re.search(r"\b(junior|associate|graduate|entry)\b", base, re.I):
        out += [f"Junior {base}", f"Associate {base}", f"{base} fresher"]
    elif years is not None and years >= 5 and not re.search(r"\b(senior|lead|staff|principal)\b", base, re.I):
        out.append(f"Senior {base}")
    return out


def places(prefs: SearchPrefs) -> list[tuple[str, float]]:
    """(where text, weight), most preferred first."""
    country = prefs.country
    if prefs.work_mode == "remote_only":
        return [(f"remote {country}".strip(), 1.0), ("remote", 0.8)]
    out: list[tuple[str, float]] = []
    for i, city in enumerate(prefs.cities[:3]):
        out.append((f"{city}, {country}".strip(", ") if country else city, 1.0 - 0.1 * i))
    if country:
        out.append((country, 0.75 if prefs.cities else 1.0))
    if prefs.work_mode in ("either", "prefer_remote"):
        out.append((f"remote {country}".strip(), 0.95 if prefs.work_mode == "prefer_remote" else 0.6))
    return out or [("", 1.0)]


@dataclass
class PlanItem:
    level: int
    provider: str
    role: str
    where: str
    weight: float
    query: str
    done: bool = False


@dataclass
class Plan:
    items: list[PlanItem]
    related: list[str]
    variants: list[str]
    queries_seen: set[str] = field(default_factory=set)

    def levels(self) -> list[int]:
        return sorted({i.level for i in self.items})

    def remaining(self, level: int | None = None) -> list[PlanItem]:
        return [i for i in self.items if not i.done and (level is None or i.level == level)]


TOP = ("greenhouse", "lever", "ashby", "linkedin", "naukri", "glassdoor", "wellfound", "careers", "internshala")


def build_plan(prefs: SearchPrefs, profile: CandidateProfile, providers: list[Provider]) -> Plan:
    related = related_roles(prefs, profile)
    variants = seniority_variants(prefs, profile)
    usable = [p for p in providers if p.priority > 0 and p.serves(prefs.country_code, prefs.internships)]
    if not prefs.include_likely_active:
        # boards cannot verify a job, so they get fewer searches: they still find leads and duplicates of ATS posts
        for p in usable:
            if p.kind == "board":
                p.priority = round(p.priority * 0.45, 3)
    top = [p for p in usable if p.name in TOP and (p.name != "internshala" or prefs.internships or (profile.years() or 0) < 1)]
    rest = [p for p in usable if p not in top]
    where = places(prefs)
    primary = [(r, 1.0) for r in prefs.target_roles[:2]] + [(v, 0.9) for v in variants]
    rel = [(r, 0.8) for r in related]
    ctx_years, remote_only = profile.years(), prefs.work_mode == "remote_only"

    items: list[PlanItem] = []
    seen: set[str] = set()

    def add(level: int, provs: list[Provider], roles: list[tuple[str, float]], wheres: list[tuple[str, float]]):
        for p in provs:
            for role, rw in roles:
                for w, ww in wheres:
                    q = p.query(QueryContext(role, w, prefs.internships, ctx_years, remote_only, prefs.country_code))
                    key = " ".join(q.lower().split())
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(PlanItem(level, p.name, role, w, round(p.priority * rw * ww, 4), q))

    add(0, top, primary, where[:1])
    add(1, top, rel, where[:1])
    add(1, rest, primary, where[:1])
    add(2, top, primary + rel, where[1:])
    add(3, rest, rel, where[:1])
    add(3, rest, primary, where[1:])
    # levels 4-5: the same roles and places reworded, so a long session keeps finding new postings
    ats_first = [p for p in top + rest if p.kind in ("ats", "careers")]
    for level, suffix in ((4, "hiring now"), (5, "careers openings")):
        reworded = [(f"{r} {suffix}", w * 0.85) for r, w in primary + rel]
        add(level, ats_first, reworded, where)
    # level 6: skill-led searches ("LangChain AI Engineer"), which reach postings titled differently
    skills = [s for s in profile.primary_skills if s not in ("git", "html", "css", "sql", "javascript", "rest apis")][:3]
    if skills:
        base = prefs.target_roles[0]
        add(6, ats_first + [p for p in top if p.kind == "board"], [(f"{s} {base}", 0.75) for s in skills], where[:2])
    return Plan(items=items, related=related, variants=variants, queries_seen=set())


def yield_factor(provider: Provider) -> float:
    """How much a source's observed results raise or lower its next queries."""
    st = provider.stats
    q, postings, qualified = st.get("queries", 0), st.get("postings", 0), st.get("qualified", 0)
    if q >= 2 and postings == 0:
        return 0.3
    return (1.0 + postings / (q + 1) / 5) * (1.0 + 0.5 * min(qualified, 4))


def dead(provider: Provider) -> bool:
    """A source whose first searches returned nothing at all: the search app does not index it; stop spending on it."""
    st = provider.stats
    return st.get("queries", 0) >= 2 and st.get("results", 0) == 0


def next_batch(plan: Plan, level: int, providers: dict[str, Provider], size: int) -> list[PlanItem]:
    for item in plan.remaining(level):
        if dead(providers[item.provider]):
            item.done = True
    todo = plan.remaining(level)
    todo.sort(key=lambda i: (-(i.weight * yield_factor(providers[i.provider])), i.query))
    return todo[:size]
