"""Service for recording LLM debug artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re

KARL_LLM_DEBUG_ARTIFACT_DIR_ENV = "KARL_LLM_DEBUG_ARTIFACT_DIR"
OBJECT_KEY_COMPONENT_MAX_LENGTH = 120
_SAFE_COMPONENT_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(slots=True)
class LlmDebugArtifactRecorder:
    """Record raw prompt/response artifacts for LLM debugging."""

    base_dir: Path | None
    enabled: bool = False

    def record(
        self,
        *,
        run_id: str,
        stage: str,
        object_key: str,
        prompt_text: str,
        response_text: str,
    ) -> tuple[Path, Path]:
        """Persist prompt/response payloads under the run directory."""
        if not self.enabled or self.base_dir is None:
            raise RuntimeError("LLM debug artifact recording is disabled")

        target_dir = self.base_dir / run_id / stage / self._normalize_object_key_component(object_key)
        target_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = target_dir / "prompt.json"
        response_path = target_dir / "response.json"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        response_path.write_text(response_text, encoding="utf-8")
        return prompt_path, response_path

    def _normalize_object_key_component(self, object_key: str) -> str:
        raw_key = object_key.strip() or "object"
        sanitized = _SAFE_COMPONENT_PATTERN.sub("-", raw_key).strip("-.")
        if not sanitized:
            sanitized = "object"
        if sanitized in {".", ".."}:
            sanitized = "object"

        needs_hash = sanitized != raw_key or len(sanitized) > OBJECT_KEY_COMPONENT_MAX_LENGTH
        if not needs_hash:
            return sanitized

        digest = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:12]
        prefix_max = OBJECT_KEY_COMPONENT_MAX_LENGTH - len(digest) - 1
        prefix = sanitized[: max(prefix_max, 1)].rstrip("-.")
        if not prefix:
            prefix = "object"
        return f"{prefix}-{digest}"


def build_llm_debug_artifact_recorder_from_env() -> LlmDebugArtifactRecorder:
    """Build recorder from KARL_LLM_DEBUG_ARTIFACT_DIR environment variable."""
    raw_base_dir = os.getenv(KARL_LLM_DEBUG_ARTIFACT_DIR_ENV)
    if not raw_base_dir:
        return LlmDebugArtifactRecorder(base_dir=None, enabled=False)
    return LlmDebugArtifactRecorder(base_dir=Path(raw_base_dir), enabled=True)
