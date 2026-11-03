"""A small hand-labelled matching set: 3 candidates x 16 synthetic postings, each pair labelled
0 (irrelevant) .. 3 (excellent), plus whether it breaks a hard constraint of that candidate.

This is a regression harness for the matcher, NOT a representative benchmark: the postings are synthetic and the
labels are one person's judgement. Calibrating the 85 threshold needs real postings labelled by real candidates."""

from fakes import resume

ANALYST = """Rohan Iyer
Data Analyst
Bengaluru, India
EXPERIENCE
Data Analyst at Brightcart
Aug 2025 - Present
- Built Power BI dashboards on SQL (PostgreSQL) for the e-commerce sales team
- Automated weekly Excel reports and ran A/B testing readouts
PROJECTS
- Python and pandas analysis of public transport data
SKILLS
SQL, Excel, Power BI, Python, pandas, statistics
EDUCATION
B.Sc Statistics, Christ University, 2021 - 2024
""" + "Volunteer tutor for school students in mathematics and statistics.\n" * 3

FRONTEND = """Meera Nair
Senior Frontend Engineer
Kochi, India
EXPERIENCE
Senior Frontend Engineer at Pixelwave
Mar 2021 - Present
- Led the React and TypeScript rewrite of a SaaS dashboard; Next.js for the marketing site
- Built a design system with CSS modules and Figma handoff
Frontend Developer at Webcraft
Jun 2019 - Feb 2021
- Built React and JavaScript single-page apps with REST APIs
SKILLS
React, TypeScript, JavaScript, Next.js, HTML, CSS, Jest, Git
EDUCATION
B.Tech Information Technology, 2015 - 2019
""" + "Speaker at local JavaScript meetups on accessibility.\n" * 3

CANDIDATES = {
    "ai": {"resume": resume(), "inputs": {"role": "AI Engineer", "country": "India", "city": "Pune"}},
    "analyst": {"resume": ANALYST, "inputs": {"role": "Data Analyst", "country": "India", "city": "Bengaluru"}},
    "frontend": {"resume": FRONTEND, "inputs": {"role": "Frontend Engineer", "country": "India", "remote": "remote"}},
}

ABOUT = ("\n## About us\nWe build software that teams rely on every day and we care about craft, ownership and kindness. "
         "We offer learning budgets, mentoring and health cover, and we hire people from every background.\n") * 2


def jd(title, where, mode, required, preferred="", resp="Ship features with the team and own them in production."):
    return (f"{title}\nLocation: {where}\nWorkplace type: {mode}\nEmployment type: Full-time\n\n"
            f"## What you'll do\n- {resp}\n\n## Requirements\n" + "".join(f"- {r}\n" for r in required) +
            (f"\n## Nice to have\n- {preferred}\n" if preferred else "") + "\nApply for this job\n" + ABOUT)


