"""Complete a second world's quest using ordinary offline Chinese commands."""

from pathlib import Path

from loom_npc import Runtime, load_world
from loom_npc.replay import export_jsonl


def main() -> None:
    """Emit rejected and completed workshop decisions as replayable JSONL."""
    runtime = Runtime(load_world(Path(__file__).with_suffix(".json")))
    commands = [
        ("apprentice", "进入锻造间"),
        ("smith", "请向学徒说明开工计划"),
        ("apprentice", "把矿石交给工匠"),
        ("smith", "请向学徒说明开工计划"),
        ("apprentice", "进入锻造间"),
    ]
    for actor_id, text in commands:
        runtime.step(actor_id, text)
    print(export_jsonl(runtime.traces), end="")


if __name__ == "__main__":
    main()
