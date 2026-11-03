"""Stage C: requirement-level semantic alignment. Not resume-vs-posting as two big strings: each of the posting's
requirement and responsibility lines is compared with the person's own evidence lines (experience and project
bullets), and a requirement scores by its best-supported evidence line.

Embeddings come through GRAVITY_LLM_URL (batched, cached per session). If that fails, or for tests, a lexical
similarity (cosine over word and bigram counts, with skill-aware tokens) is used and the result says so."""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable

import skills as sk

Embed = Callable[[list[str]], Awaitable[list[list[float]]]]

_STOP = set("a an and or the of to in for with on at by from as is are be will you your our we this that it its "
            "able ability strong good excellent experience experienced work working knowledge using use plus "
            "years year etc including such other new team".split())


def _tokens(text: str) -> list[str]:
    low = text.lower()
    words = [w for w in re.findall(r"[a-z0-9+#.]+", low) if w not in _STOP and len(w) > 1]
    canon = [f"skill:{s}" for s in sk.find(text)]  # "PyTorch" and "torch" meet as one token
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])] + canon * 2


def _cos(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    return dot / (math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values())))


def _vcos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Semantic:
    def __init__(self, evidence: list[str], embed: Embed | None = None, batch: int = 64):
        self.evidence = [e for e in evidence if e.strip()][:60]
        self.embed, self.batch = embed, batch
        self.mode = "embeddings" if embed else "lexical"
        self._vecs: dict[str, list[float]] = {}
        self._bags = [Counter(_tokens(e)) for e in self.evidence]
        self.calls = 0
        self.failed: str | None = None

    async def _ensure(self, texts: list[str]) -> bool:
        need = [t for t in dict.fromkeys(texts) if t not in self._vecs]
        if not need:
            return True
        if self.embed is None:
            return False
        try:
            for i in range(0, len(need), self.batch):
                chunk = need[i:i + self.batch]
                vecs = await self.embed(chunk)
                self.calls += 1
                if len(vecs) != len(chunk):
                    raise ValueError("embedding count mismatch")
                self._vecs.update(zip(chunk, vecs))
            return True
        except Exception as exc:  # noqa: BLE001 - fall back to lexical for the rest of the session
            self.embed, self.mode, self.failed = None, "lexical", str(exc)[:160]
            return False

    async def warm(self) -> None:
        if self.embed and self.evidence:
            await self._ensure(self.evidence)

    async def align(self, requirement_lines: list[str]) -> tuple[float, list[tuple[str, str, float]]]:
        """(0..1 alignment, [(requirement, best evidence line, similarity)]). 0.5 when either side is empty."""
        reqs = [r for r in requirement_lines if 12 <= len(r) <= 400
                and (sk.find(r) or len(r.split()) >= 6)][:25]   # skip one-word and boilerplate-ish lines
        if not reqs or not self.evidence:
            return 0.5, []
        pairs = []
        if self.embed and await self._ensure(self.evidence + reqs):
            for r in reqs:
                sims = [(_vcos(self._vecs[r], self._vecs[e]), e) for e in self.evidence]
                s, e = max(sims)
                pairs.append((r, e, s))
            lo, hi = 0.25, 0.75   # typical cosine range of unrelated vs closely related sentences
        else:
            for r in reqs:
                bag = Counter(_tokens(r))
                sims = [(_cos(bag, eb), e) for eb, e in zip(self._bags, self.evidence)]
                s, e = max(sims)
                pairs.append((r, e, s))
            lo, hi = 0.05, 0.45
        scaled = sorted((min(1.0, max(0.0, (s - lo) / (hi - lo))) for _, _, s in pairs), reverse=True)
        # a posting mixes core requirements with generic lines; alignment is how well its best-supported
        # two-thirds is covered, so one line about "a fast-paced environment" cannot sink a strong match
        kept = scaled[:max(1, round(len(scaled) * 2 / 3))]
        return sum(kept) / len(kept), pairs
