"""Emit a complete deterministic quest as replayable JSONL."""

from loom_npc import Runtime
from loom_npc.replay import export_jsonl


def main() -> None:
    """Show a blocked secret followed by the legal letter/key sequence."""
    runtime = Runtime()
    actions = [
        {"type": "speak", "actor_id": "mara", "target_id": "player", "topic": "lighthouse_secret"},
        {"type": "give", "actor_id": "player", "target_id": "mara", "item_id": "letter"},
        {"type": "speak", "actor_id": "mara", "target_id": "player", "topic": "lighthouse_secret"},
        {"type": "give", "actor_id": "mara", "target_id": "player", "item_id": "key"},
        {"type": "move", "actor_id": "player", "location_id": "tower"},
    ]
    for action in actions:
        runtime.step(action["actor_id"], "固定场景输入", proposed_action=action)
    print(export_jsonl(runtime.traces), end="")


if __name__ == "__main__":
    main()
