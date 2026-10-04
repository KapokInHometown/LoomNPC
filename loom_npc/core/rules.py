"""Closed, declarative scenario rules evaluated against the pre-action snapshot."""

from typing import Any, Dict, List, Optional


def validate_rules(state: Dict[str, Any]) -> None:
    """Validate rule schemas and every world reference before execution."""
    from .parser import SCHEMA

    rules = state.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("World rules must be a list")
    ids = set()
    references = {"actor_id": "actors", "target_id": "actors", "item_id": "items",
                  "location_id": "locations", "topic": "facts"}
    for rule in rules:
        _fields(rule, {"id", "match", "requires", "reject", "effects", "summary_suffix"})
        _text(rule["id"])
        if rule["id"] in ids:
            raise ValueError("Duplicate scenario rule id: " + rule["id"])
        ids.add(rule["id"])
        match = rule["match"]
        if not isinstance(match, dict) or not isinstance(match.get("type"), str) or match["type"] not in SCHEMA:
            raise ValueError("Rule match needs a supported action type")
        if not set(match) <= SCHEMA[match["type"]]:
            raise ValueError("Rule match fields must belong to its action schema")
        for field, value in match.items():
            _text(value)
            if field in references:
                _reference(state, references[field], value)
        _conditions(state, rule["requires"], match["type"])
        _fields(rule["reject"], {"code", "message"})
        _text(rule["reject"]["code"])
        _text(rule["reject"]["message"])
        if not isinstance(rule["summary_suffix"], str):
            raise ValueError("Rule summary_suffix must be text")
        if not isinstance(rule["effects"], list):
            raise ValueError("Rule effects must be a list")
        for effect in rule["effects"]:
            kind = _kind(effect)
            fields = {"type", "quest_id", "value"} if kind == "set_quest" else {"type", "actor_id", "target_id", "amount"}
            if kind not in {"set_quest", "add_trust"}:
                raise ValueError("Unknown scenario effect: " + kind)
            _fields(effect, fields, {"when"})
            _conditions(state, effect.get("when", []), match["type"])
            if kind == "set_quest":
                _reference(state, "quests", effect["quest_id"])
                if type(effect["value"]) is not bool:
                    raise ValueError("Quest effect value must be boolean")
            else:
                _reference(state, "actors", effect["actor_id"])
                _reference(state, "actors", effect["target_id"])
                if type(effect["amount"]) is not int or effect["amount"] <= 0:
                    raise ValueError("Trust effect amount must be a positive integer")


def _fields(value: Any, required: set, optional: Optional[set] = None) -> None:
    if not isinstance(value, dict) or not required <= set(value) <= required | (optional or set()):
        raise ValueError("Invalid scenario rule fields; expected " + ", ".join(sorted(required)))


def _text(value: Any) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("Scenario rule identifiers/messages must be non-empty trimmed text")


def _reference(state: Dict[str, Any], collection: str, key: Any) -> None:
    _text(key)
    if key not in state[collection]:
        raise ValueError("Unknown scenario rule reference in " + collection + ": " + key)


def _kind(value: Any) -> str:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise ValueError("Scenario condition/effect needs a type")
    return value["type"]


def _conditions(state: Dict[str, Any], conditions: Any, action_type: str) -> None:
    if not isinstance(conditions, list):
        raise ValueError("Scenario conditions must be a list")
    fields = {
        "quest_state": {"type", "quest_id", "value"},
        "has_item": {"type", "actor_id", "item_id"},
        "trust_at_least": {"type", "actor_id", "target_id", "minimum"},
        "target_is": {"type", "actor_id"},
    }
    for condition in conditions:
        kind = _kind(condition)
        if kind not in fields:
            raise ValueError("Unknown scenario condition: " + kind)
        _fields(condition, fields[kind])
        for field, collection in (("actor_id", "actors"), ("target_id", "actors"),
                                  ("item_id", "items"), ("quest_id", "quests")):
            if field in condition:
                _reference(state, collection, condition[field])
        if kind == "quest_state" and type(condition["value"]) is not bool:
            raise ValueError("Quest condition value must be boolean")
        if kind == "trust_at_least" and (type(condition["minimum"]) is not int or condition["minimum"] < 0):
            raise ValueError("Trust condition minimum must be a non-negative integer")
        if kind == "target_is" and action_type == "move":
            raise ValueError("Move rules cannot check a target actor")


def matching_rules(state: Dict[str, Any], action: Dict[str, str]) -> List[Dict[str, Any]]:
    """Return all matching rules in configuration order, without mutation."""
    return [rule for rule in state.get("rules", [])
            if all(action.get(field) == value for field, value in rule["match"].items())]


def _satisfied(state: Dict[str, Any], action: Dict[str, str], conditions: List[Dict[str, Any]]) -> bool:
    for condition in conditions:
        kind = condition["type"]
        if kind == "quest_state":
            ok = state["quests"][condition["quest_id"]] == condition["value"]
        elif kind == "has_item":
            ok = condition["item_id"] in state["actors"][condition["actor_id"]]["inventory"]
        elif kind == "trust_at_least":
            ok = state["actors"][condition["actor_id"]]["trust"].get(condition["target_id"], 0) >= condition["minimum"]
        else:
            ok = action.get("target_id") == condition["actor_id"]
        if not ok:
            return False
    return True


def rule_rejection(state: Dict[str, Any], action: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Return the first unmet rule's configured failure; shared checks run first."""
    for rule in matching_rules(state, action):
        if not _satisfied(state, action, rule["requires"]):
            return {"ok": False, **rule["reject"]}
    return None


def apply_rule_effects(before: Dict[str, Any], state: Dict[str, Any], action: Dict[str, str]) -> str:
    """Apply effects only to the executor's copy; every guard reads before state."""
    suffix = ""
    for rule in matching_rules(before, action):
        for effect in rule["effects"]:
            if not _satisfied(before, action, effect.get("when", [])):
                continue
            if effect["type"] == "set_quest":
                state["quests"][effect["quest_id"]] = effect["value"]
            else:
                trust = state["actors"][effect["actor_id"]]["trust"]
                target = effect["target_id"]
                trust[target] = trust.get(target, 0) + effect["amount"]
        suffix += rule["summary_suffix"]
    return suffix
