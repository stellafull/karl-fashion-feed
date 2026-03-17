"""OpenAI-compatible structured output and batch client."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel
from pydantic import ValidationError

from backend.app.config.llm_config import ModelConfig

SchemaT = TypeVar("SchemaT", bound=BaseModel)
ClientFactory = Callable[[ModelConfig], Any]

_BATCH_ENDPOINT = "/v1/chat/completions"
_BATCH_COMPLETION_WINDOW = "24h"
_BATCH_REQUESTS_PER_JOB = max(int(os.getenv("LLM_BATCH_REQUESTS_PER_JOB", "100")), 1)
_BATCH_POLL_SECONDS = max(float(os.getenv("LLM_BATCH_POLL_SECONDS", "5")), 0.5)
_BATCH_TIMEOUT_SECONDS = max(int(os.getenv("LLM_BATCH_TIMEOUT_SECONDS", "1800")), 1)
_TERMINAL_BATCH_STATUSES = {"completed", "failed", "expired", "cancelled"}


@dataclass(frozen=True)
class BatchChatRequest:
    custom_id: str
    messages: list[dict[str, Any]]


@dataclass(frozen=True)
class BatchChatResult:
    custom_id: str
    value: BaseModel | None = None
    error: str | None = None


class BatchJobFailedError(RuntimeError):
    def __init__(
        self,
        *,
        batch_id: str | None,
        status: str | None,
        message: str,
    ) -> None:
        super().__init__(message)
        self.batch_id = batch_id
        self.status = status


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        client_factory: ClientFactory | None = None,
        max_retries: int = 2,
    ) -> None:
        self._client_factory = client_factory or self._build_default_client
        self._max_retries = max(max_retries, 0)

    def complete_json(
        self,
        *,
        model_config: ModelConfig,
        messages: list[dict[str, Any]],
        schema: type[SchemaT],
    ) -> SchemaT:
        client = self._client_factory(model_config)
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = client.chat.completions.create(
                    model=model_config.model_name,
                    temperature=model_config.temperature,
                    response_format={"type": "json_object"},
                    messages=messages,
                )
                payload = _extract_payload_from_chat_body(_to_mapping(response))
                return schema.model_validate(payload)
            except Exception as exc:  # pragma: no cover - exercised via integration stubs
                last_error = exc
                if attempt >= self._max_retries or not _is_retryable_exception(exc):
                    raise

        assert last_error is not None
        raise last_error

    def complete_json_batch(
        self,
        *,
        model_config: ModelConfig,
        requests: list[BatchChatRequest],
        schema: type[SchemaT],
        metadata: dict[str, str] | None = None,
        ) -> dict[str, BatchChatResult]:
        if not requests:
            return {}
        if not self.supports_batch(model_config):
            raise BatchJobFailedError(
                batch_id=None,
                status="unsupported",
                message=f"batch API disabled for base_url={model_config.base_url}",
            )

        results: dict[str, BatchChatResult] = {}
        for chunk in _chunked(requests, _BATCH_REQUESTS_PER_JOB):
            results.update(
                self._run_batch_job(
                    model_config=model_config,
                    requests=chunk,
                    schema=schema,
                    metadata=metadata,
                )
            )
        return results

    def _run_batch_job(
        self,
        *,
        model_config: ModelConfig,
        requests: list[BatchChatRequest],
        schema: type[SchemaT],
        metadata: dict[str, str] | None,
    ) -> dict[str, BatchChatResult]:
        client = self._client_factory(model_config)
        input_path = self._write_jsonl_file(model_config=model_config, requests=requests)
        try:
            with input_path.open("rb") as handle:
                uploaded_file = client.files.create(file=handle, purpose="batch")

            batch_kwargs: dict[str, Any] = {
                "completion_window": _BATCH_COMPLETION_WINDOW,
                "endpoint": _BATCH_ENDPOINT,
                "input_file_id": uploaded_file.id,
            }
            if metadata:
                batch_kwargs["metadata"] = metadata

            batch = client.batches.create(**batch_kwargs)
            batch = self._poll_batch(client, batch_id=batch.id)
            if getattr(batch, "status", None) != "completed":
                raise BatchJobFailedError(
                    batch_id=getattr(batch, "id", None),
                    status=getattr(batch, "status", None),
                    message=_batch_failure_message(batch),
                )

            output_lines = self._read_jsonl_response(client, getattr(batch, "output_file_id", None))
            error_lines = self._read_jsonl_response(client, getattr(batch, "error_file_id", None))
            return self._parse_batch_results(
                requests=requests,
                output_lines=output_lines,
                error_lines=error_lines,
                schema=schema,
            )
        finally:
            if input_path.exists():
                input_path.unlink()

    def _poll_batch(self, client: Any, *, batch_id: str) -> Any:
        deadline = time.monotonic() + _BATCH_TIMEOUT_SECONDS
        batch = client.batches.retrieve(batch_id)
        while getattr(batch, "status", None) not in _TERMINAL_BATCH_STATUSES:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"batch job timed out: {batch_id}")
            time.sleep(_BATCH_POLL_SECONDS)
            batch = client.batches.retrieve(batch_id)
        return batch

    def _read_jsonl_response(self, client: Any, file_id: str | None) -> list[dict[str, Any]]:
        if not file_id:
            return []
        response = client.files.content(file_id)
        text = response.text if hasattr(response, "text") else response.read().decode("utf-8")
        lines = []
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            lines.append(json.loads(stripped))
        return lines

    def _parse_batch_results(
        self,
        *,
        requests: list[BatchChatRequest],
        output_lines: list[dict[str, Any]],
        error_lines: list[dict[str, Any]],
        schema: type[SchemaT],
    ) -> dict[str, BatchChatResult]:
        parsed: dict[str, BatchChatResult] = {}

        for line in output_lines:
            custom_id = str(line.get("custom_id") or "")
            if not custom_id:
                continue
            try:
                response_body = _response_body_from_batch_line(line)
                payload = _extract_payload_from_chat_body(response_body)
                parsed[custom_id] = BatchChatResult(
                    custom_id=custom_id,
                    value=schema.model_validate(payload),
                )
            except Exception as exc:
                parsed[custom_id] = BatchChatResult(
                    custom_id=custom_id,
                    error=f"{exc.__class__.__name__}: {exc}",
                )

        for line in error_lines:
            custom_id = str(line.get("custom_id") or "")
            if not custom_id or custom_id in parsed:
                continue
            parsed[custom_id] = BatchChatResult(
                custom_id=custom_id,
                error=_error_message_from_batch_line(line),
            )

        for request in requests:
            if request.custom_id not in parsed:
                parsed[request.custom_id] = BatchChatResult(
                    custom_id=request.custom_id,
                    error="missing batch result",
                )

        return parsed

    def _write_jsonl_file(
        self,
        *,
        model_config: ModelConfig,
        requests: list[BatchChatRequest],
    ) -> Path:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".jsonl",
            delete=False,
        ) as handle:
            for request in requests:
                record = {
                    "custom_id": request.custom_id,
                    "method": "POST",
                    "url": _BATCH_ENDPOINT,
                    "body": {
                        "model": model_config.model_name,
                        "temperature": model_config.temperature,
                        "response_format": {"type": "json_object"},
                        "messages": request.messages,
                    },
                }
                handle.write(json.dumps(record, ensure_ascii=False))
                handle.write("\n")
            return Path(handle.name)

    @staticmethod
    def _build_default_client(model_config: ModelConfig) -> Any:
        if not model_config.api_key:
            raise ValueError(
                f"missing API key for {model_config.model_name} "
                f"(expected {model_config.api_key_env} or OPENAI_API_KEY)"
            )

        from openai import OpenAI

        return OpenAI(
            api_key=model_config.api_key,
            base_url=model_config.base_url,
            timeout=model_config.timeout_seconds,
        )

    @staticmethod
    def supports_batch(model_config: ModelConfig) -> bool:
        base_url = (model_config.base_url or "").lower()
        if "openrouter.ai" in base_url:
            return False
        return True


def _extract_payload_from_chat_body(body: dict[str, Any]) -> dict[str, Any]:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("chat response missing choices")
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(getattr(item, "text", ""))
            for item in content
        )
    else:
        text = str(content or "")
    return json.loads(_strip_json_fence(text))


def _response_body_from_batch_line(line: dict[str, Any]) -> dict[str, Any]:
    response = line.get("response")
    if not isinstance(response, dict):
        raise ValueError("batch line missing response")
    body = response.get("body")
    if not isinstance(body, dict):
        raise ValueError("batch response missing body")
    return body


def _error_message_from_batch_line(line: dict[str, Any]) -> str:
    error = line.get("error")
    if isinstance(error, dict):
        parts = [str(error.get("code") or "").strip(), str(error.get("message") or "").strip()]
        message = ": ".join(part for part in parts if part)
        if message:
            return message
    if error:
        return str(error)
    return "unknown batch error"


def _to_mapping(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        dumped = response.model_dump()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(response, "to_dict"):
        dumped = response.to_dict()
        if isinstance(dumped, dict):
            return dumped
    return {}


def _strip_json_fence(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        lines = cleaned.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return cleaned


def _chunked(values: list[BatchChatRequest], size: int) -> list[list[BatchChatRequest]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _batch_failure_message(batch: Any) -> str:
    status = getattr(batch, "status", None) or "unknown"
    errors = getattr(batch, "errors", None)
    if errors is None:
        return f"batch job did not complete successfully: status={status}"

    data = getattr(errors, "data", None)
    if not data:
        return f"batch job did not complete successfully: status={status}"

    messages: list[str] = []
    for item in data:
        code = str(getattr(item, "code", "") or "").strip()
        message = str(getattr(item, "message", "") or "").strip()
        line = getattr(item, "line", None)
        parts = [part for part in [code, message] if part]
        if line is not None:
            parts.insert(0, f"line={line}")
        rendered = " | ".join(parts)
        if rendered:
            messages.append(rendered)
    if not messages:
        return f"batch job did not complete successfully: status={status}"
    return f"batch job did not complete successfully: status={status}; errors={'; '.join(messages)}"


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, BatchJobFailedError):
        return False
    if isinstance(exc, (JSONDecodeError, ValidationError)):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        if status_code == 429 or status_code >= 500:
            return True
        return False

    try:  # pragma: no cover - import availability depends on runtime
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )

        if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)):
            return True
        if isinstance(exc, APIStatusError):
            retry_status = getattr(exc, "status_code", None)
            return isinstance(retry_status, int) and (retry_status == 429 or retry_status >= 500)
    except Exception:
        pass

    return False
