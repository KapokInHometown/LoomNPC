"""Presentation-only dialogue with public context and deterministic fallback."""

import copy
import json
from typing import Any, Dict

from ..models.speech import ModelSpeech, SpeechAdapter
from ..models.prompts import SPEECH_PROMPT_VERSION
from ..verifier import ActionVerifier
from .types import Action, WorldState


MAX_SPEECH_CHARS = 1000


def build_speech_context(before: Dict[str, Any], context: Dict[str, Any],
                         action: Action, fallback: str) -> Dict[str, Any]:
    """Project pre-action actor context; omit secret facts and their history.

    Authored persona/goal/labels and player input remain untrusted text. This
    projection does not perform semantic validation of those strings.
    """
    world = WorldState.from_dict(before)
    verifier = ActionVerifier()
    public_ids = {
        fact["id"] for fact in context["known_facts"]
        if not fact["secret"] and verifier.verify(
            world, Action("speak", action.actor_id, action.target_id, fact["id"]), action.actor_id
        )["ok"]
    }
    safe_events = {
        event["id"] for event in before["events"]
        if event["type"] != "speak" or event["action"]["topic"] in public_ids
    }
    observation = copy.deepcopy(context["observation"])
    observation["recent_events"] = [event for event in observation["recent_events"] if event["id"] in safe_events]
    return {
        "prompt_version": SPEECH_PROMPT_VERSION,
        "actor": copy.deepcopy(context["actor"]),
        "input": context["input"],
        "target": next(copy.deepcopy(actor) for actor in observation["visible_actors"] if actor["id"] == action.target_id),
        "action": action.to_dict(),
        "approved_fact": {"id": action.topic, "text": fallback},
        "belief": {"known_facts": [key for key in context["belief"]["known_facts"] if key in public_ids]},
        "observation": observation,
        "memories": [copy.deepcopy(memory) for memory in context["memories"] if memory["event_id"] in safe_events],
    }


def parse_speech(raw_output: str) -> str:
    """Check JSON shape and display limits only, never factual or secret safety."""
    def unique_fields(pairs):
        record = dict(pairs)
        if len(record) != len(pairs):
            raise ValueError("Duplicate dialogue fields")
        return record

    data = json.loads(raw_output, object_pairs_hook=unique_fields)
    if not isinstance(data, dict) or set(data) != {"text"} or not isinstance(data["text"], str):
        raise ValueError("Dialogue must contain only a text string")
    text = data["text"].strip()
    if not text or len(text) > MAX_SPEECH_CHARS or any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise ValueError("Dialogue is empty, too long or contains control characters")
    return text


def render_speech(adapter: SpeechAdapter, before: Dict[str, Any], context: Dict[str, Any],
                  action: Action, fallback: str) -> Dict[str, Any]:
    """Try one optional generation after execution; failure retains fixed speech."""
    record = {"status": "skipped", "code": "SECRET_TOPIC", "text": fallback,
              "context": {}, "raw_output": None, "semantic_validation": "not_performed"}
    if before["facts"][action.topic]["secret"]:
        return record
    record["context"] = build_speech_context(before, context, action, fallback)
    record.update(status="fallback", code="MODEL_ERROR")
    try:
        output = adapter.generate_speech(copy.deepcopy(record["context"]))
        if not isinstance(output, ModelSpeech) or not isinstance(output.raw_output, str):
            raise TypeError("Speech adapter must return ModelSpeech with text output")
        if output.request is not None:
            if not isinstance(output.request, dict):
                raise TypeError("Speech request provenance must be an object")
            record["model_request"] = copy.deepcopy(output.request)
        record["raw_output"] = output.raw_output
    except Exception:
        # Provider/adapter exception bodies may contain credentials. Record only
        # a stable failure code; the committed action is still successful.
        return record
    try:
        record["text"] = parse_speech(record["raw_output"])
    except (ValueError, TypeError, RecursionError):
        record["code"] = "INVALID_OUTPUT"
        return record
    record.update(status="generated", code="OK")
    return record


def verify_speech_record(record: Dict[str, Any], before: Dict[str, Any],
                         context: Dict[str, Any], action: Action, fallback: str) -> None:
    """Replay display records without models; success is not semantic approval."""
    fields = {"status", "code", "text", "context", "raw_output", "semantic_validation"}
    if not isinstance(record, dict) or not fields <= set(record) <= fields | {"model_request"}:
        raise ValueError("Invalid dialogue record schema")
    if record["semantic_validation"] != "not_performed":
        raise ValueError("Dialogue cannot claim semantic validation")
    if before["facts"][action.topic]["secret"]:
        expected = {"status": "skipped", "code": "SECRET_TOPIC", "text": fallback,
                    "context": {}, "raw_output": None, "semantic_validation": "not_performed"}
        if record != expected:
            raise ValueError("Secret dialogue must retain its fixed template")
        return
    if record["context"] != build_speech_context(before, context, action, fallback):
        raise ValueError("Dialogue context mismatch")
    if "model_request" in record and not isinstance(record["model_request"], dict):
        raise ValueError("Invalid dialogue request provenance")
    if record["status"] == "generated" and record["code"] == "OK":
        if not isinstance(record["raw_output"], str) or record["text"] != parse_speech(record["raw_output"]):
            raise ValueError("Generated dialogue differs from recorded output")
        return
    if record["status"] != "fallback" or record["text"] != fallback:
        raise ValueError("Invalid dialogue fallback")
    if record["code"] == "MODEL_ERROR" and record["raw_output"] is None and "model_request" not in record:
        return
    if record["code"] == "INVALID_OUTPUT" and isinstance(record["raw_output"], str):
        try:
            parse_speech(record["raw_output"])
        except (ValueError, TypeError, RecursionError):
            return
    raise ValueError("Dialogue fallback reason mismatch")
