"""Opt-in adapter evaluations with state goals and replayable per-run evidence."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from ..core import Runtime, load_world
from ..core.types import read_data
from ..models import LLMAdapter
from ..replay import export_jsonl, replay_jsonl
from . import _at_path


def load_live_scenarios(path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Validate the entire live suite before any model calls or artifact writes."""
    data = read_data("live_evals.json") if path is None else json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"scenarios"} or not isinstance(data["scenarios"], list) or not data["scenarios"]:
        raise ValueError("Live eval configuration must contain a non-empty scenarios list")
    initial = load_world().to_dict()
    ids = set()
    for scenario in data["scenarios"]:
        if not isinstance(scenario, dict) or set(scenario) != {"id", "name", "kind", "steps", "goals"}:
            raise ValueError("Each live scenario needs id, name, kind, steps and goals")
        for field in ("id", "name"):
            if not isinstance(scenario[field], str) or not scenario[field].strip():
                raise ValueError("Live scenario id and name must be non-empty text")
        if scenario["id"] in ids:
            raise ValueError("Live scenario ids must be unique")
        ids.add(scenario["id"])
        if scenario["kind"] not in ("smoke", "boundary", "task"):
            raise ValueError("Live scenario kind must be smoke, boundary or task")
        if not isinstance(scenario["steps"], list) or not scenario["steps"]:
            raise ValueError("Each live scenario needs at least one model step")
        for step in scenario["steps"]:
            if not isinstance(step, dict) or set(step) != {"actor_id", "input"}:
                raise ValueError("Live steps only accept actor_id and input; fixed actions are forbidden")
            if not isinstance(step["actor_id"], str) or step["actor_id"] not in initial["actors"]:
                raise ValueError("Live step actor must exist in the initial world")
            if not isinstance(step["input"], str) or not step["input"].strip():
                raise ValueError("Live step input must be non-empty text")
        if not isinstance(scenario["goals"], dict) or not scenario["goals"]:
            raise ValueError("Each live scenario needs final world-state goals")
        for state_path, condition in scenario["goals"].items():
            if not isinstance(state_path, str) or not isinstance(condition, dict) or len(condition) != 1:
                raise ValueError("Each live goal needs a state path and one condition")
            operation, expected = next(iter(condition.items()))
            actual = _at_path(initial, state_path)
            if operation not in ("equals", "contains", "not_contains"):
                raise ValueError("Live goal operation must be equals, contains or not_contains")
            if type(expected) not in (str, bool, int):
                raise ValueError("Live goal values must be strings, booleans or integers")
            if operation == "equals" and type(actual) is not type(expected):
                raise ValueError("Live equality goal must match the state field type")
            if operation != "equals" and not isinstance(actual, list):
                raise ValueError("Live membership goals require a list state field")
    return data["scenarios"]


