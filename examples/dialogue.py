"""Offline scripted dialogue demonstration; not a model-quality evaluation."""

import json
from typing import Any, Dict

from loom_npc import Runtime
from loom_npc.models import ModelError
from loom_npc.models.speech import ModelSpeech
from loom_npc.replay import export_jsonl


class ScriptedSpeechAdapter:
    """Return fixed test outputs through the optional speech adapter boundary."""

    def __init__(self) -> None:
        self.calls = 0

    def generate_speech(self, context: Dict[str, Any]) -> ModelSpeech:
        """Demonstrate memory input, then a safe failure with fixed fallback."""
        self.calls += 1
        if self.calls > 1:
            raise ModelError("离线脚本模拟生成失败。")
        text = "旅人，愿今夜的灯火照亮你的归途。"
        if context["memories"]:
            text = "信已送到。愿今夜的灯火照亮你的归途。"
        return ModelSpeech(json.dumps({"text": text}, ensure_ascii=False))


def main() -> None:
    """Emit generated, fallback, rejected and secret-template trace records."""
    runtime = Runtime(speech_adapter=ScriptedSpeechAdapter())
    for actor, text in [("mara", "秘密"), ("player", "交信"),
                        ("mara", "你好，今晚有点冷"), ("mara", "你好"), ("mara", "秘密")]:
        runtime.step(actor, text)
    print(export_jsonl(runtime.traces), end="")


if __name__ == "__main__":
    main()
