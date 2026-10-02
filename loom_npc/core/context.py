"""Central construction of actor-limited model context."""

from dataclasses import asdict
from typing import Any, Dict

from ..memory import retrieve_memories
from .types import Observation, WorldState


def build_context(world: WorldState, actor_id: str, player_input: str) -> Dict[str, Any]:
    """Provide private beliefs and local observations, never global world truth."""
    state = world.to_dict()
    actor = state["actors"][actor_id]
    location_id = actor["location"]
    visible = [
        {key: other[key] for key in ("id", "name", "role", "location")}
        for _, other in sorted(state["actors"].items())
        if other["location"] == location_id and other["id"] != actor_id
    ]
    reachable = [location_id] + state["locations"][location_id]["connections"]
    events = [
        {key: event[key] for key in ("id", "tick", "type", "actor_id", "summary")}
        for event in state["events"]
        if actor_id in event["witnesses"]
    ][-5:]
    observation = Observation(
        location=location_id,
        visible_actors=visible,
        visible_items=[{"id": item, "name": state["items"][item]["name"]} for item in actor["inventory"]],
        available_locations=[{"id": key, "name": state["locations"][key]["name"]} for key in reachable],
        recent_events=events,
    )
    return {
        "prompt_version": "loom-mock-v1",
        "actor": {key: actor[key] for key in ("id", "name", "persona", "goal", "location", "inventory", "trust")},
        "input": player_input,
        "observation": asdict(observation),
        "belief": actor["belief"],
        "known_facts": [state["facts"][key] for key in actor["belief"]["known_facts"]],
        "memories": retrieve_memories(actor, player_input),
        "allowed_actions": actor["allowed_actions"],
    }
