"""The skill vocabulary: canonical names, the aliases people write, which skills imply others, and which are
transferable to each other. Pure data plus matching on word boundaries, so "go" never matches "going" and
"c" never matches "cat". Used on both sides: the resume and the job posting are read with the same vocabulary."""
from __future__ import annotations

import re
from functools import lru_cache

# canonical -> aliases (regex fragments, matched case-insensitively on word boundaries)
SKILLS: dict[str, tuple[str, ...]] = {
    # languages
    "python": ("python", "python3"), "java": ("java", "java 8", "java 11", "java 17"),
    "javascript": ("javascript", "js", "es6", "ecmascript"), "typescript": ("typescript",),
    "c++": (r"c\+\+", "cpp"), "c#": ("c#", "csharp"), "golang": ("golang",), "rust": ("rust",), "kotlin": ("kotlin",),
    "swift": ("swift",), "ruby": ("ruby", "ruby on rails", "rails"), "php": ("php", "laravel"), "scala": ("scala",),
    "matlab": ("matlab",), "sql": ("sql", "mysql", "postgresql", "postgres", "sqlite", "mssql", "t-sql", "pl/sql", "oracle db"),
    "shell": ("bash", "shell scripting", "powershell", "zsh"),
    # web / backend
    "react": ("react", "reactjs", r"react\.js"), "angular": ("angular", "angularjs"), "vue": ("vue", "vuejs", r"vue\.js"),
    "next.js": (r"next\.js", "nextjs"), "svelte": ("svelte",), "node.js": (r"node\.js", "nodejs", "node"),
    "express": ("express", "expressjs"), "django": ("django",), "flask": ("flask",), "fastapi": ("fastapi",),
    "spring": ("spring", "spring boot", "springboot"), ".net": (r"\.net", "dotnet", r"asp\.net"),
    "html": ("html", "html5"), "css": ("css", "css3", "tailwind", "sass", "scss"),
    "rest apis": ("rest", "restful", "rest api", "rest apis", "api design", "web apis"), "graphql": ("graphql",),
    "grpc": ("grpc",), "microservices": ("microservices", "micro-services", "microservice architecture"),
    "system design": ("system design", "distributed systems", "scalable systems"),
    "data structures": ("data structures", "algorithms", "dsa"),
    "oop": ("oop", "object-oriented", "object oriented"),
    # data stores / data eng
    "nosql": ("nosql", "mongodb", "mongo", "dynamodb", "cassandra", "couchbase"), "redis": ("redis",),
    "elasticsearch": ("elasticsearch", "elastic search", "opensearch"),
    "spark": ("spark", "pyspark", "apache spark"), "hadoop": ("hadoop", "hive", "hdfs"), "kafka": ("kafka", "apache kafka"),
    "airflow": ("airflow", "apache airflow"), "dbt": ("dbt",), "snowflake": ("snowflake",), "bigquery": ("bigquery",),
    "redshift": ("redshift",), "databricks": ("databricks",), "etl": ("etl", "elt", "data pipelines", "data pipeline"),
    # cloud / devops
    "aws": ("aws", "amazon web services", "ec2", "s3", "aws lambda", "sagemaker"), "azure": ("azure",),
    "gcp": ("gcp", "google cloud", "google cloud platform", "vertex ai"), "docker": ("docker", "containers"),
    "kubernetes": ("kubernetes", "k8s", "eks", "gke", "aks"), "terraform": ("terraform",), "linux": ("linux", "unix"),
    "git": ("git", "github", "gitlab", "bitbucket"), "ci/cd": ("ci/cd", "ci cd", "jenkins", "github actions", "gitlab ci", "circleci"),
    "devops": ("devops", "sre", "site reliability"),
    # ml / ai
    "machine learning": ("machine learning", "ml"), "deep learning": ("deep learning", "neural network", "neural networks"),
    "nlp": ("nlp", "natural language processing"), "computer vision": ("computer vision", "opencv", "image processing"),
    "llm": ("llm", "llms", "large language model", "large language models", "generative ai", "genai", "gen ai", "gpt-4", "gpt"),
    "langchain": ("langchain", "lang chain", "langgraph"), "llamaindex": ("llamaindex", "llama index", "llama_index"),
    "rag": ("rag", "retrieval augmented generation", "retrieval-augmented generation"),
    "vector databases": ("vector database", "vector databases", "vector db", "pinecone", "weaviate", "faiss", "chromadb",
                         "milvus", "qdrant", "pgvector"),
    "prompt engineering": ("prompt engineering", "prompt design"),
    "fine-tuning": ("fine-tuning", "fine tuning", "finetuning", "lora", "peft", "rlhf"),
    "hugging face": ("hugging face", "huggingface"), "mlops": ("mlops", "mlflow", "kubeflow", "model deployment"),
    "pytorch": ("pytorch", "torch"), "tensorflow": ("tensorflow", "keras"), "scikit-learn": ("scikit-learn", "sklearn", "scikit learn"),
    "pandas": ("pandas",), "numpy": ("numpy",), "statistics": ("statistics", "statistical", "hypothesis testing"),
    # analytics / BI
    "data analysis": ("data analysis", "data analytics", "analytics skills", "business analytics", "data analysts?"), "excel": (r"excel(?!\s+(?:at|in)\b)", "spreadsheets", "google sheets", "vlookup"),
    "power bi": ("power bi", "powerbi"), "tableau": ("tableau",), "looker": ("looker",),
    "a/b testing": ("a/b testing", "ab testing", "experimentation"),
    # mobile / other engineering
    "android": ("android",), "ios": ("ios",), "react native": ("react native",), "flutter": ("flutter", "dart"),
    "embedded": ("embedded", "firmware", "rtos", "arduino", "raspberry pi"), "verilog": ("verilog", "vhdl", "fpga"),
    "cybersecurity": ("cybersecurity", "cyber security", "penetration testing", "owasp", "siem", "soc analyst"),
    "networking": ("tcp/ip", "ccna", "network engineering", "routing and switching"),
    "blockchain": ("blockchain", "solidity", "web3", "smart contracts"),
    "testing": ("unit testing", "pytest", "jest", "junit", "selenium", "test automation", "cypress", "playwright"),
    "cad": ("autocad", "solidworks", "catia", "cad"),
    # product / design / business
    "agile": ("agile", "scrum", "kanban"), "jira": ("jira",), "figma": ("figma",),
    "ui/ux": ("ui/ux", "ux design", "ui design", "user research", "user experience", "wireframing"),
    "product management": ("product management", "product roadmap", "product strategy"),
    "project management": ("project management", "pmp", "prince2"),
    "seo": ("seo", "search engine optimization"), "digital marketing": ("digital marketing", "sem", "google ads", "performance marketing"),
    "content marketing": ("content marketing", "copywriting", "content writing"),
    "sales": ("sales", "business development", "lead generation"), "crm": ("salesforce", "hubspot", "crm"),
    "financial modeling": ("financial modelling", "financial modeling", "valuation", "dcf"),
    "accounting": ("accounting", "bookkeeping", "tally", "gaap", "ifrs"),
    "recruiting": ("recruiting", "talent acquisition", "sourcing candidates"),
    "customer support": ("customer service", "customer support", "zendesk"),
    "technical writing": ("technical writing", "documentation"),
    "communication": ("communication skills", "stakeholder management", "presentation skills"),
    "leadership": ("team leadership", "led a team", "mentored", "people management"),
}

