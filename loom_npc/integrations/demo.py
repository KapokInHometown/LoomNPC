"""Presentation metadata for the local debugger, never part of NPC context."""

import re
from typing import Any, Dict

from loom_npc.core.types import read_data


def demo_presentation(initial: Dict[str, Any]) -> Dict[str, Any]:
    """Use bundled art only for its matching scene; otherwise show generic places."""
    town = read_data("lantern_town.json")
    # IDs alone cannot establish that a user-authored world fits the illustration.
    fields = ("id", "locations", "items", "facts", "rules")
    actor_fields = ("id", "name", "role", "persona", "goal", "allowed_actions")
    if (all(initial.get(key) == town.get(key) for key in fields)
            and initial["actors"].keys() == town["actors"].keys()
            and all(actor[field] == town["actors"][identifier][field]
                    for identifier, actor in initial["actors"].items() for field in actor_fields)
            and initial["quests"].keys() == town["quests"].keys()):
        return read_data("lantern_town_demo.json")
    return {
        "art": "generic",
        "default_actor": next(iter(initial["actors"])),
        "description": " ".join(actor["name"] + "：" + actor["goal"]
                                for actor in initial["actors"].values()),
    }


def trace_filename(world: Dict[str, Any]) -> str:
    """Keep response headers ASCII and exclude user-configured path characters."""
    identifier = re.sub(r"[^a-zA-Z0-9_-]+", "-", world["id"]).strip("-") or "world"
    return "loom-%s-%s.jsonl" % (identifier.replace("_", "-"), world["tick"])
