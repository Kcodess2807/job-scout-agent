"""Quality evaluation of the matcher on the labelled set (tests/eval_data.py): Precision@K, Recall@K, NDCG@K,
hard-constraint violation rate, duplicate rate and a score-vs-label calibration table. Bars are set for the first
release's priorities: precision and zero hard-constraint violations. `python tests/test_eval.py` prints a report."""
from __future__ import annotations

import math
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1] / "src")]

import eligibility
import ranking
from clarify import decide
from config import DEFAULT
from conftest import TODAY, run
from dedupe import dedupe_all
from eval_data import CANDIDATES, POSTINGS
from models import Listing
from normalize import with_page
from prefs import read_inputs
from profile import build_profile
from resume import load_resume
from planner import related_roles
from scoring import score
from semantic import Semantic
from verify import verify
from transport import PageResult
from providers.catalog import build

K = 3
RELEVANT = 2


def listings() -> list[Listing]:
    out = []
    for pid, title, company, _city, _country, _mode, text, _ in POSTINGS:
        url = f"https://boards.greenhouse.io/{company.lower()}/jobs/{int(pid[1:]) + 9000000}"
        base = Listing(key=f"greenhouse:{pid}", title=title, company=company, source="greenhouse", source_kind="ats",
                       authority=1.0, source_url=url, canonical_url=url, application_url=url)
        lst = with_page(base, {"title": title, "text": text}, None, TODAY)
        page = PageResult("ok", {"url": url, "title": title, "text": text}, read_at=1.79e9)
        out.append(lst.model_copy(update={"verification": verify(lst, page, build()[0], DEFAULT.policy, TODAY)}))
    return out


def evaluate_candidate(name: str):
    c = CANDIDATES[name]
    r = run(load_resume(c["resume"]))
    profile = build_profile(r["text"], resume_hash=r["hash"], today=TODAY)
    prefs, profile, _ = decide(read_inputs(c["inputs"]), profile, ask=None)
    sem = Semantic(profile.evidence_lines)
    related = related_roles(prefs, profile)           # as the engine does
    scored = []
    for lst in listings():
        rej, con = eligibility.check(lst, profile, prefs, DEFAULT.policy)
        sv, _ = run(sem.align(lst.requirements.required_lines + lst.requirements.responsibility_lines))
        m = score(lst, profile, prefs, DEFAULT, related=related, semantic=sv, rejections=rej, concerns=con)
        scored.append((lst, m))
    ranked = [lm for lm in ranking.rank(scored, TODAY) if lm[1].eligible]
    returned = [lm for lm in ranked if lm[1].score >= DEFAULT.policy.min_score]
    labels = {pid: lab[name] for pid, *_, lab in POSTINGS}
    return ranked, returned, labels


def ndcg(gains: list[int], ideal: list[int], k: int) -> float:
    dcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(gains[:k]))
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(sorted(ideal, reverse=True)[:k]))
    return dcg / idcg if idcg else 1.0


def metrics() -> dict:
    per = {}
    calib: dict[str, list[int]] = {}
    for name in CANDIDATES:
        ranked, returned, labels = evaluate_candidate(name)
        pid = lambda lst: lst.key.split(":")[1]  # noqa: E731
        top = [labels[pid(lst)][0] for lst, _ in ranked[:K]]
        relevant_total = sum(1 for lab, hv in labels.values() if lab >= RELEVANT and not hv)
        violations = sum(1 for lst, _ in returned if labels[pid(lst)][1])
        per[name] = {
            "precision@k": sum(g >= RELEVANT for g in top) / K,
            "recall@k": (sum(g >= RELEVANT for g in top) / min(K, relevant_total)) if relevant_total else 1.0,
            "ndcg@k": ndcg(top, [lab for lab, _ in labels.values()], K),
            "returned": len(returned),
            "returned_precision": (sum(labels[pid(lst)][0] >= RELEVANT for lst, _ in returned) / len(returned)) if returned else 1.0,
            "hard_violations_returned": violations,
        }
        for lst, m in ranked:
            calib.setdefault(f"{(m.score // 10) * 10}s", []).append(labels[pid(lst)][0])
    n = len(per)
    agg = {k: round(sum(p[k] for p in per.values()) / n, 3) for k in ("precision@k", "recall@k", "ndcg@k", "returned_precision")}
    agg["hard_violation_rate"] = sum(p["hard_violations_returned"] for p in per.values()) / max(1, sum(p["returned"] for p in per.values()))
    kept, dups = dedupe_all(listings())
    agg["duplicate_rate"] = 1 - len(kept) / len(listings())
    agg["verified_active_rate"] = sum(x.verification.status == "verified_active" for x in kept) / len(kept)
    agg["calibration_mean_label_by_score_band"] = {b: round(sum(v) / len(v), 2) for b, v in sorted(calib.items())}
    return {"per_candidate": per, "aggregate": agg}


def test_matching_quality_bars():
    m = metrics()["aggregate"]
    assert m["hard_violation_rate"] == 0            # never return a job that breaks a confirmed constraint
    assert m["precision@k"] >= 0.66
    assert m["returned_precision"] >= 0.9           # what passes 85 is relevant
    assert m["ndcg@k"] >= 0.8
    assert m["duplicate_rate"] == 0                 # distinct postings are not merged


def test_scores_rise_with_labels():
    bands = metrics()["aggregate"]["calibration_mean_label_by_score_band"]
    values = [bands[b] for b in sorted(bands, key=lambda b: int(b[:-1])) if b != "0s"]
    assert values[-1] >= 2.5 and values[0] <= 1.0


if __name__ == "__main__":
    import json
    print(json.dumps(metrics(), indent=2))
