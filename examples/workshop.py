"""Run a second world's fixed quest with the unchanged offline runtime."""

from pathlib import Path

from loom_npc import Runtime, load_world
from loom_npc.replay import export_jsonl


def main() -> None:
    """Emit rejected and completed workshop decisions as replayable JSONL."""
    runtime = Runtime(load_world(Path(__file__).with_suffix(".json")))
    actions = [
        {"type": "move", "actor_id": "apprentice", "location_id": "forge"},
        {"type": "speak", "actor_id": "smith", "target_id": "apprentice", "topic": "forge_plan"},
        {"type": "give", "actor_id": "apprentice", "target_id": "smith", "item_id": "ore"},
        {"type": "speak", "actor_id": "smith", "target_id": "apprentice", "topic": "forge_plan"},
        {"type": "move", "actor_id": "apprentice", "location_id": "forge"},
    ]
    for action in actions:
        runtime.step(action["actor_id"], "固定工坊输入", proposed_action=action)
    print(export_jsonl(runtime.traces), end="")


if __name__ == "__main__":
    main()
