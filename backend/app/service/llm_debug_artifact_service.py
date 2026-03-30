"""Service for recording LLM debug artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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

        target_dir = self.base_dir / run_id / stage / object_key
        target_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = target_dir / "prompt.json"
        response_path = target_dir / "response.json"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        response_path.write_text(response_text, encoding="utf-8")
        return prompt_path, response_path
