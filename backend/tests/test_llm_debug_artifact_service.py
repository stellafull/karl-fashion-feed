from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.service.llm_debug_artifact_service import LlmDebugArtifactRecorder


class LlmDebugArtifactRecorderTest(unittest.TestCase):
    def test_record_writes_prompt_and_response_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = LlmDebugArtifactRecorder(base_dir=Path(tmpdir), enabled=True)

            prompt_path, response_path = recorder.record(
                run_id="run-1",
                stage="story_cluster",
                object_key="frame-f1",
                prompt_text='{"frames":[]}',
                response_text='{"groups":[]}',
            )

            self.assertTrue(prompt_path.exists())
            self.assertTrue(response_path.exists())
