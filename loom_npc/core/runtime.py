"""Explicit decision pipeline: observe, decide, parse, verify, execute, record."""

import copy
from typing import Any, Dict, Optional

from ..models import LLMAdapter, MockLLM, ModelDecision, ModelError
from ..verifier import ActionVerifier, result
from .context import build_context
from .executor import Executor, state_diff
from .parser import ActionParseError, parse_action
from .types import Trace, WorldState, load_world


class Runtime:
    """Run one proposed NPC action at a time and retain all decision outcomes."""

    def __init__(self, world: Optional[WorldState] = None, adapter: Optional[LLMAdapter] = None):
        self.world = world if world is not None else load_world()
        self.adapter = adapter if adapter is not None else MockLLM()
        self.traces = []
        self.verifier = ActionVerifier()
        self.executor = Executor()

    def step(self, actor_id: str, player_input: str, proposed_action: Any = None) -> Dict[str, Any]:
        """Run the decision pipeline; every failure produces a trace."""
        before = self.world.to_dict()
        trace = {
            "id": "trace-{:04d}".format(len(self.traces) + 1),
            "tick": before["tick"], "actor_id": actor_id, "input": player_input,
            "source": "proposal" if proposed_action is not None else "adapter",
            "context": {}, "raw_output": None, "action": None,
            "verification": result(False, "NOT_RUN", "尚未校验。"),
            "execution": {"ok": False, "message": "行动未执行。"},
            "status": "pending", "errors": [], "before": before,
            "after": before, "state_diff": [],
        }
        if actor_id not in before["actors"]:
            trace["verification"] = result(False, "ACTOR_NOT_FOUND", "行动者不存在。")
            return self._finish(trace, "rejected", "observe", "ACTOR_NOT_FOUND", "行动者不存在。")
        if not isinstance(player_input, str):
            return self._finish(trace, "parse_error", "input", "INVALID_INPUT", "输入必须是文本。")
        trace["context"] = build_context(self.world, actor_id, player_input)
        if proposed_action is not None:
            trace["raw_output"] = copy.deepcopy(proposed_action)
        else:
            try:
                decision = self.adapter.generate_decision(copy.deepcopy(trace["context"]))
                if not isinstance(decision, ModelDecision) or not isinstance(decision.raw_output, str):
                    raise TypeError("Adapter must return ModelDecision with text output")
                if decision.request is not None:
                    if not isinstance(decision.request, dict):
                        raise TypeError("Model request provenance must be an object")
                    trace["model_request"] = copy.deepcopy(decision.request)
                trace["raw_output"] = decision.raw_output
            except ModelError as error:
                return self._finish(trace, "model_error", "decide", "MODEL_ERROR", str(error))
            except Exception as error:
                message = "模型适配器执行失败（" + type(error).__name__ + "）。"
                return self._finish(trace, "model_error", "decide", "MODEL_ERROR", message)
        try:
            action = parse_action(trace["raw_output"])
        except ActionParseError as error:
            return self._finish(trace, "parse_error", "parse", "PARSE_ERROR", str(error))
        trace["action"] = action.to_dict()
        trace["verification"] = self.verifier.verify(self.world, action, actor_id)
        if not trace["verification"]["ok"]:
            check = trace["verification"]
            return self._finish(trace, "rejected", "verify", check["code"], check["message"])
        try:
            trace["execution"] = self.executor.execute(self.world, action, actor_id)
        except Exception as error:
            message = "行动执行失败（" + type(error).__name__ + "）。"
            trace["execution"] = {"ok": False, "message": message}
            return self._finish(trace, "execution_error", "execute", "EXECUTION_ERROR", message)
        return self._finish(trace, "executed")

    def _finish(self, trace: Dict[str, Any], status: str, stage: str = "", code: str = "", message: str = "") -> Dict[str, Any]:
        trace["status"] = status
        if stage:
            trace["errors"].append({"stage": stage, "code": code, "message": message})
        trace["after"] = self.world.to_dict()
        trace["state_diff"] = state_diff(trace["before"], trace["after"])
        recorded = Trace(trace).to_dict()
        self.traces.append(recorded)
        return copy.deepcopy(recorded)
