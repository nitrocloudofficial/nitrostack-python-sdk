from __future__ import annotations

import hashlib
from typing import Any, Optional, Sequence

from nitrostack.transforms.search.base import BaseSearchTransform
from nitrostack.transforms.search.bm25_engine import Bm25Engine
from nitrostack.transforms.search.models import RankOutcome
from nitrostack.transforms.search.serializer import FIELD_WEIGHTS, tool_fields
from nitrostack.transforms.search.tokenizer import tokenize


def catalog_fingerprint(tools: Sequence[Any]) -> str:
    """Order-independent hash of everything the index is built from."""
    digest = hashlib.sha256()
    for fields in sorted(tool_fields(t) for t in tools):
        for part in fields:
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
        digest.update(b"\x01")
    return digest.hexdigest()


class Bm25SearchTransform(BaseSearchTransform):
    """Ranks tools by BM25 over name, title, description and parameter text.

    Field weights (name > title > description > parameters) come from FIELD_WEIGHTS.
    The index is built lazily and rebuilt only when the catalog fingerprint changes.
    """

    name = "bm25_search"

    def __init__(self, *, k1: float = 1.5, b: float = 0.75, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._k1 = k1
        self._b = b
        Bm25Engine(k1=k1, b=b)  # validate parameters now, not on the first search
        self._engine: Optional[Bm25Engine] = None
        self._fingerprint: Optional[str] = None
        self.build_count = 0

    def _ensure_index(self, tools: Sequence[Any]) -> Bm25Engine:
        fingerprint = catalog_fingerprint(tools)
        if self._engine is None or fingerprint != self._fingerprint:
            # Build a complete new engine, then swap. There is no await in between,
            # so no search can observe a half-built index.
            engine = Bm25Engine(k1=self._k1, b=self._b)
            engine.build(
                (
                    tool.name,
                    [
                        (weight, tokenize(text))
                        for weight, text in zip(FIELD_WEIGHTS, tool_fields(tool))
                    ],
                )
                for tool in tools
            )
            self._engine = engine
            self._fingerprint = fingerprint
            self.build_count += 1
        return self._engine

    async def rank(self, query: str, tools: Sequence[Any]) -> RankOutcome:
        tokens = tokenize(query)
        if not tokens:
            return RankOutcome(
                ranked=[],
                note="The query has no searchable words. Try a specific term, or search with an empty query to browse.",
            )
        engine = self._ensure_index(tools)
        by_name = {tool.name: tool for tool in tools}
        ranked = [(by_name[name], score) for name, score in engine.search(tokens) if name in by_name]
        return RankOutcome(ranked=ranked)