"""The registered discovery targets. ATS boards come first: they are the employer's own listing, so a live page
there is the strongest evidence a job is open. Boards re-post (and often hide the employer's apply link), so they
rank lower and their postings are at best "likely active". Aggregators are leads only: never read, never counted.

Hosts and URL shapes were taken from public posting URLs; none is called directly (contract v2)."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from providers.base import Provider, QueryContext, query_param, slug_company

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
INDIA = frozenset({"IN"})


def _first_segment(url: str) -> str | None:
    seg = [s for s in urlsplit(url).path.split("/") if s]
    return seg[0] if seg else None


def _subdomain(url: str) -> str | None:
    host = urlsplit(url).netloc.lower().split(":")[0]
    label = host.split(".")[0]
    return None if label in ("www", "jobs", "careers", "apply", "boards", "job-boards") else label


class Greenhouse(Provider):
    def posting_id(self, url):
        return query_param(url, "token") or query_param(url, "gh_jid") or super().posting_id(url)

    def is_posting(self, url):
        return super().is_posting(url) or bool(query_param(url, "token") and "embed" in url)

    def company(self, url, title):
        seg = query_param(url, "for") or _first_segment(url)
        return slug_company(seg) if seg and seg != "embed" else None


class Lever(Provider):
    def company(self, url, title):
        return slug_company(_first_segment(url))

    def application_url(self, url):
        return self.canonical(url).removesuffix("/apply") + "/apply"


class Ashby(Provider):
    def company(self, url, title):
        return slug_company(_first_segment(url).replace("%20", "-") if _first_segment(url) else None)

    def application_url(self, url):
        return self.canonical(url).removesuffix("/application") + "/application"


class Workable(Provider):
    def company(self, url, title):
        return slug_company(_first_segment(url))

    def application_url(self, url):
        return self.canonical(url).rstrip("/").removesuffix("/apply") + "/apply/"


class SubdomainATS(Provider):
    """Workday, Recruitee, BambooHR, Personio: the company is the first label of the host."""
    def company(self, url, title):
        return slug_company(_subdomain(url))


class PathATS(Provider):
    """ATS hosts that carry the company in the path (jobs.jobvite.com/<co>/..., ats.rippling.com/<co>/...)."""
    skip_segments: tuple[str, ...] = ("apply", "en", "en-us", "careers", "jobs")

    def company(self, url, title):
        seg = [s for s in urlsplit(url).path.split("/") if s and s.lower() not in self.skip_segments]
        return slug_company(seg[0]) if seg else None


class ICIMS(SubdomainATS):
    def company(self, url, title):
        label = urlsplit(url).netloc.lower().split(".")[0]
        return slug_company(label.removeprefix("careers-").removeprefix("jobs-"))


class SmartRecruiters(Provider):
    def company(self, url, title):
        return slug_company(_first_segment(url))


_LINKEDIN_TITLE = re.compile(r"^(?P<company>.+?) hiring (?P<title>.+?)(?: in (?P<loc>.+?))?\s*\|\s*LinkedIn", re.I)


_LINKEDIN_AT_TITLE = re.compile(r"^.+ at (?P<company>[^|—]+?)(?:\s+—\s+[^—|]+?)?\s*\|\s*LinkedIn", re.I)
_LINKEDIN_SLUG = re.compile(r"^/jobs/view/(?P<slug>[\w%-]+?)-\d{8,}/?$")


class LinkedIn(Provider):
    """Company from the page title (both of LinkedIn's shapes), else from the URL slug
    (/jobs/view/software-engineer-at-truscalar-ai-4454193413 -> "Truscalar Ai"), which only fills a gap: the slug
    loses capitals and punctuation, and a title holding " at " splits ambiguously."""
    def company(self, url, title):
        m = _LINKEDIN_TITLE.match(title or "") or _LINKEDIN_AT_TITLE.match(title or "")
        if m:
            return m.group("company").strip()
        s = _LINKEDIN_SLUG.match(urlsplit(url).path)
        if s and s.group("slug").count("-at-") == 1:
            return slug_company(s.group("slug").split("-at-")[1])
        return None


class Board(Provider):
    pass


class Naukri(Board):
    """Naukri postings name an experience band ("2-5 Yrs"); the person's years narrow the search."""
    def query(self, ctx: QueryContext) -> str:
        years = f" {int(ctx.years)} years" if ctx.years is not None and not ctx.internships else ""
        return f"{super().query(ctx)}{years}"


class Glassdoor(Board):
    """Glassdoor runs one site per country (glassdoor.co.in for India); the search aims at the person's own."""
    SITES = {"IN": "glassdoor.co.in", "GB": "glassdoor.co.uk", "CA": "glassdoor.ca", "AU": "glassdoor.com.au",
             "DE": "glassdoor.de", "FR": "glassdoor.fr", "IE": "glassdoor.ie", "SG": "glassdoor.sg"}

    def query(self, ctx: QueryContext) -> str:
        kind = "internship" if ctx.internships else "jobs"
        host = self.SITES.get(ctx.country_code or "", "glassdoor.com")
        return f"site:{host}/job-listing {ctx.role} {kind} {ctx.where}".strip()


class Careers(Provider):
    """Any employer's own careers site: the fallback for hosts no other provider claims."""
    _PATH = re.compile(r"/(careers?|jobs?|positions?|openings?|vacanc(?:y|ies)|opportunit(?:y|ies)|join(?:-us)?|"
                       r"requisitions?|roles?|job-details?|jobdetail|posting)s?(/|$)", re.I)
    _ID = re.compile(rf"(\d{{4,}}|{UUID}|[a-z0-9]+(?:-[a-z0-9]+){{3,}})/?$", re.I)
    _LIST = re.compile(r"/(search|results|category|categories|departments?|teams?|locations?)(/|$)|jobs-in-|"
                       r"-jobs/?$|/jobs/?$|/careers/?$", re.I)
    _IDQ = ("gh_jid", "jobId", "jobid", "job_id", "jid", "requisitionId", "pid")

    def matches(self, host):
        return True

    def is_posting(self, url):
        p = urlsplit(url)
        if any(query_param(url, q) for q in self._IDQ):
            return True
        return bool(self._PATH.search(p.path) and self._ID.search(p.path) and not self._LIST.search(p.path))

    def posting_id(self, url):
        return None

    def key(self, url):
        gh = query_param(url, "gh_jid")
        return f"greenhouse:{gh}" if gh and gh.isdigit() else None  # the same id as the Greenhouse board's posting

    def company(self, url, title):
        """careers.acme.com / acme.com/careers / acme.co.in/jobs -> 'Acme' (the domain's own name)."""
        host = urlsplit(url).netloc.lower().removeprefix("www.").split(":")[0]
        parts = host.split(".")
        if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac"):
            parts = parts[:-1]
        return parts[-2].capitalize() if len(parts) >= 2 else None

    def query(self, ctx: QueryContext) -> str:
        kind = "internship" if ctx.internships else "job opening"
        return f"{ctx.role} {kind} {ctx.where} careers apply"


AGGREGATOR_HOSTS = ("jooble.org", "talent.com", "simplyhired.com", "ziprecruiter.com", "monster.com", "foundit.sg",
                    "shine.com", "timesjobs.com", "careerjet.com", "careerjet.co.in", "adzuna.com", "adzuna.in",
                    "jobrapido.com", "jora.com", "whatjobs.com", "bebee.com", "jobsora.com", "jobted.com", "learn4good.com",
                    "jobisjob.com", "neuvoo.com", "recruit.net", "jobleads.com", "workindia.in", "apna.co", "freshersworld.com",
                    "jobfound.org", "interndoor.com", "myinternships.in", "offcampusjobs4u.com",
                    "jobsnet.in", "freshersnow.com", "freshersvoice.com", "fresherslive.com", "meterwork.com",
                    "talents.vaia.com", "hiredoor.in", "naukri.org", "indeedjobs.com", "jobsforher.com",
                    "linkedin-jobs.com", "jobgether.com", "remoterocketship.com", "builtin.in", "wellfound.org")
NOT_JOBS_HOSTS = ("youtube.com", "reddit.com", "medium.com", "quora.com", "wikipedia.org", "facebook.com", "instagram.com",
                  "x.com", "twitter.com", "github.com", "stackoverflow.com", "geeksforgeeks.org", "coursera.org",
                  "udemy.com", "ambitionbox.com", "levels.fyi", "payscale.com", "builtin.com", "simplilearn.com")


def build() -> list[Provider]:
    """A fresh list per session (providers keep per-session stats)."""
    ats = [
        Greenhouse("greenhouse", "Greenhouse", "ats", 1.0,
                   ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"),
                   posting=re.compile(r"^/[\w.-]+/jobs/(?P<id>\d{5,})"), listing=re.compile(r"^/[\w.-]+/?$"),
                   priority=1.0, site="job-boards.greenhouse.io",
                   closed_words=("the job you are looking for is no longer open", "this job is no longer open")),
        Lever("lever", "Lever", "ats", 1.0, ("jobs.lever.co", "jobs.eu.lever.co"),
              posting=re.compile(rf"^/[\w.%-]+/(?P<id>{UUID})", re.I), listing=re.compile(r"^/[\w.%-]+/?$"),
              priority=1.0, site="jobs.lever.co",
              closed_words=("the job posting you're looking for might have closed", "sorry, we couldn't find anything here")),
        Ashby("ashby", "Ashby", "ats", 1.0, ("jobs.ashbyhq.com",),
              posting=re.compile(rf"^/[\w.%-]+/(?P<id>{UUID})", re.I), listing=re.compile(r"^/[\w.%-]+/?$"),
              priority=0.95, site="jobs.ashbyhq.com", closed_words=("this job is no longer accepting applications",)),
        Workable("workable", "Workable", "ats", 0.95, ("apply.workable.com",),
                 posting=re.compile(r"^/[\w-]+/j/(?P<id>[0-9A-F]{6,})", re.I), listing=re.compile(r"^/[\w-]+/?$"),
                 priority=0.8, site="apply.workable.com"),
        SmartRecruiters("smartrecruiters", "SmartRecruiters", "ats", 0.95, ("jobs.smartrecruiters.com",),
                        posting=re.compile(r"^/[\w-]+/(?P<id>\d{6,})"), listing=re.compile(r"^/[\w-]+/?$"),
                        priority=0.75, site="jobs.smartrecruiters.com"),
        SubdomainATS("workday", "Workday", "ats", 0.95, ("myworkdayjobs.com",),
                     posting=re.compile(r"/job/.+_(?P<id>[A-Za-z]*[-_]?\d{3,}[\w-]*)/?$"), priority=0.75,
                     site="myworkdayjobs.com"),
        SubdomainATS("recruitee", "Recruitee", "ats", 0.9, ("recruitee.com",),
                     posting=re.compile(r"^/o/(?P<id>[\w-]{4,})"), priority=0.5, site="recruitee.com"),
        SubdomainATS("bamboohr", "BambooHR", "ats", 0.9, ("bamboohr.com",),
                     posting=re.compile(r"^/(?:careers|jobs)/(?:view\.php\?id=)?(?P<id>\d{1,7})"), priority=0.4,
                     site="bamboohr.com/careers"),
        SubdomainATS("personio", "Personio", "ats", 0.9, ("jobs.personio.de", "jobs.personio.com"),
                     posting=re.compile(r"^/job/(?P<id>\d{4,})"), priority=0.4, site="jobs.personio.com"),
        # widely used in India
        SubdomainATS("keka", "Keka", "ats", 0.9, ("keka.com",), posting=re.compile(r"^/careers/jobdetails/(?P<id>\d{2,})"),
                     priority=0.7, site="keka.com/careers/jobdetails"),
        SubdomainATS("darwinbox", "Darwinbox", "ats", 0.9, ("darwinbox.in", "darwinbox.com"),
                     posting=re.compile(r"/careers/(?:jobdetails?/|jobDetails/)?(?P<id>[a-f0-9]{10,})", re.I),
                     priority=0.6, site="darwinbox.in"),
        SubdomainATS("zohorecruit", "Zoho Recruit", "ats", 0.9, ("zohorecruit.com", "zohorecruit.in"),
                     posting=re.compile(r"^/jobs/Careers/(?P<id>\d{6,})", re.I), priority=0.6, site="zohorecruit.in/jobs/Careers"),
        SubdomainATS("freshteam", "Freshteam", "ats", 0.9, ("freshteam.com",),
                     posting=re.compile(r"^/jobs/(?P<id>[\w-]{6,})/"), priority=0.5, site="freshteam.com/jobs"),
        # used worldwide
        SubdomainATS("breezy", "Breezy HR", "ats", 0.9, ("breezy.hr",), posting=re.compile(r"^/p/(?P<id>[a-f0-9]{8,})"),
                     priority=0.5, site="breezy.hr/p"),
        SubdomainATS("jazzhr", "JazzHR", "ats", 0.9, ("applytojob.com",), posting=re.compile(r"^/apply/(?P<id>\w{6,})"),
                     priority=0.4, site="applytojob.com/apply"),
        SubdomainATS("teamtailor", "Teamtailor", "ats", 0.9, ("teamtailor.com",),
                     posting=re.compile(r"^/jobs/(?P<id>\d{4,})"), priority=0.5, site="teamtailor.com/jobs"),
        ICIMS("icims", "iCIMS", "ats", 0.9, ("icims.com",), posting=re.compile(r"^/jobs/(?P<id>\d{3,})/"),
              priority=0.5, site="icims.com/jobs"),
        PathATS("jobvite", "Jobvite", "ats", 0.9, ("jobs.jobvite.com",), posting=re.compile(r"^/[\w-]+/job/(?P<id>\w{6,})"),
                priority=0.5, site="jobs.jobvite.com"),
        PathATS("rippling", "Rippling", "ats", 0.9, ("ats.rippling.com",),
                posting=re.compile(rf"^/(?:[\w-]+/)?[\w-]+/jobs/(?P<id>{UUID})", re.I), priority=0.5, site="ats.rippling.com"),
        PathATS("dover", "Dover", "ats", 0.9, ("app.dover.com",), posting=re.compile(rf"^/apply/[\w-]+/(?P<id>{UUID})", re.I),
                priority=0.4, site="app.dover.com/apply"),
    ]
    boards = [
        LinkedIn("linkedin", "LinkedIn", "board", 0.6, ("linkedin.com",),
                 posting=re.compile(r"^/jobs/view/(?:[\w%-]*?-)?(?P<id>\d{8,})"), priority=0.9, site="linkedin.com/jobs/view",
                 closed_words=("no longer accepting applications",), fresh_days=21),
        Naukri("naukri", "Naukri", "board", 0.6, ("naukri.com",), countries=INDIA,
              posting=re.compile(r"^/job-listings-[\w-]*?-(?P<id>\d{9,})"), priority=0.85, site="naukri.com/job-listings",
              closed_words=("this job is no longer available", "job has expired"), fresh_days=21),
        Board("wellfound", "Wellfound", "board", 0.65, ("wellfound.com", "angel.co"),
              posting=re.compile(r"/jobs/(?P<id>\d{5,})"), priority=0.75, site="wellfound.com/jobs"),
        Board("indeed", "Indeed", "board", 0.5, ("indeed.com", "indeed.co.in", "indeed.co.uk"),
              posting=re.compile(r"/(?:viewjob|rc/clk|m/basecamp/viewjob|pagead/clk)\?(?:.*&)?jk=(?P<id>[0-9a-f]{16})"),
              fetchable=False, priority=0.6, site="indeed.com/viewjob", fresh_days=21),
        Board("foundit", "Foundit", "board", 0.5, ("foundit.in",), countries=INDIA,
              posting=re.compile(r"^/job/[\w-]+?-(?P<id>\d{6,})"), priority=0.55, site="foundit.in/job"),
        Board("instahyre", "Instahyre", "board", 0.5, ("instahyre.com",), countries=INDIA,
              posting=re.compile(r"^/job-(?P<id>\d{4,})-"), priority=0.5, site="instahyre.com"),
        Board("cutshort", "Cutshort", "board", 0.5, ("cutshort.io",), countries=INDIA,
              posting=re.compile(r"^/job/[\w-]+?-(?P<id>[A-Za-z0-9]{6,})$"), priority=0.45, site="cutshort.io/job"),
        Board("hirist", "Hirist", "board", 0.5, ("hirist.tech", "hirist.com"), countries=INDIA,
              posting=re.compile(r"^/j/[\w-]+?-(?P<id>\d{6,})"), priority=0.4, site="hirist.tech/j"),
        Board("internshala", "Internshala", "board", 0.6, ("internshala.com",), countries=INDIA,
              posting=re.compile(r"^/(?:internship|job)/detail/[\w-]+?(?P<id>\d{6,})/?$"), priority=0.8,
              site="internshala.com", internships_only=False),
        Board("workatastartup", "Y Combinator jobs", "board", 0.65, ("workatastartup.com", "ycombinator.com"),
              posting=re.compile(r"(?:^/jobs/|/companies/[\w-]+/jobs/)(?P<id>\d{3,})"), priority=0.6,
              site="ycombinator.com/companies"),
        Board("unstop", "Unstop", "board", 0.5, ("unstop.com",), countries=INDIA,
              posting=re.compile(r"^/(?:jobs|internships|o)/[\w-]+?-(?P<id>\d{5,})"), priority=0.45, site="unstop.com/jobs"),
        Board("iimjobs", "IIMJobs", "board", 0.5, ("iimjobs.com",), countries=INDIA,
              posting=re.compile(r"^/j/[\w-]+?-(?P<id>\d{5,})\.html"), priority=0.4, site="iimjobs.com/j"),
        Glassdoor("glassdoor", "Glassdoor", "board", 0.5,
                  ("glassdoor.com", "glassdoor.co.in", "glassdoor.co.uk", "glassdoor.ca", "glassdoor.com.au",
                   "glassdoor.de", "glassdoor.fr", "glassdoor.ie", "glassdoor.sg"),
                  posting=re.compile(r"/job-listing/.*?(?:jl=|JV_[\w]*?KO\d+,\d+_KE\d+,\d+\.htm\?jl=)(?P<id>\d{6,})|"
                                     r"/partner/jobListing\.htm\?(?:.*&)?(?:jobListingId|jl)=(?P<pid>\d{6,})"),
                  fetchable=False, priority=0.7, site="glassdoor.com/job-listing"),
    ]
    aggregator = Board("aggregator", "Job aggregators", "aggregator", 0.25, AGGREGATOR_HOSTS,
                       posting=re.compile(r"."), fetchable=False, priority=0.0)
    careers = Careers("careers", "Company careers pages", "careers", 0.8, (), priority=0.7)
    return ats + boards + [aggregator, careers]


def classify(url: str, providers: list[Provider]) -> Provider | None:
    """The provider a URL belongs to, or None for pages that are not job sources at all."""
    host = urlsplit(url).netloc.lower().split(":")[0].removeprefix("www.")
    if any(host == h or host.endswith("." + h) for h in NOT_JOBS_HOSTS):
        return None
    for p in providers:
        if p.name != "careers" and p.matches(host):
            return p
    return next((p for p in providers if p.name == "careers"), None)
