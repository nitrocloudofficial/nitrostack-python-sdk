"""Tokenizer for tool search: lowercase, split on punctuation, snake_case and camelCase."""
from __future__ import annotations

import re
import unicodedata
from typing import List

# Words that carry no search meaning. Removed from documents and queries alike.
STOP_WORDS = frozenset({
    "a", "an", "the", "of", "to", "for", "and", "or", "in", "on", "by",
    "with", "is", "it", "this", "that",
})

# getRnaById -> get Rna By Id ; HTTPServer -> HTTP Server ; v2Api -> v2 Api
_CAMEL_LOWER_UPPER = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CAMEL_ACRONYM = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
# Letters and digits in any script; underscore is a separator, not a word character.
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str, *, drop_stop_words: bool = True) -> List[str]:
    if not text:
        return []
    text = unicodedata.normalize("NFC", text)
    text = _CAMEL_LOWER_UPPER.sub(" ", text)
    text = _CAMEL_ACRONYM.sub(" ", text)
    tokens = [t.lower() for t in _WORD.findall(text)]
    if drop_stop_words:
        tokens = [t for t in tokens if t not in STOP_WORDS]
    return tokens