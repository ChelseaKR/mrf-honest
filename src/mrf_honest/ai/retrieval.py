"""Small lexical ranking used to choose which cited passages to show a model."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from mrf_honest.ai.corpus import Passage

_TOKEN = re.compile(r"[a-z0-9§]+(?:\.[0-9]+)*")
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to "
    "was were will with shall may any such not no other than under upon which who".split()
)
_K1 = 1.5
_B = 0.75


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.casefold()) if t not in _STOPWORDS]


@dataclass(frozen=True)
class Ranked:
    passage: Passage
    score: float


def rank(query: str, passages: list[Passage], limit: int) -> list[Ranked]:
    """BM25 over ``passages``; the top ``limit`` with a positive score."""
    terms = set(tokenize(query))
    if limit <= 0 or not passages or not terms:
        return []
    counts = [Counter(tokenize(p.text)) for p in passages]
    lengths = [sum(c.values()) for c in counts]
    average = max(sum(lengths) / len(lengths), 1.0)
    frequency: Counter[str] = Counter()
    for c in counts:
        frequency.update(c.keys())
    total = len(passages)
    scored: list[Ranked] = []
    for passage, c, length in zip(passages, counts, lengths, strict=True):
        score = 0.0
        for term in terms:
            tf = c.get(term)
            if not tf:
                continue
            idf = math.log(1 + (total - frequency[term] + 0.5) / (frequency[term] + 0.5))
            score += idf * tf * (_K1 + 1) / (tf + _K1 * (1 - _B + _B * length / average))
        if score > 0:
            scored.append(Ranked(passage, score))
    scored.sort(key=lambda r: (-r.score, r.passage.passage_id))
    return scored[:limit]
