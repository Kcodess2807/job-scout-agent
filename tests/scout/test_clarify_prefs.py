"""Minimal questioning: extract first, ask second, one batched question at most, disclosed defaults, corrections."""
import pytest

from clarify import decide, parse_answer
from conftest import run
from fakes import resume
from models import CandidateProfile
from prefs import corrections, read_inputs
from profile import build_profile
from resume import load_resume

from conftest import TODAY


def full_profile():
    r = run(load_resume(resume()))
    return build_profile(r["text"], resume_hash=r["hash"], today=TODAY)


class Asker:
    def __init__(self, answer):
        self.answer, self.questions = answer, []

    def __call__(self, question, options=None, default=None):
        self.questions.append(question)
        return self.answer


def test_complete_inputs_ask_nothing():
    ask = Asker("unused")
    prefs, profile, info = decide(read_inputs({"role": "AI Engineer", "country": "India", "city": "Pune",
                                               "remote": "either", "count": 10}), full_profile(), ask=ask)
    assert ask.questions == [] and info["asked"] is None
    assert prefs.target_roles == ["AI Engineer"] and prefs.cities == ["Pune"] and prefs.work_mode == "either"


def test_role_and_city_are_taken_from_the_resume_without_asking():
    ask = Asker("unused")
    prefs, _, _ = decide(read_inputs({"country": "India"}), full_profile(), ask=ask)
    assert ask.questions == []
    assert prefs.role_provenance == "inferred" and "Applied AI Engineer" in prefs.target_roles[0]
    assert prefs.cities == ["Pune"] and any("resume gives it" in a for a in prefs.assumptions)


def test_no_resume_no_role_asks_once_for_both():
    ask = Asker("data analyst, 2 years")
    prefs, profile, info = decide(read_inputs({"country": "India"}), CandidateProfile(), ask=ask)
    assert len(ask.questions) == 1 and "role" in ask.questions[0] and "years" in ask.questions[0]
    assert prefs.target_roles == ["data analyst"] and profile.years() == 2.0
    assert profile.years_experience.provenance == "user"


def test_no_role_anywhere_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        decide(read_inputs({"country": "India"}), CandidateProfile(), ask=Asker(""))


def test_user_years_win_over_resume_and_the_conflict_is_said():
    prefs, profile, _ = decide(read_inputs({"role": "AI Engineer", "country": "India", "experience_years": "2"}),
                               full_profile(), ask=None)
    assert profile.years() == 2.0 and any("your number was used" in n for n in profile.notes)


def test_hard_and_soft_work_modes():
    assert read_inputs({"remote": "remote"})["work_mode"] == "remote_only"
    assert read_inputs({"remote": "prefer remote"})["work_mode"] == "prefer_remote"
    assert read_inputs({"remote": "on-site or hybrid"})["work_mode"] == "no_remote"
    assert read_inputs({})["work_mode"] is None


def test_cities_keep_preference_order_and_count_is_clamped_with_a_note():
    raw = read_inputs({"city": "Pune preferred, open to Bangalore", "count": 99})
    raw2 = read_inputs({"city": "Pune, Bangalore"})
    assert raw2["cities"] == ["Pune", "Bengaluru"] and raw["cities"] == ["Pune", "Bengaluru"]
    assert raw["count"] == 25 and "25" in raw["count_note"]


def test_follow_up_corrections():
    c = corrections("Actually I have 3 years, remote only please, also Bangalore. Find me 5")
    assert c["years"] == 3.0 and c["work_mode"] == "remote_only" and c["add_cities"] == ["Bengaluru"] and c["count"] == 5
    assert corrections("look for data engineer roles")["roles"] == ["Data Engineer"]
    assert corrections("thanks!") == {}


def test_parse_answer_variants():
    assert parse_answer("fresher", ["years"]) == {"years": 0.0}
    assert parse_answer("4", ["years"]) == {"years": 4.0}
    assert parse_answer("Backend engineer", ["role"]) == {"roles": ["Backend engineer"]}


def test_years_typed_as_text_are_read():
    assert read_inputs({"experience_years": "2"})["years"] == 2.0
    assert read_inputs({"experience_years": "1.5 years"})["years"] == 1.5
    assert read_inputs({"experience_years": "6 months"})["years"] == 0.5
    assert read_inputs({"experience_years": ""})["years"] is None
