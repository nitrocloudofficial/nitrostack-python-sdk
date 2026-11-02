"""Pure-Python Okapi BM25 with weighted fields. No dependencies."""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, Iterable, List, Sequence, Tuple

# One document: (doc_id, [(field_weight, tokens), ...])
IndexDocument = Tuple[str, Sequence[Tuple[float, Sequence[str]]]]


class Bm25Engine:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        if k1 < 0:
            raise ValueError("k1 must be >= 0.")
        if not 0.0 <= b <= 1.0:
            raise ValueError("b must be between 0 and 1.")
        self.k1 = k1
        self.b = b
        self._ids: List[str] = []
        self._lengths: List[float] = []
        self._postings: Dict[str, Dict[int, float]] = {}
        self._avg_len = 0.0

    @property
    def size(self) -> int:
        return len(self._ids)

    def build(self, documents: Iterable[IndexDocument]) -> None:
        ids: List[str] = []
        lengths: List[float] = []
        postings: Dict[str, Dict[int, float]] = {}
        for doc_index, (doc_id, fields) in enumerate(documents):
            tf: Counter = Counter()
            length = 0.0
            for weight, tokens in fields:
                for token in tokens:
                    tf[token] += weight
                    length += weight
            ids.append(doc_id)
            lengths.append(length)
            for term, freq in tf.items():
                postings.setdefault(term, {})[doc_index] = freq
        self._ids = ids
        self._lengths = lengths
        self._postings = postings
        self._avg_len = (sum(lengths) / len(lengths)) if lengths else 0.0

    def idf(self, term: str) -> float:
        n = len(self._postings.get(term, ()))
        total = len(self._ids)
        # The "1 +" keeps IDF positive even for terms that appear in most documents.
        return math.log(1.0 + (total - n + 0.5) / (n + 0.5))

    def search(self, query_tokens: Sequence[str]) -> List[Tuple[str, float]]:
        """Return (doc_id, score) for documents with score > 0, best first, ties by id."""
        scores: Dict[int, float] = {}
        for term in set(query_tokens):  # a repeated query word counts once
            docs = self._postings.get(term)
            if not docs:
                continue
            idf = self.idf(term)
            for doc_index, freq in docs.items():
                norm = 1.0
                if self._avg_len > 0:
                    norm = 1.0 - self.b + self.b * (self._lengths[doc_index] / self._avg_len)
                denom = freq + self.k1 * norm
                scores[doc_index] = scores.get(doc_index, 0.0) + idf * (freq * (self.k1 + 1.0)) / denom
        ranked = [(self._ids[i], s) for i, s in scores.items() if s > 0]
        ranked.sort(key=lambda pair: (-pair[1], pair[0]))
        return ranked