# short names that collide with English words: matched case-sensitively, and only as a list item
CASE_SENSITIVE = {"c": "C", "r": "R", "go": "Go"}

# skill -> skills it shows, inferred at a lower strength
IMPLIES: dict[str, tuple[str, ...]] = {
    "fastapi": ("python", "rest apis"), "django": ("python",), "flask": ("python", "rest apis"), "pandas": ("python",),
    "numpy": ("python",), "pytorch": ("python", "deep learning"), "tensorflow": ("python", "deep learning"),
    "scikit-learn": ("python", "machine learning"), "langchain": ("python", "llm"), "llamaindex": ("python", "llm"),
    "rag": ("llm",), "fine-tuning": ("llm", "deep learning"), "prompt engineering": ("llm",), "hugging face": ("nlp", "deep learning"),
    "deep learning": ("machine learning",), "nlp": ("machine learning",), "computer vision": ("machine learning",),
    "llm": ("nlp", "prompt engineering"), "react": ("javascript",), "vue": ("javascript",), "angular": ("typescript",), "next.js": ("react", "javascript"),
    "node.js": ("javascript",), "express": ("node.js",), "spring": ("java",), "react native": ("react", "javascript"),
    "kubernetes": ("docker",), "pyspark": ("spark",), "spark": ("etl",), "airflow": ("etl",), "dbt": ("sql", "etl"),
    "snowflake": ("sql",), "bigquery": ("sql",), "redshift": ("sql",), "mlops": ("machine learning",),
}

# skills a recruiter would accept as transferable for each other (same job, another tool)
GROUPS: tuple[frozenset[str], ...] = tuple(frozenset(g) for g in (
    {"django", "flask", "fastapi"}, {"react", "vue", "angular", "svelte", "next.js"}, {"aws", "gcp", "azure"},
    {"pytorch", "tensorflow"}, {"tableau", "power bi", "looker"}, {"java", "kotlin", "scala"}, {"java", "c#"},
    {"android", "ios", "flutter", "react native"}, {"snowflake", "bigquery", "redshift", "databricks"},
    {"spark", "hadoop", "databricks"}, {"langchain", "llamaindex"}, {"rag", "vector databases"},
    {"javascript", "typescript"}, {"nosql", "sql"}, {"golang", "rust", "c++"}, {"ci/cd", "devops"},
    {"jira", "agile"}, {"seo", "digital marketing", "content marketing"}, {"node.js", "fastapi", "django", "spring", ".net"},
))