# id, title, company, location, country, mode, text, {candidate: (label, hard_violation)}
POSTINGS = [
    ("p01", "Applied AI Engineer", "NimbusAI", "Pune", "IN", "hybrid",
     jd("Applied AI Engineer", "Pune, India", "Hybrid", ["2+ years of experience with Python", "LangChain, RAG and vector databases",
                                                         "REST APIs with FastAPI", "Docker and AWS"], "Kubernetes"),
     {"ai": (3, False), "analyst": (0, False), "frontend": (0, True)}),
    ("p02", "LLM Engineer", "QuantumLeaf", "India", "IN", "remote",
     jd("LLM Engineer", "Remote - India", "Remote", ["1-3 years of experience building with LLMs in Python",
                                                     "Vector databases and RAG", "Docker"], "LlamaIndex"),
     {"ai": (3, False), "analyst": (0, False), "frontend": (0, False)}),
    ("p03", "AI Engineer (Computer Vision)", "Visionary", "Pune", "IN", "hybrid",
     jd("AI Engineer (Computer Vision)", "Pune, India", "Hybrid", ["PhD required in computer vision",
                                                                   "2+ years of experience with PyTorch", "OpenCV and C++"]),
     {"ai": (1, True), "analyst": (0, True), "frontend": (0, True)}),
    ("p04", "Senior Staff ML Engineer", "DeepForge", "Bengaluru", "IN", "onsite",
     jd("Senior Staff ML Engineer", "Bengaluru, India", "On-site", ["10+ years of experience in machine learning",
                                                                    "PyTorch and distributed systems"]),
     {"ai": (0, True), "analyst": (0, True), "frontend": (0, True)}),
    ("p05", "AI Engineer", "EuroLabs", "Berlin", "DE", "onsite",
     jd("AI Engineer", "Berlin, Germany", "On-site", ["2+ years of experience with Python and LLMs", "LangChain and RAG"]),
     {"ai": (0, True), "analyst": (0, True), "frontend": (0, True)}),
    ("p06", "Python Backend Engineer", "Ledgerly", "Pune", "IN", "hybrid",
     jd("Python Backend Engineer", "Pune, India", "Hybrid", ["2+ years of experience with Python",
                                                             "Django or FastAPI", "PostgreSQL and REST APIs"], "AWS"),
     {"ai": (2, False), "analyst": (0, False), "frontend": (0, True)}),
    ("p07", "Data Analyst", "Brightmart", "Bengaluru", "IN", "hybrid",
     jd("Data Analyst", "Bengaluru, India", "Hybrid", ["0-2 years of experience in data analysis", "SQL and Excel",
                                                       "Power BI or Tableau dashboards"], "Python"),
     {"ai": (0, False), "analyst": (3, False), "frontend": (0, True)}),
    ("p08", "Senior Data Analyst", "Finlytics", "Bengaluru", "IN", "hybrid",
     jd("Senior Data Analyst", "Bengaluru, India", "Hybrid", ["5+ years of experience in analytics", "SQL, Tableau"]),
     {"ai": (0, False), "analyst": (1, True), "frontend": (0, True)}),
    ("p09", "Business Analyst", "Retailo", "Bengaluru", "IN", "onsite",
     jd("Business Analyst", "Bengaluru, India", "On-site", ["1+ years of experience as an analyst", "Excel and SQL",
                                                            "Stakeholder management"]),
     {"ai": (0, False), "analyst": (2, False), "frontend": (0, True)}),
    ("p10", "Data Scientist", "Healthwise", "Pune", "IN", "hybrid",
     jd("Data Scientist", "Pune, India", "Hybrid", ["3+ years of experience in machine learning", "Python, PyTorch, scikit-learn",
                                                    "Statistics"]),
     {"ai": (1, False), "analyst": (0, True), "frontend": (0, True)}),
    ("p11", "Frontend Engineer", "Cloudnest", "India", "IN", "remote",
     jd("Frontend Engineer", "Remote - India", "Remote", ["4+ years of experience with React and TypeScript",
                                                          "HTML and CSS", "Testing with Jest"], "Next.js"),
     {"ai": (0, False), "analyst": (0, False), "frontend": (3, False)}),
    ("p12", "Senior React Developer", "Shopwise", "India", "IN", "remote",
     jd("Senior React Developer", "Remote - India", "Remote", ["5+ years of experience with React", "Next.js and TypeScript",
                                                               "REST APIs"]),
     {"ai": (0, True), "analyst": (0, True), "frontend": (3, False)}),
    ("p13", "Frontend Engineer", "Metroapps", "Pune", "IN", "onsite",
     jd("Frontend Engineer", "Pune, India", "On-site", ["3+ years of experience with React", "JavaScript, HTML, CSS"]),
     {"ai": (0, False), "analyst": (0, False), "frontend": (1, True)}),
    ("p14", "Full Stack Developer", "Tasklane", "India", "IN", "remote",
     jd("Full Stack Developer", "Remote - India", "Remote", ["3+ years of experience with React and Node.js",
                                                             "TypeScript", "PostgreSQL"]),
     {"ai": (1, False), "analyst": (0, False), "frontend": (2, False)}),
    ("p15", "Product Analyst", "Streamly", "Bengaluru", "IN", "hybrid",
     jd("Product Analyst", "Bengaluru, India", "Hybrid", ["1+ years of experience with SQL", "A/B testing and statistics"],
        "Python"),
     {"ai": (0, False), "analyst": (2, False), "frontend": (0, True)}),
    ("p16", "UI Designer", "Artboard", "India", "IN", "remote",
     jd("UI Designer", "Remote - India", "Remote", ["3+ years of experience in UI design", "Figma", "Design systems"]),
     {"ai": (0, False), "analyst": (0, False), "frontend": (1, False)}),
]
