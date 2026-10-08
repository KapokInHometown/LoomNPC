"""Model adapter boundary and an explicitly deterministic offline mock."""

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol


class ModelError(RuntimeError):
    """Adapter failure whose message is safe to record; never include credentials."""


@dataclass(frozen=True)
class ModelDecision:
    """Untrusted model output awaiting strict parsing and verification."""

    raw_output: str
    # Only the inference payload and prompt provenance, never headers or credentials.
    request: Optional[Dict[str, Any]] = None


class LLMAdapter(Protocol):
    """Replaceable model boundary; adapters never receive a WorldState."""

    def generate_decision(self, context: Dict[str, Any]) -> ModelDecision:
        """Propose a single action from the actor-limited context."""
        ...


class MockLLM:
    """Resolve limited commands against actor-visible labels, entirely offline."""

    def generate_decision(self, context: Dict[str, Any]) -> ModelDecision:
        """Route the supported Chinese/English commands deterministically."""
        actor = context["actor"]["id"]
        text = _normalize(context["input"])
        observation = context["observation"]
        items = observation["visible_items"]
        bare_item = any(text == _normalize(label) for item in items for label in _labels(item))
        if _contains_any(text, ("前往", "走到", "移动", "进入", "去", "move", "go to")):
            kind = "move"
        elif bare_item or _contains_any(text, ("交付", "交给", "递给", "递交", "送给", "送还", "give", "deliver", "hand")) or any(
            _contains(text, verb + label) for item in items for label in _labels(item) for verb in ("交", "递")
        ):
            kind = "give"
        else:
            kind = "speak"
        if kind not in context["allowed_actions"]:
            raise ModelError("Mock 无法决策：角色没有请求的行动权限。")
        action = {"type": kind, "actor_id": actor}
        if kind == "move":
            locations = [place for place in observation["available_locations"]
                         if place["id"] != context["actor"]["location"]]
            action["location_id"] = _resolve(text, locations, "相邻地点")
        else:
            action["target_id"] = _target(text, observation["visible_actors"])
            if kind == "give":
                action["item_id"] = _resolve(text, items, "持有物品")
            else:
                action["topic"] = _resolve(text, context["known_facts"], "已知话题")
        return ModelDecision(json.dumps(action, ensure_ascii=False, sort_keys=True))


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def _labels(entity: Dict[str, Any]) -> List[str]:
    return [entity["id"], entity.get("name", entity.get("text", ""))] + entity.get("aliases", [])


def _contains(text: str, label: str) -> bool:
    label = _normalize(label)
    if not label:
        return False
    if label.isascii():
        return re.search(r"(?<![a-z0-9_])" + re.escape(label) + r"(?![a-z0-9_])", text) is not None
    return label in text


def _contains_any(text: str, labels: tuple) -> bool:
    return any(_contains(text, label) for label in labels)


def _resolve(text: str, entities: List[Dict[str, Any]], label: str) -> str:
    """Discard contained short labels; reject independent matches or ties."""
    matches = []
    for entity in entities:
        matches.extend((_normalize(name), entity["id"]) for name in _labels(entity) if _contains(text, name))
    if not matches:
        raise ModelError("Mock 无法决策：未匹配到" + label + "。")
    ids = {identifier for name, identifier in matches
           if not any(name != other and _contains(other, name) for other, _ in matches)}
    if len(ids) != 1:
        raise ModelError("Mock 无法决策：" + label + "有歧义，请使用完整名称或标识。")
    return next(iter(ids))


def _target(text: str, actors: List[Dict[str, Any]]) -> str:
    """Resolve an explicit addressee, or the sole visible conversation partner."""
    slot = re.search(r"(?:交给|递给|送给|给|对|向|(?<![a-z0-9_])to\b)\s*([^，。！？,.!?]+)", text)
    if slot:
        address = slot.group(1).strip()
        suffix = r"(?:$|\s|[吧呀啊]|说明|讲|介绍|说|聊|谈|询问|关于|的)"
        if not re.match(r"[我你]" + suffix, address):
            candidates = [actor for actor in actors if any(
                re.match(re.escape(_normalize(name)) + suffix, address) for name in _labels(actor)
            )]
            if len(candidates) != 1:
                raise ModelError("Mock 无法决策：未指定唯一的可见目标角色。")
            return candidates[0]["id"]
    mentioned = [actor for actor in actors if any(_contains(text, name) for name in _labels(actor))]
    candidates = mentioned or actors
    if len(candidates) != 1:
        raise ModelError("Mock 无法决策：需要唯一的可见目标角色，请指定名称或标识。")
    return candidates[0]["id"]
