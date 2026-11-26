from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from nitrostack.transforms.search.base import BaseSearchTransform
from nitrostack.transforms.search.models import RankOutcome
from nitrostack.transforms.search.regex_match import run_regex
from nitrostack.transforms.search.serializer import FIELD_WEIGHTS, tool_fields

_N_FIELDS = len(FIELD_WEIGHTS)


class RegexSearchTransform(BaseSearchTransform):
    """Literal, case-insensitive substring search. Optionally treats the query as a regex.

    The regex runs in a child process with a timeout. A timeout, an invalid pattern,
    or a failure to start the worker falls back to a literal match with a note.
    """

    name = "regex_search"

    def __init__(
        self,
        *,
        allow_regex: bool = False,
        regex_timeout: float = 2.0,
        max_pattern_length: int = 200,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if regex_timeout <= 0:
            raise ValueError("regex_timeout must be positive.")
        self.allow_regex = allow_regex
        self.regex_timeout = regex_timeout
        self.max_pattern_length = max_pattern_length

    async def rank(self, query: str, tools: Sequence[Any]) -> RankOutcome:
        q = query.strip()
        texts: List[str] = []
        for tool in tools:
            texts.extend(tool_fields(tool))

        hits: Optional[set] = None
        note: Optional[str] = None

        if self.allow_regex:
            if len(q) > self.max_pattern_length:
                note = f"Pattern longer than {self.max_pattern_length} characters; used a literal match."
            else:
                outcome = await run_regex(q, texts, self.regex_timeout)
                if outcome.hits is not None:
                    hits = outcome.hits
                else:
                    note = f"Regex not used: {outcome.reason}. Used a literal match."

        if hits is None:
            needle = q.casefold()
            hits = {i for i, text in enumerate(texts) if needle in text.casefold()}

        scored: List[Tuple[Any, float]] = []
        for index, tool in enumerate(tools):
            score = sum(
                FIELD_WEIGHTS[f] for f in range(_N_FIELDS) if (index * _N_FIELDS + f) in hits
            )
            if score > 0:
                scored.append((tool, score))
        scored.sort(key=lambda pair: (-pair[1], pair[0].name))  # equal scores: by name
        return RankOutcome(ranked=scored, note=note)