from __future__ import annotations

import json
import unittest

from backend.app.config.llm_config import ModelConfig
from backend.app.service.llm_client_service import BatchChatRequest, OpenAICompatibleClient


class StubSchema:
    @staticmethod
    def model_validate(payload):
        return payload


class StrictSchema:
    @staticmethod
    def model_validate(payload):
        if not isinstance(payload, dict) or "ok" not in payload:
            raise ValueError("invalid payload")
        return payload


class StubBinaryResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class StubFilesAPI:
    def __init__(self) -> None:
        self.uploaded_text = ""
        self.contents: dict[str, str] = {}

    def create(self, *, file, purpose: str):
        self.uploaded_text = file.read().decode("utf-8")
        return type("UploadedFile", (), {"id": "file-input"})()

    def content(self, file_id: str):
        return StubBinaryResponse(self.contents[file_id])


class StubBatchesAPI:
    def __init__(self) -> None:
        self.create_kwargs: dict[str, object] = {}
        self.retrieve_calls = 0

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return type("Batch", (), {"id": "batch-1"})()

    def retrieve(self, batch_id: str):
        self.retrieve_calls += 1
        if self.retrieve_calls == 1:
            return type("Batch", (), {"id": batch_id, "status": "in_progress"})()
        return type(
            "Batch",
            (),
            {
                "id": batch_id,
                "status": "completed",
                "output_file_id": "file-output",
                "error_file_id": "file-error",
            },
        )()


class StubClient:
    def __init__(self) -> None:
        self.files = StubFilesAPI()
        self.batches = StubBatchesAPI()

        output_line = {
            "custom_id": "article:1",
            "response": {
                "status_code": 200,
                "body": {
                    "choices": [
                        {
                            "message": {
                                "content": '{"ok": true, "id": 1}',
                            }
                        }
                    ]
                },
            },
        }
        error_line = {
            "custom_id": "article:2",
            "error": {"code": "invalid_request", "message": "bad payload"},
        }
        self.files.contents["file-output"] = json.dumps(output_line, ensure_ascii=False) + "\n"
        self.files.contents["file-error"] = json.dumps(error_line, ensure_ascii=False) + "\n"


class StubChatCompletionsAPI:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def create(self, **_: object):
        self.calls += 1
        if not self._responses:
            raise RuntimeError("no stub response left")
        current = self._responses.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


class StubChatClient:
    def __init__(self, responses: list[object]) -> None:
        self.chat = type(
            "ChatAPI",
            (),
            {"completions": StubChatCompletionsAPI(responses)},
        )()


class LLMClientServiceTest(unittest.TestCase):
    def test_complete_json_retries_parse_errors(self) -> None:
        bad_response = {
            "choices": [
                {
                    "message": {
                        "content": "not-json",
                    }
                }
            ]
        }
        good_response = {
            "choices": [
                {
                    "message": {
                        "content": '{"ok": true}',
                    }
                }
            ]
        }
        stub_client = StubChatClient([bad_response, good_response])
        client = OpenAICompatibleClient(client_factory=lambda model_config: stub_client)
        model_config = ModelConfig(
            model_name="qwen-plus",
            temperature=0.0,
            api_key_env="TEST_API_KEY",
            base_url_env="TEST_BASE_URL",
        )

        result = client.complete_json(
            model_config=model_config,
            messages=[{"role": "user", "content": "hello"}],
            schema=StrictSchema,
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(stub_client.chat.completions.calls, 2)

    def test_complete_json_retries_transient_errors(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": '{"ok": true}',
                    }
                }
            ]
        }
        stub_client = StubChatClient([ConnectionError("temporary"), response])
        client = OpenAICompatibleClient(client_factory=lambda model_config: stub_client)
        model_config = ModelConfig(
            model_name="qwen-plus",
            temperature=0.0,
            api_key_env="TEST_API_KEY",
            base_url_env="TEST_BASE_URL",
        )

        result = client.complete_json(
            model_config=model_config,
            messages=[{"role": "user", "content": "hello"}],
            schema=StrictSchema,
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(stub_client.chat.completions.calls, 2)

    def test_complete_json_batch_uploads_jsonl_and_parses_results(self) -> None:
        stub_client = StubClient()
        client = OpenAICompatibleClient(client_factory=lambda model_config: stub_client)
        model_config = ModelConfig(
            model_name="qwen-plus",
            temperature=0.0,
            api_key_env="TEST_API_KEY",
            base_url_env="TEST_BASE_URL",
        )
        results = client.complete_json_batch(
            model_config=model_config,
            requests=[
                BatchChatRequest(
                    custom_id="article:1",
                    messages=[{"role": "user", "content": "first"}],
                ),
                BatchChatRequest(
                    custom_id="article:2",
                    messages=[{"role": "user", "content": "second"}],
                ),
            ],
            schema=StubSchema,
            metadata={"stage": "article_enrichment"},
        )
        uploaded_lines = [json.loads(line) for line in stub_client.files.uploaded_text.splitlines() if line]

        self.assertEqual(len(uploaded_lines), 2)
        self.assertEqual(uploaded_lines[0]["custom_id"], "article:1")
        self.assertEqual(uploaded_lines[0]["url"], "/v1/chat/completions")
        self.assertEqual(stub_client.batches.create_kwargs["endpoint"], "/v1/chat/completions")
        self.assertEqual(stub_client.batches.create_kwargs["metadata"], {"stage": "article_enrichment"})
        self.assertEqual(results["article:1"].value, {"ok": True, "id": 1})
        self.assertEqual(results["article:2"].error, "invalid_request: bad payload")


if __name__ == "__main__":
    unittest.main()
