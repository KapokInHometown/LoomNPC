"""Deterministic memory retrieval without embeddings or external services."""

import copy
import re
import unicodedata
from typing import Any, Dict, List, Set


_HAN = r"\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U000323af"
_HAN_TOKEN = re.compile(rf"[{_HAN}]+")
_TOKENS = re.compile(rf"[{_HAN}]+|[^{_HAN}\W_]+")


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _tokenize(text: str) -> Set[str]:
    """Extract distinct Han bigrams (or a lone character) and whole words."""
    terms = set()
    for token in _TOKENS.findall(text):
        if _HAN_TOKEN.fullmatch(token) and len(token) > 1:
            terms.update(token[index:index + 2] for index in range(len(token) - 1))
        else:
            terms.add(token)
    return terms


def retrieve_memories(actor: Dict[str, Any], query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Rank only the current actor's memories by relevance, importance and time."""
    terms = _tokenize(_normalize(query))
    han_terms = {term for term in terms if _HAN_TOKEN.fullmatch(term)}
    word_terms = terms - han_terms

    def score(memory: Dict[str, Any]) -> tuple:
        summary = _normalize(memory["summary"])
        relevance = sum(term in summary for term in han_terms)
        relevance += len(word_terms & _tokenize(summary))
        return relevance, memory["importance"], memory["tick"], memory["id"]

    return copy.deepcopy(sorted(actor["memories"], key=score, reverse=True)[:limit])
