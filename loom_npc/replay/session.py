"""Opt-in local session files, anchored to one configured initial world."""

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from ..core.runtime import Runtime
from ..core.types import WorldState
from ..models import LLMAdapter
from . import export_jsonl


class SessionStore:
    """Save a counted JSONL history and restore only after complete replay.

    One process owns the path. The header also represents an empty session and
    detects missing complete trace lines; it is not a cryptographic signature.
    """

    def __init__(self, path: Path, initial: WorldState):
        self.path = Path(path)
        self.initial = WorldState.from_dict(initial.to_dict())

    def load(self, adapter: Optional[LLMAdapter] = None) -> Runtime:
        """Read an existing file; corruption never falls back to a new world."""
        text = self.path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines:
            raise ValueError("会话文件缺少头记录")
        header = json.loads(lines[0])
        fields = {"format", "version", "initial", "trace_count"}
        if not isinstance(header, dict) or set(header) != fields:
            raise ValueError("会话头记录字段无效")
        if header["format"] != "loom-npc-session" or type(header["version"]) is not int or header["version"] != 1:
            raise ValueError("不支持的会话文件版本")
        if header["initial"] != self.initial.to_dict():
            raise ValueError("会话初始世界与当前配置不一致")
        count = header["trace_count"]
        if type(count) is not int or count < 0 or count != sum(bool(line.strip()) for line in lines[1:]):
            raise ValueError("会话 trace 数量不一致，记录可能已截断")
        if count == 0:
            return Runtime(WorldState.from_dict(header["initial"]), adapter=adapter)
        return Runtime.from_jsonl("\n".join(lines[1:]), adapter=adapter, initial=self.initial)

    def save(self, runtime: Runtime) -> None:
        """Validate, fsync, and atomically replace the file before acknowledging."""
        traces = export_jsonl(runtime.traces)
        checked = (Runtime.from_jsonl(traces, initial=self.initial) if runtime.traces
                   else Runtime(WorldState.from_dict(self.initial.to_dict())))
        if checked.world.to_dict() != runtime.world.to_dict():
            raise ValueError("当前世界与会话 trace 重算结果不一致")
        header = {"format": "loom-npc-session", "version": 1,
                  "initial": self.initial.to_dict(), "trace_count": len(runtime.traces)}
        content = json.dumps(header, ensure_ascii=False, sort_keys=True) + "\n" + traces
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Keep temporary files beside the target so os.replace is atomic. On
        # failure retain the temporary file for diagnosis; never use it to load.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                         prefix="." + self.path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
