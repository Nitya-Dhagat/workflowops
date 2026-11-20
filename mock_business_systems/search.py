"""Tiny dependency-free BM25 index for the knowledge base."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

TOKEN = re.compile(r"[a-z0-9]+")
STOP = {"the", "a", "an", "of", "to", "and", "or", "is", "be", "for", "in", "on", "must", "are"}


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP]


@dataclass
class BM25:
    k1: float = 1.5
    b: float = 0.75
    docs: list[tuple[str, Counter[str], int]] = field(default_factory=list)
    df: Counter[str] = field(default_factory=Counter)

    def add(self, doc_id: str, text: str) -> None:
        toks = tokenize(text)
        tf = Counter(toks)
        self.docs.append((doc_id, tf, len(toks)))
        self.df.update(tf.keys())

    def search(self, query: str, k: int = 5) -> list[tuple[str, float]]:
        if not self.docs:
            return []
        n = len(self.docs)
        avgdl = sum(d[2] for d in self.docs) / n
        q = tokenize(query)
        scored: list[tuple[str, float]] = []
        for doc_id, tf, dl in self.docs:
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                norm = self.k1 * (1 - self.b + self.b * dl / avgdl)
                s += idf * tf[t] * (self.k1 + 1) / (tf[t] + norm)
            if s > 0:
                scored.append((doc_id, round(s, 6)))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:k]
