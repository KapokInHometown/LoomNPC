"""Optional dialogue adapter, separate from structured action proposals."""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol


@dataclass(frozen=True)
class ModelSpeech:
    """Untrusted JSON dialogue output; never a world mutation or knowledge source."""

    raw_output: str
    request: Optional[Dict[str, Any]] = None


class SpeechAdapter(Protocol):
    """Generate display text from a separately filtered dialogue context."""

    def generate_speech(self, context: Dict[str, Any]) -> ModelSpeech:
        """Return JSON containing only a text field, without world access."""
        ...