def run_live_evals(adapter: LLMAdapter, *, provider: str, model: str, prompt_version: str,
                   output_dir: Union[str, Path], repeats: int = 1,
                   path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Call only the supplied adapter; retain failures, with no retries or fallback."""
    if type(repeats) is not int or repeats < 1:
        raise ValueError("Live eval repeats must be a positive integer")
    for value in (provider, model, prompt_version):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Live eval provider, model and prompt_version must be non-empty text")
    scenarios = load_live_scenarios(path)
    directory = Path(output_dir)
    # A fresh directory prevents overwriting prior runs or mixing trace identities.
    directory.mkdir(parents=True, exist_ok=False)
    configuration = {"provider": provider, "model": model, "prompt_version": prompt_version}
    report = {
        "schema_version": 1, "run_id": str(uuid4()),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "configuration": configuration, "repeats": repeats,
        "scenarios": scenarios, "results": [],
    }
    for scenario_index, scenario in enumerate(scenarios, 1):
        for repeat in range(1, repeats + 1):
            runtime = Runtime(load_world(), adapter=adapter)
            filename = "scenario-{:03d}-repeat-{:03d}.jsonl".format(scenario_index, repeat)
            steps = []
            with (directory / filename).open("x", encoding="utf-8") as trace_file:
                for step_index, step in enumerate(scenario["steps"], 1):
                    trace = runtime.step(step["actor_id"], step["input"])
                    trace_file.write(export_jsonl([trace]))
                    trace_file.flush()
                    steps.append(_step_result(trace, configuration, filename, step_index))
            goals = _check_goals(runtime.world.to_dict(), scenario["goals"])
            replay = replay_jsonl(export_jsonl(runtime.traces))
            goals_met = all(goal["met"] for goal in goals)
            completed = goals_met and replay["ok"] and all(
                step["status"] in ("executed", "rejected") for step in steps
            )
            report["results"].append({
                "scenario_id": scenario["id"], "name": scenario["name"],
                "kind": scenario["kind"], "repeat": repeat,
                "trace_file": filename, "steps": steps, "goals": goals,
                "goals_met": goals_met, "completed": completed,
                "replay": {key: replay[key] for key in ("ok", "count", "model_calls")},
            })
            if not replay["ok"]:
                report["results"][-1]["replay"]["error"] = replay["error"]
    report["metrics"] = _summarize(report["results"])
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (directory / "result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    return report


def _step_result(trace: Dict[str, Any], configuration: Dict[str, str], filename: str,
                 step_index: int) -> Dict[str, Any]:
    request = trace.get("model_request")
    provenance = dict(configuration)
    if request is not None:
        provenance = {
            "provider": request["provider"], "model": request["body"]["model"],
            "prompt_version": request["prompt_version"],
        }
    return {
        "step": step_index, "trace": {"file": filename, "line": step_index, "id": trace["id"]},
        **provenance, "provenance_source": "request" if request is not None else "configuration",
        "status": trace["status"], "model_output_received": trace["raw_output"] is not None,
        "parse_success": trace["action"] is not None,
        "verifier_code": trace["verification"]["code"],
        "errors": trace["errors"],
    }


def _check_goals(state: Dict[str, Any], conditions: Dict[str, Any]) -> List[Dict[str, Any]]:
    goals = []
    for state_path, condition in conditions.items():
        operation, expected = next(iter(condition.items()))
        actual = _at_path(state, state_path)
        met = actual == expected if operation == "equals" else expected in actual
        if operation == "not_contains":
            met = not met
        goals.append({"path": state_path, "condition": condition, "actual": actual, "met": met})
    return goals


def _rate(numerator: int, denominator: int) -> Optional[float]:
    return numerator / denominator if denominator else None


def _summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    steps = [step for result in results for step in result["steps"]]
    statuses = Counter(step["status"] for step in steps)
    outputs = sum(step["model_output_received"] for step in steps)
    parsed = sum(step["parse_success"] for step in steps)
    rejected = Counter(step["verifier_code"] for step in steps if step["status"] == "rejected")
    tasks = [result for result in results if result["kind"] == "task"]
    completed_tasks = sum(result["completed"] for result in tasks)
    return {
        "model_calls": len(steps), "model_outputs": outputs,
        "model_errors": statuses["model_error"],
        "parse_successes": parsed, "parse_failures": statuses["parse_error"],
        "parse_success_rate": _rate(parsed, outputs),
        "parsed_per_call_rate": _rate(parsed, len(steps)),
        "verifier_checks": parsed, "verifier_rejections": dict(sorted(rejected.items())),
        "executed_actions": statuses["executed"], "execution_errors": statuses["execution_error"],
        "status_counts": dict(sorted(statuses.items())),
        "completed_runs": sum(result["completed"] for result in results), "total_runs": len(results),
        "task_completion": {"completed": completed_tasks, "total": len(tasks),
                            "rate": _rate(completed_tasks, len(tasks))},
    }
