"""Pages the demo serves itself, so it runs offline: two postings, a LinkedIn sign-in wall, a closed job and a team
page. Any other link is fetched for real."""

SAMPLE_RESUME = """Aditya Rao
aditya@rao.dev | +91 98765 43210 | Bengaluru | https://github.com/adityarao

SUMMARY
Backend engineer with 3 years in Python and Go

EXPERIENCE
Software Engineer | Razorpay | Bengaluru | Jul 2021 - Present
- Cut p95 latency 40% on the payouts API
- Built a reconciliation job in Go that runs every night
- Moved 30 services to Kubernetes with Docker and Terraform

PROJECTS
Ledgerline | Go, Postgres
- Built a double-entry ledger service in Go
- Handles 2,000 writes a second in tests

EDUCATION
B.Tech Computer Science | RV College of Engineering | 2021

SKILLS
Languages: Python, Go, SQL
Tools: Postgres, Kubernetes, Docker, Terraform, Kafka
"""

SAMPLE_JOBS = """Blacksmith | Backend Engineer | https://blacksmith.example/careers/backend | hiring@blacksmith.example
Nimbus | Platform Engineer | https://nimbus.example/jobs/platform
Acme | Data Engineer | https://www.linkedin.com/jobs/view/4012345678
Orbit | SRE | https://orbit.example/jobs/sre"""

_BLACKSMITH = ("Blacksmith is hiring a Backend Engineer. We run CI jobs on bare-metal gaming CPUs, so builds finish in "
               "half the time. You will write Go services that schedule jobs across our fleet and keep Postgres fast "
               "under load. Must have: Go, Postgres, Kubernetes. Nice to have: Kafka, Terraform. ") * 6
_NIMBUS = ("Nimbus is hiring a Platform Engineer. We move payments data for 400 banks across three regions. You will "
           "own the deploy pipeline and the Kubernetes clusters it runs on. Must have: Kubernetes, Docker, Python. "
           "Nice to have: Go, Rust. ") * 6
_WALL = ("Sign in to view more jobs. Join now. LinkedIn. User Agreement. Privacy Policy. Cookie Policy. Email or phone. "
         "Password. Forgot password? Sign in. New to LinkedIn? Join now. By clicking Continue to join or sign in, you "
         "agree to LinkedIn's User Agreement. ") * 4
_CLOSED = "This job has expired and is no longer accepting applications. See our other open roles. " * 10
_NIMBUS_TEAM = ("Our team. Priya Nair, Talent Partner, runs hiring for engineering at Nimbus. Sam Lee, Founder, started "
                "Nimbus in 2019. Questions about a role go to priya@nimbus.example. ") * 4

PAGES = {
    "https://blacksmith.example/careers/backend": _BLACKSMITH,
    "https://nimbus.example/jobs/platform": _NIMBUS,
    "https://nimbus.example/team": _NIMBUS_TEAM,
    "https://www.linkedin.com/jobs/view/4012345678": _WALL,
    "https://orbit.example/jobs/sre": _CLOSED,
}

# what "the company" writes back when you simulate a reply
REPLY_TEMPLATES = {
    "interview": "Thanks for writing. Can we talk on Friday about the role? Let me know a time that works.",
    "question": "Thanks. Could you send your CV?",
    "notice": "Thanks for applying. What is your notice period?",
    "rejection": "Unfortunately we will not be moving forward with your application this time.",
    "offer": "We are happy to make you an offer for the role.",
    "opt_out": "Please remove me from your list.",
    "auto_ack": "Thank you for applying. We have received your application.",
}
