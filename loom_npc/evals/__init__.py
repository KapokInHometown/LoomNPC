"""Readable, reproducible behavioral scenarios using the offline mock."""

import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

from ..core import Runtime, load_world
from ..core.types import read_data
from ..replay import export_jsonl, replay_jsonl


def run_evals(path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Run fixed scenarios and report behavioral checks plus replay integrity."""
    data = read_data("evals.json") if path is None else json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"scenarios"} or not isinstance(data["scenarios"], list):
        raise ValueError("Eval configuration must contain a scenarios list")
    results = []
    for scenario in data["scenarios"]:
        if not isinstance(scenario, dict) or set(scenario) != {"id", "name", "steps"}:
            raise ValueError("Each eval needs id, name and steps")
        if not isinstance(scenario["steps"], list) or not scenario["steps"]:
            raise ValueError("Each eval needs at least one step")
        runtime = Runtime(load_world())
        failures = []
        for index, step in enumerate(scenario["steps"], 1):
            if not isinstance(step, dict) or not {"actor_id", "input", "expect"} <= set(step) or set(step) - {"actor_id", "input", "action", "expect"}:
                raise ValueError("Each step needs actor_id, input, optional action, and expect")
            trace = runtime.step(step["actor_id"], step["input"], step.get("action"))
            expected = step["expect"]
            if not isinstance(expected, dict) or not expected:
                raise ValueError("Each step must define expected trace paths")
            for path_key, value in expected.items():
                actual = _at_path(trace, path_key)
                if actual != value:
                    failures.append("step {} {}: expected {!r}, got {!r}".format(index, path_key, value, actual))
        replay = replay_jsonl(export_jsonl(runtime.traces))
        if not replay["ok"]:
            failures.append("Replay: " + replay["error"])
        results.append({
            "id": scenario["id"], "name": scenario["name"], "passed": not failures,
            "detail": "; ".join(failures) if failures else "{} 个固定步骤与无模型回放通过。".format(len(scenario["steps"])),
        })
    return {"passed": sum(item["passed"] for item in results), "total": len(results), "results": results}


def _at_path(value: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError("Unknown expected trace path: " + path)
        value = value[part]
    return value
