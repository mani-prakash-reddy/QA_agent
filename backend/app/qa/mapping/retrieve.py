"""Hybrid retrieval of candidate screens for a requirement: lexical coverage + embedding cosine,
combined by reciprocal-rank fusion (robust to the two scores living on different scales)."""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.qa.mapping.cards import ScreenCard

STOP = frozenset(
    "a an and are as at be been by can do does for from has have if in into is it its may must "
    "not of on or shall should so such than that the their then there these they this to via "
    "was we when where which while will with within without would user users system able "
    "application app page screen".split()
)
RRF_K = 10


def tokens(text: str) -> list[str]:
    out = []
    for raw in re.findall(r"[a-z0-9]+", text.lower()):
        if raw in STOP or len(raw) < 2:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if raw.endswith(suffix) and len(raw) - len(suffix) >= 4:
                raw = raw[: -len(suffix)]
                break
        out.append(raw)
    return out


@dataclass
class Candidate:
    card: ScreenCard
    lexical: float
    cosine: float | None
    score: float  # fused, 0..1


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Retriever:
    def __init__(
        self,
        cards: list[ScreenCard],
        vectors: Mapping[str, Sequence[float]] | None = None,
    ) -> None:
        self.cards = cards
        self.vectors = vectors or {}
        self._tokens = [set(tokens(c.text)) for c in cards]
        df: dict[str, int] = {}
        for ts in self._tokens:
            for t in ts:
                df[t] = df.get(t, 0) + 1
        n = max(len(cards), 1)
        self._idf = {t: math.log(1 + n / c) for t, c in df.items()}

    def lexical(self, query: str) -> list[float]:
        q = set(tokens(query))
        known = {t for t in q if t in self._idf}
        total = sum(self._idf[t] for t in known) or 1.0
        return [sum(self._idf[t] for t in known & ts) / total for ts in self._tokens]

    def search(
        self, query: str, query_vec: Sequence[float] | None, k: int = 6
    ) -> list[Candidate]:
        if not self.cards:
            return []
        lex = self.lexical(query)
        cos: list[float | None] = [
            _cosine(query_vec, self.vectors[c.group_key])
            if query_vec is not None and c.group_key in self.vectors
            else None
            for c in self.cards
        ]
        n = len(self.cards)
        lex_rank = {i: r for r, i in enumerate(sorted(range(n), key=lambda i: -lex[i]))}
        have_cos = [i for i in range(n) if cos[i] is not None]
        cos_rank = {
            i: r for r, i in enumerate(sorted(have_cos, key=lambda i: -(cos[i] or 0.0)))
        }
        fused: list[float] = []
        for i in range(n):
            s = (1 / (RRF_K + lex_rank[i])) if lex[i] > 0 else 0.0
            if i in cos_rank:
                s += 1 / (RRF_K + cos_rank[i])
            fused.append(s)
        best_possible = (1 / RRF_K) * (2 if have_cos else 1)
        order = sorted(range(n), key=lambda i: -fused[i])
        # A screen with no lexical overlap and a poor semantic rank is not a candidate at all.
        picked = [i for i in order if fused[i] > 0][:k]
        return [
            Candidate(self.cards[i], lex[i], cos[i], round(fused[i] / best_possible, 4))
            for i in picked
        ]
