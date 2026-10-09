"""Deterministic, transactional execution of verified actions."""

import copy
from dataclasses import asdict
from typing import Any, Dict, List

from ..verifier import ActionVerifier
from .types import Action, Event, Memory, WorldState
from .rules import apply_rule_effects


def state_diff(before: Dict[str, Any], after: Dict[str, Any], prefix: str = "") -> List[Dict[str, Any]]:
    """List changed JSON paths; arrays are recorded as one complete value."""
    differences = []
    for key in sorted(set(before) | set(after)):
        path = prefix + "/" + key
        old, new = before.get(key), after.get(key)
        if isinstance(old, dict) and isinstance(new, dict):
            differences.extend(state_diff(old, new, path))
        elif old != new:
            differences.append({"path": path, "before": old, "after": new})
    return differences


class Executor:
    """Commit canonical actions; generated display text never enters execution."""

    def execute(self, world: WorldState, action: Action, actor_id: str) -> Dict[str, Any]:
        """Recheck rules, apply on a copy, and commit all changes atomically."""
        verification = ActionVerifier().verify(world, action, actor_id)
        if not verification["ok"]:
            raise ValueError("Action is not executable: " + verification["code"])
        before = world.to_dict()
        state = copy.deepcopy(before)
        actor = state["actors"][actor_id]
        target = state["actors"].get(action.target_id)
        origin = actor["location"]
        participants = [actor_id] + ([action.target_id] if action.target_id else [])
        witnesses = sorted(participants)
        speech = None
        importance = 1
        if action.type == "speak":
            # Fact propagation and event/memory provenance use this registered
            # template, independently of optional later dialogue generation.
            fact = state["facts"][action.topic]
            speech = fact["text"].format(name=actor["name"], target=target["name"])
            if action.topic not in target["belief"]["known_facts"]:
                target["belief"]["known_facts"].append(action.topic)
            summary = actor["name"] + "对" + target["name"] + "说：“" + speech + "”"
            importance = 3 if fact["secret"] else 1
        elif action.type == "move":
            actor["location"] = action.location_id
            witnesses = sorted(key for key, other in state["actors"].items() if other["location"] in (origin, action.location_id))
            summary = actor["name"] + "从" + state["locations"][origin]["name"] + "前往" + state["locations"][action.location_id]["name"] + "。"
        else:
            actor["inventory"].remove(action.item_id)
            target["inventory"].append(action.item_id)
            summary = actor["name"] + "将" + state["items"][action.item_id]["name"] + "交给" + target["name"] + "。"
            importance = 2
        summary += apply_rule_effects(before, state, action.to_dict())
        state["tick"] += 1
        event = Event(
            id="event-{:04d}".format(state["tick"]), tick=state["tick"], type=action.type,
            actor_id=actor_id, location=origin, participants=participants,
            witnesses=witnesses, summary=summary, action=action.to_dict(),
        )
        event_data = asdict(event)
        state["events"].append(event_data)
        for witness in witnesses:
            memory = Memory(
                id="memory-{}-{:04d}".format(witness, state["tick"]), event_id=event.id,
                tick=state["tick"], summary=summary, actor_ids=participants,
                location=origin, importance=importance,
            )
            state["actors"][witness]["memories"].append(asdict(memory))
        world._commit(state)
        execution = {"ok": True, "message": summary, "event": event_data}
        if speech is not None:
            execution["speech"] = speech
        return execution
