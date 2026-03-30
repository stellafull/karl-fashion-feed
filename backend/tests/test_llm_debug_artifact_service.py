from __future__ import annotations

import hashlib
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

    def test_record_handles_long_object_key_with_deterministic_short_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = LlmDebugArtifactRecorder(base_dir=Path(tmpdir), enabled=True)
            long_object_key = "-".join(["123e4567-e89b-12d3-a456-426614174000"] * 20)

            prompt_path, response_path = recorder.record(
                run_id="run-1",
                stage="digest_report_writing",
                object_key=long_object_key,
                prompt_text='{"prompt":"x"}',
                response_text='{"response":"y"}',
            )

            self.assertTrue(prompt_path.exists())
            self.assertTrue(response_path.exists())
            self.assertLessEqual(len(prompt_path.parent.name), 120)
            expected_suffix = hashlib.sha1(long_object_key.encode("utf-8")).hexdigest()[:12]
            self.assertTrue(prompt_path.parent.name.endswith(f"-{expected_suffix}"))
