"""Read and verify JSONL decision records without calling a model."""

import json
from typing import Any, Dict, Iterable

from ..core.context import build_context
from ..core.executor import Executor, state_diff
from ..core.parser import ActionParseError, parse_action
from ..core.speech import verify_speech_record
from ..core.types import WorldState
from ..verifier import ActionVerifier, result


def export_jsonl(traces: Iterable[Dict[str, Any]]) -> str:
    """Encode one complete trace per line, preserving Unicode text."""
    return "".join(json.dumps(trace, ensure_ascii=False, sort_keys=True) + "\n" for trace in traces)


def replay_jsonl(text: str) -> Dict[str, Any]:
    """Recompute rule/execution results and state continuity with zero model calls."""
    world = None
    count = 0
    recorded_failures = 0
    speech_records = 0
    try:
        if not isinstance(text, str):
            raise ValueError("Replay input must be JSONL text")
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            trace = json.loads(line)
            if not isinstance(trace, dict):
                raise ValueError("Trace must be an object")
            required = {"id", "tick", "actor_id", "input", "source", "context", "raw_output", "action", "verification", "execution", "status", "errors", "before", "after", "state_diff"}
            if not required <= set(trace) <= required | {"model_request", "speech"}:
                raise ValueError("Trace fields do not match the schema")
            if "model_request" in trace:
                _require(trace["source"] == "adapter" and isinstance(trace["model_request"], dict), "Invalid model request provenance")
            if "speech" in trace:
                _require(trace["status"] == "executed" and isinstance(trace["action"], dict)
                         and trace["action"].get("type") == "speak", "Dialogue requires executed speak")
            if world is None:
                world = WorldState.from_dict(trace["before"])
            _require(trace["before"] == world.to_dict(), "World-state continuity mismatch")
            _require(trace["id"] == "trace-{:04d}".format(count + 1), "Trace id sequence mismatch")
            _require(trace["tick"] == world.tick, "Trace tick mismatch")
            _require(trace["source"] in ("adapter", "proposal"), "Unknown trace source")
            _verify_trace(world, trace)
            _require(trace["after"] == world.to_dict(), "Recomputed world differs from recorded after state")
            _require(trace["state_diff"] == state_diff(trace["before"], trace["after"]), "State diff mismatch")
            if trace["status"] in ("model_error", "execution_error"):
                recorded_failures += 1
            if "speech" in trace:
                speech_records += 1
            count += 1
        _require(count > 0, "Replay contains no traces")
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        return {"ok": False, "count": count, "world": world.to_dict() if world else None, "error": str(error), "model_calls": 0}
    limitations = []
    if recorded_failures:
        limitations.append("模型或环境导致的失败只核对结构与未改状态，不重现外部故障。")
    if speech_records:
        limitations.append("台词仅核对记录、格式与回退边界，不重新生成，不检验语义安全或认证来源。")
    return {
        "ok": True, "count": count, "world": world.to_dict(), "model_calls": 0,
        "recorded_failures": recorded_failures,
        "limitation": "".join(limitations) or None,
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _error(trace: Dict[str, Any], stage: str, code: str) -> None:
    errors = trace["errors"]
    _require(isinstance(errors, list) and len(errors) == 1, "Failure must have one explicit error")
    _require(set(errors[0]) == {"stage", "code", "message"}, "Invalid error schema")
    _require(errors[0]["stage"] == stage and errors[0]["code"] == code, "Error stage/code mismatch")
    _require(isinstance(errors[0]["message"], str) and bool(errors[0]["message"]), "Missing error message")


def _verify_trace(world: WorldState, trace: Dict[str, Any]) -> None:
    actor_id = trace["actor_id"]
    status = trace["status"]
    pending = result(False, "NOT_RUN", "尚未校验。")
    not_executed = {"ok": False, "message": "行动未执行。"}
    if actor_id not in world.to_dict()["actors"]:
        _require(status == "rejected", "Missing actor must be rejected")
        _require(trace["context"] == {} and trace["raw_output"] is None and trace["action"] is None, "Missing actor trace contains a decision")
        _require(trace["verification"] == result(False, "ACTOR_NOT_FOUND", "行动者不存在。"), "Actor rejection mismatch")
        _require(trace["execution"] == not_executed, "Rejected action was executed")
        _error(trace, "observe", "ACTOR_NOT_FOUND")
        return
    if not isinstance(trace["input"], str):
        _require(status == "parse_error", "Non-text input must fail")
        _require(trace["context"] == {} and trace["raw_output"] is None and trace["action"] is None, "Invalid input trace contains a decision")
        _require(trace["verification"] == pending and trace["execution"] == not_executed, "Invalid input was executed")
        _error(trace, "input", "INVALID_INPUT")
        return
    _require(trace["context"] == build_context(world, actor_id, trace["input"]), "Actor context mismatch")
    if status == "model_error":
        _require(trace["source"] == "adapter" and trace["raw_output"] is None and trace["action"] is None, "Invalid model failure")
        _require(trace["verification"] == pending and trace["execution"] == not_executed, "Model failure was executed")
        _error(trace, "decide", "MODEL_ERROR")
        return
    try:
        action = parse_action(trace["raw_output"])
    except ActionParseError:
        _require(status == "parse_error" and trace["action"] is None, "Invalid raw action must fail parsing")
        _require(trace["verification"] == pending and trace["execution"] == not_executed, "Parse failure was executed")
        _error(trace, "parse", "PARSE_ERROR")
        return
    _require(trace["action"] == action.to_dict(), "Parsed action differs from raw output")
    verification = ActionVerifier().verify(world, action, actor_id)
    _require(trace["verification"] == verification, "Verification result mismatch")
    if not verification["ok"]:
        _require(status == "rejected" and trace["execution"] == not_executed, "Invalid action was not rejected")
        _error(trace, "verify", verification["code"])
        return
    if status == "execution_error":
        _require(isinstance(trace["execution"], dict) and set(trace["execution"]) == {"ok", "message"}, "Invalid execution failure")
        _require(trace["execution"]["ok"] is False, "Failed execution marked successful")
        _error(trace, "execute", "EXECUTION_ERROR")
        return
    _require(status == "executed" and trace["errors"] == [], "Valid execution status mismatch")
    execution = Executor().execute(world, action, actor_id)
    _require(trace["execution"] == execution, "Execution result mismatch")
    if "speech" in trace:
        verify_speech_record(trace["speech"], trace["before"], trace["context"], action, execution["speech"])
