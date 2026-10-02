"""Strict action parsing at the model/world boundary."""

import json
from typing import Any

from .types import Action


class ActionParseError(ValueError):
    """The proposed action does not match the closed action schema."""


SCHEMA = {
    "speak": {"type", "actor_id", "target_id", "topic"},
    "move": {"type", "actor_id", "location_id"},
    "give": {"type", "actor_id", "target_id", "item_id"},
}


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ActionParseError("Duplicate action field: " + key)
        result[key] = value
    return result


def parse_action(raw: Any) -> Action:
    """Accept one JSON object with exact, non-empty string fields."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw, object_pairs_hook=_unique_object)
        except json.JSONDecodeError as error:
            raise ActionParseError("Action is not valid JSON: " + error.msg) from error
    if not isinstance(raw, dict):
        raise ActionParseError("Action must be a JSON object")
    kind = raw.get("type")
    if not isinstance(kind, str) or kind not in SCHEMA:
        raise ActionParseError("Action type must be speak, move or give")
    if set(raw) != SCHEMA[kind]:
        raise ActionParseError("Fields for " + kind + " must be: " + ", ".join(sorted(SCHEMA[kind])))
    if any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in raw.values()):
        raise ActionParseError("Action fields must be non-empty, trimmed strings")
    return Action(**raw)
