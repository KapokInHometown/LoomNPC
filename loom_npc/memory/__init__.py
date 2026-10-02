"""Deterministic memory retrieval without embeddings or external services."""

import copy
from typing import Any, Dict, List


def retrieve_memories(actor: Dict[str, Any], query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Rank only the current actor's memories by relevance, importance and time."""
    terms = set(query.lower().split())

    def score(memory: Dict[str, Any]) -> tuple:
        summary = memory["summary"].lower()
        relevance = sum(term in summary for term in terms)
        return relevance, memory["importance"], memory["tick"], memory["id"]

    return copy.deepcopy(sorted(actor["memories"], key=score, reverse=True)[:limit])