DOMAINS: dict[str, tuple[str, ...]] = {
    "fintech": ("fintech", "payments", "digital banking", "lending", "upi"), "banking": ("banking", "bank"),
    "insurance": ("insurance", "insurtech"), "healthcare": ("healthcare", "healthtech", "medical", "clinical", "hospital"),
    "e-commerce": ("e-commerce", "ecommerce", "marketplace", "retail"), "edtech": ("edtech", "education technology", "e-learning"),
    "saas": ("saas", "b2b software"), "gaming": ("gaming", "game development"), "logistics": ("logistics", "supply chain"),
    "telecom": ("telecom", "telecommunications"), "automotive": ("automotive", "autonomous vehicles"),
    "adtech": ("adtech", "advertising technology", "martech"), "cybersecurity": ("cybersecurity", "infosec"),
    "travel": ("travel", "hospitality"), "media": ("media", "streaming", "publishing"), "energy": ("energy", "renewables"),
    "manufacturing": ("manufacturing", "industrial"), "pharma": ("pharma", "pharmaceutical", "biotech", "life sciences"),
    "legal": ("legaltech", "legal tech"), "hr tech": ("hr tech", "hrtech"), "real estate": ("real estate", "proptech"),
}


@lru_cache(maxsize=1)
def _patterns() -> list[tuple[str, re.Pattern]]:
    out = []
    for canon, aliases in SKILLS.items():
        alt = "|".join(sorted(aliases, key=len, reverse=True))
        out.append((canon, re.compile(rf"(?<![\w+#/])(?:{alt})(?![\w+#]|\.\w)", re.I)))
    return out


_LIST_CTX = r"(?:(?<=^)|(?<=[,:;/|(•·\-–]\s)|(?<=[,:;/|(•·]))"


@lru_cache(maxsize=1)
def _case_patterns() -> list[tuple[str, re.Pattern]]:
    # "C", "R" or "Go" only as an item of a list ("Python, R, SQL" / "C/C++" / "Languages: Go")
    return [(canon, re.compile(rf"{_LIST_CTX}\s*{word}(?=\s*(?:[,;/|)]|$|\s+and\b|\s+programming|\s+language))", re.M))
            for canon, word in (("c", "C"), ("r", "R"), ("golang", "Go"))]


# general abilities, not technical requirements: never counted as a "missing" required skill
SOFT = frozenset({"communication", "leadership", "agile", "jira", "product management", "project management",
                  "technical writing", "data structures", "oop", "system design", "sales", "customer support"})


def find(text: str) -> dict[str, int]:
    """Canonical skill -> number of mentions in the text."""
    counts: dict[str, int] = {}
    if not text:
        return counts
    for canon, pat in _patterns():
        n = len(pat.findall(text))
        if n:
            counts[canon] = counts.get(canon, 0) + n
    for canon, pat in _case_patterns():
        n = len(pat.findall(text))
        if n:
            counts[canon] = counts.get(canon, 0) + n
    # "node" alone is too common in prose; keep node.js only when written as a tech name or beside JS words
    if "node.js" in counts and not re.search(r"node\.?js|\bnode\b.{0,40}(express|javascript|npm|backend)", text, re.I):
        del counts["node.js"]
    checks = {
        "rest apis": r"\brestful\b|\brest\s*(api|apis|services|endpoints)\b|api design|web apis",
        "spring": r"spring\s*(boot|framework|mvc|cloud|security|data)|springboot|java.{0,60}\bspring\b|\bspring\b.{0,60}java",
        "express": r"express\.?js|\bexpress\b.{0,40}(node|javascript|api)|(node|javascript).{0,40}\bexpress\b",
    }
    for canon, needed in checks.items():
        if canon in counts and not re.search(needed, text, re.I):
            del counts[canon]
    return counts


def first_line_with(text: str, skill: str) -> str | None:
    if skill in CASE_SENSITIVE or skill not in SKILLS:
        return None
    pat = dict(_patterns())[skill]
    for line in (text or "").splitlines():
        if pat.search(line):
            return " ".join(line.split())[:200]
    return None


def implied(skills: set[str]) -> set[str]:
    out: set[str] = set()
    frontier = set(skills)
    for _ in range(3):  # implications are shallow; three hops is plenty and bounds the loop
        new = {i for s in frontier for i in IMPLIES.get(s, ())} - skills - out
        if not new:
            break
        out |= new
        frontier = new
    return out


def transferable(skill: str, have: set[str]) -> str | None:
    """A skill the person has that a recruiter would accept in place of `skill`, if any."""
    for group in GROUPS:
        if skill in group:
            hit = sorted((group - {skill}) & have)
            if hit:
                return hit[0]
    return None


def find_domains(text: str) -> list[str]:
    low = (text or "").lower()
    return sorted(d for d, words in DOMAINS.items()
                  if any(re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", low) for w in words))
