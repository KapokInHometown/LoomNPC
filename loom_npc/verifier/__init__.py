"""Rule checks without world mutation."""

from typing import Any, Dict

from ..core.types import Action, WorldState
from ..core.rules import rule_rejection


def result(ok: bool, code: str, message: str) -> Dict[str, Any]:
    """Construct a structured verification result."""
    return {"ok": ok, "code": code, "message": message}


class ActionVerifier:
    """Protect actor identity, knowledge, proximity, ownership and quest rules."""

    def verify(self, world: WorldState, action: Action, actor_id: str) -> Dict[str, Any]:
        """Check a proposed action; this function never commits world state."""
        state = world.to_dict()
        if actor_id not in state["actors"]:
            return result(False, "ACTOR_NOT_FOUND", "行动者不存在。")
        if action.actor_id != actor_id:
            return result(False, "ACTOR_MISMATCH", "模型不能冒充其他角色行动。")
        actor = state["actors"][actor_id]
        if action.type not in actor["allowed_actions"]:
            return result(False, "ACTION_FORBIDDEN", "角色没有此行动权限。")
        if action.type == "move":
            if action.location_id not in state["locations"]:
                return result(False, "LOCATION_NOT_FOUND", "目标地点不存在。")
            if action.location_id not in state["locations"][actor["location"]]["connections"]:
                return result(False, "NOT_CONNECTED", "只能移动到相邻地点。")
            return rule_rejection(state, action.to_dict()) or result(True, "OK", "地点相邻，通行条件满足。")
        if action.target_id not in state["actors"]:
            return result(False, "TARGET_NOT_FOUND", "目标角色不存在。")
        if action.target_id == actor_id:
            return result(False, "SELF_TARGET", "此行动需要另一名角色。")
        target = state["actors"][action.target_id]
        if action.type == "speak":
            if action.topic not in state["facts"]:
                return result(False, "TOPIC_NOT_FOUND", "话题没有绑定已登记的事实模板。")
            if action.topic not in actor["belief"]["known_facts"]:
                return result(False, "KNOWLEDGE_BOUNDARY", "该角色并不知道这条信息，不能把世界真相当成自己的认知。")
        if actor["location"] != target["location"]:
            return result(False, "OUT_OF_REACH", "双方不在同一地点，无法交谈或交付物品。")
        if action.type == "speak":
            fact = state["facts"][action.topic]
            if fact["secret"] and (actor["trust"].get(action.target_id, 0) < fact["required_trust"] or
                                   (fact["required_quest"] and not state["quests"][fact["required_quest"]])):
                return result(False, "SECRET_LOCKED", "信任与任务条件尚未满足，秘密不能透露。")
            return rule_rejection(state, action.to_dict()) or result(True, "OK", "信息在角色认知范围内，披露条件满足。")
        if action.item_id not in state["items"]:
            return result(False, "ITEM_NOT_FOUND", "物品不存在。")
        if action.item_id not in actor["inventory"]:
            return result(False, "NOT_OWNER", "角色未持有该物品。")
        return rule_rejection(state, action.to_dict()) or result(True, "OK", "物品归属、距离与任务条件满足。")
