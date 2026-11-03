"""Resume extraction and profile normalisation: sections, skills with evidence strength, years from dates (overlaps
once, internships apart), conflicts, PII redaction, malformed files."""

import pytest

from conftest import TODAY, run
from fakes import resume
from models import Strength
from profile import build_profile, enrich, grounded, _Quoted
from resume import load_resume
from security import redact


def profile_of(text, today=TODAY):
    r = run(load_resume(text))
    return build_profile(r["text"], resume_hash=r["hash"], today=today), r


def test_ai_engineer_resume_is_read_into_evidence():
    p, r = profile_of(resume())
    assert "[email]" in r["text"] and "[phone]" in r["text"] and "[profile link]" in r["text"]
    assert "priya.sharma@example.com" not in r["text"]
    s = p.skill_map()
    assert s["fastapi"].strength == Strength.demonstrated          # used at a job
    assert s["kubernetes"].strength == Strength.project            # only in a project: not proof of proficiency
    assert s["react"].strength == Strength.listed                  # only in the skills list
    assert s["javascript"].strength == Strength.inferred           # implied by React, never claimed
    assert "kubernetes" not in p.primary_skills and "python" in p.primary_skills
    assert p.titles[:2] == ["AI Engineer", "Software Engineer"]
    assert p.highest_degree == "bachelor" and p.certifications == ["AWS Certified Cloud Practitioner"]
    assert p.location.value == "Pune, India" and p.location.provenance == "explicit"
    assert any("Applied AI Engineer" in r for r in p.preferred_roles)


def test_years_come_from_dates_and_a_stale_claim_is_a_conflict():
    p, _ = profile_of(resume())
    # Jan 2024 - Nov 2026 (35 months) + Jul 2022 - Dec 2023 (18 months); the internship is not counted
    assert p.years() == pytest.approx(4.4, abs=0.05)
    assert p.years_experience.provenance == "conflict" and p.internship_months == 6
    assert p.seniority.value == "mid"


def test_overlapping_jobs_count_once():
    text = ("EXPERIENCE\nData Engineer at A\nJan 2020 - Dec 2021\n- Built Spark pipelines\n"
            "Consultant at B\nJan 2021 - Dec 2022\n- SQL dashboards\n" + "filler line about work\n" * 10)
    p, _ = profile_of(text)
    assert p.years() == 3.0


def test_fresher_gets_zero_years_inferred_not_missing():
    text = ("Final year student\nEDUCATION\nB.Tech Computer Science, 2023 - 2027\nPROJECTS\n- Built a Flask app with SQL\n"
            "SKILLS\nPython, SQL, Excel\n" + "Coursework in data structures and algorithms\n" * 6)
    p, _ = profile_of(text)
    assert p.years() == 0.0 and p.years_experience.provenance == "inferred" and p.seniority.value == "entry"
    assert p.skill_map()["flask"].strength == Strength.project


def test_missing_years_stay_missing_and_nothing_is_guessed():
    text = "SKILLS\nPython, SQL\nEXPERIENCE\nAnalyst at a bank, building reports in Excel and SQL every week.\n" * 4
    p, _ = profile_of(text)
    assert p.years() is None and p.years_experience.provenance == "missing"
    dumped = p.model_dump()
    for never in ("notice_period", "salary", "relocation", "work_mode"):
        assert never not in dumped


def test_short_or_garbled_files_are_reported_not_crashed(tmp_path):
    bad = tmp_path / "cv.pdf"
    bad.write_bytes(b"%PDF-1.4 not really a pdf")
    r = run(load_resume(None, str(bad)))
    assert r["text"] == "" and "could not be read" in r["note"]
    tiny = run(load_resume("Python dev"))
    assert tiny["text"] == "" and "scanned" in tiny["note"]


def test_docx_resume_is_read(tmp_path):
    import docx
    d = docx.Document()
    for line in resume().splitlines():
        d.add_paragraph(line)
    path = tmp_path / "cv.docx"
    d.save(path)
    r = run(load_resume(None, str(path)))
    assert r["source"] == "file docx" and "LangChain" in r["text"]


def test_an_uploaded_link_is_read_through_the_pages_source_only():
    seen = []

    async def read_page(url):
        seen.append(url)
        return {"url": url, "text": resume()}

    r = run(load_resume(None, "https://files.gravity.example/signed/cv.pdf?sig=abc", read_page))
    assert seen and r["source"] == "uploaded file" and r["text"]
    refused = run(load_resume(None, "http://169.254.169.254/latest/meta-data", read_page))
    assert len(seen) == 1 and refused["source"] == "none"   # never handed to a reader


def test_redaction_keeps_dates():
    assert redact("Worked 2019 - 2022, call +1 (415) 555-0100") == "Worked 2019 - 2022, call [phone]"


def test_model_items_must_quote_the_resume():
    text = "AI Engineer at Nimbus Analytics\nB.Tech in Computer Science"
    items = [_Quoted(value="AI Engineer", quote="AI Engineer at Nimbus Analytics"),
             _Quoted(value="Staff Engineer", quote="Staff Engineer at Google")]       # invented: dropped
    assert grounded(items, " ".join(text.lower().split())) == ["AI Engineer"]


def test_enrich_adds_only_grounded_titles():
    from fakes import FakeModel
    p, r = profile_of(resume())
    model = FakeModel({"_Extract": {"job_titles": [{"value": "Lead Architect", "quote": "Lead Architect at Initech"}],
                                    "target_roles": [{"value": "Applied AI Engineer", "quote": "Seeking a role as an Applied AI Engineer"}]}})
    out, called = run(enrich(p, r["text"], model))
    assert called and "Lead Architect" not in out.titles and "Applied AI Engineer" in out.preferred_roles
