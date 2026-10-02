"""Model adapter boundary and an explicitly deterministic offline mock."""

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol


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
    """Fixed intent routing for the demo. This is not a real language model."""

    def generate_decision(self, context: Dict[str, Any]) -> ModelDecision:
        """Route the supported Chinese/English commands deterministically."""
        actor = context["actor"]["id"]
        text = context["input"].lower()
        target = "mara" if actor == "player" else "player"
        if any(word in text for word in ("交信", "递信", "交付信", "deliver letter")):
            action = {"type": "give", "actor_id": actor, "target_id": "mara", "item_id": "letter"}
        elif any(word in text for word in ("钥匙", "key")):
            action = {"type": "give", "actor_id": actor, "target_id": target, "item_id": "key"}
        elif any(word in text for word in ("前往", "走到", "移动", "进入", "move", "go to")):
            location = "tower" if any(word in text for word in ("塔", "tower")) else "inn" if any(word in text for word in ("旅店", "inn")) else "square"
            action = {"type": "move", "actor_id": actor, "location_id": location}
        else:
            topic = "lighthouse_secret" if any(word in text for word in ("秘密", "secret")) else "town" if any(word in text for word in ("小镇", "灯港", "town")) else "letter_request" if "信" in text else "greeting"
            action = {"type": "speak", "actor_id": actor, "target_id": target, "topic": topic}
        return ModelDecision(json.dumps(action, ensure_ascii=False, sort_keys=True))
