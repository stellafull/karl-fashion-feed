#!/usr/bin/env python3
"""Benchmark the configured DashScope OpenAI-compatible chat endpoint."""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import tiktoken
from openai import APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.config.llm_config import (
    IMAGE_ANALYSIS_MODEL_CONFIG,
    RAG_CHAT_MODEL_CONFIG,
    STORY_SUMMARIZATION_MODEL_CONFIG,
    ModelConfig,
)

ENCODING = tiktoken.get_encoding("cl100k_base")
SYSTEM_PROMPT = "You are a helpful assistant."
USER_PREFIX = "请用简体中文回答："
SMALL_PROBE_MAX_TOKENS = 8
TOKEN_SPEED_MAX_TOKENS = 512
LIMIT_STATUS_CODES = frozenset({400, 413})
CAPACITY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
DEFAULT_MAX_CONTEXT_PROBE_TOKENS = 262_144
DEFAULT_MAX_COT_PROBE_TOKENS = 65_536
DEFAULT_MAX_OUTPUT_PROBE_TOKENS = 65_536


@dataclass(frozen=True)
class BenchmarkSettings:
    """Resolved runtime settings for a benchmark run."""

    profile: str
    model_name: str
    base_url: str
    api_key: str
    timeout_seconds: int
    duration_seconds: int
    context_step_tokens: int
    output_step_tokens: int
    max_concurrency_upper_bound: int
    max_context_probe_tokens: int
    max_cot_probe_tokens: int
    max_output_probe_tokens: int


@dataclass(frozen=True)
class ChatResult:
    """Normalized chat completion result."""

    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    finish_reason: str | None


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Benchmark the current DashScope OpenAI-compatible chat endpoint.",
    )
    parser.add_argument(
        "--profile",
        choices=("story", "rag", "image"),
        default="story",
        help="Reuse the project's configured model profile.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the model name from the selected profile.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override the OpenAI-compatible base URL from the selected profile.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Override the API key from the selected profile.",
    )
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=60,
        help="Duration for the single-concurrency throughput probe.",
    )
    parser.add_argument(
        "--context-step-tokens",
        type=int,
        default=1024,
        help="Initial token step used when probing context length.",
    )
    parser.add_argument(
        "--output-step-tokens",
        type=int,
        default=1024,
        help="Initial token step used when probing output limits.",
    )
    parser.add_argument(
        "--max-concurrency-upper-bound",
        type=int,
        default=128,
        help="Upper bound for the binary-search concurrency probe.",
    )
    parser.add_argument(
        "--max-context-probe-tokens",
        type=int,
        default=DEFAULT_MAX_CONTEXT_PROBE_TOKENS,
        help="Hard upper bound for the context-limit probe.",
    )
    parser.add_argument(
        "--max-cot-probe-tokens",
        type=int,
        default=DEFAULT_MAX_COT_PROBE_TOKENS,
        help="Hard upper bound for the visible reasoning-output probe.",
    )
    parser.add_argument(
        "--max-output-probe-tokens",
        type=int,
        default=DEFAULT_MAX_OUTPUT_PROBE_TOKENS,
        help="Hard upper bound for the configured max_tokens probe.",
    )
    return parser


def resolve_settings(args: argparse.Namespace) -> BenchmarkSettings:
    """Resolve runtime settings from CLI args and project config."""
    profile_config = resolve_profile_config(args.profile)
    model_name = (args.model or profile_config.model_name).strip()
    base_url = (args.base_url or profile_config.base_url or "").strip()
    api_key = (args.api_key or profile_config.api_key or "").strip()

    if not model_name:
        raise ValueError("missing model name")
    if not base_url:
        raise ValueError("missing base URL")
    if not api_key:
        raise ValueError(f"missing API key for {model_name}")

    validate_positive_int("duration_seconds", args.duration_seconds)
    validate_positive_int("context_step_tokens", args.context_step_tokens)
    validate_positive_int("output_step_tokens", args.output_step_tokens)
    validate_positive_int("max_concurrency_upper_bound", args.max_concurrency_upper_bound)
    validate_positive_int("max_context_probe_tokens", args.max_context_probe_tokens)
    validate_positive_int("max_cot_probe_tokens", args.max_cot_probe_tokens)
    validate_positive_int("max_output_probe_tokens", args.max_output_probe_tokens)

    return BenchmarkSettings(
        profile=args.profile,
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=profile_config.timeout_seconds,
        duration_seconds=args.duration_seconds,
        context_step_tokens=args.context_step_tokens,
        output_step_tokens=args.output_step_tokens,
        max_concurrency_upper_bound=args.max_concurrency_upper_bound,
        max_context_probe_tokens=args.max_context_probe_tokens,
        max_cot_probe_tokens=args.max_cot_probe_tokens,
        max_output_probe_tokens=args.max_output_probe_tokens,
    )


def validate_positive_int(name: str, value: int) -> None:
    """Validate that a numeric CLI argument is strictly positive."""
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")


def resolve_profile_config(profile: str) -> ModelConfig:
    """Map a benchmark profile to the project's configured model config."""
    if profile == "story":
        return STORY_SUMMARIZATION_MODEL_CONFIG
    if profile == "rag":
        return RAG_CHAT_MODEL_CONFIG
    if profile == "image":
        return IMAGE_ANALYSIS_MODEL_CONFIG
    raise ValueError(f"unsupported profile: {profile}")


async def chat(
    client: AsyncOpenAI,
    settings: BenchmarkSettings,
    message: str,
    *,
    max_tokens: int | None = None,
) -> ChatResult:
    """Send one chat completion request through the configured endpoint."""
    response = await client.chat.completions.create(
        model=settings.model_name,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ],
        max_tokens=max_tokens,
    )
    if response.usage is None:
        raise ValueError("chat completion response missing usage")

    first_choice = response.choices[0]
    return ChatResult(
        content=extract_content(first_choice.message.content),
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        total_tokens=response.usage.total_tokens,
        finish_reason=first_choice.finish_reason,
    )


def extract_content(content: str | list[object] | None) -> str:
    """Extract text content from the first choice."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if getattr(item, "type", None) == "text":
                text = getattr(item, "text", "")
                if text:
                    text_parts.append(text)
        return "".join(text_parts)
    return ""


def token_len(text: str) -> int:
    """Approximate token length using the OpenAI-compatible tokenizer."""
    return len(ENCODING.encode(text))


def build_repeated_prompt(*, base_text: str, target_tokens: int) -> str:
    """Build a long prompt by repeating a short unit string."""
    unit_tokens = max(token_len(base_text), 1)
    repeat_count = max(math.ceil(target_tokens / unit_tokens), 1)
    return base_text * repeat_count


def is_limit_error(error: Exception) -> bool:
    """Return whether the exception indicates a hard size limit."""
    return isinstance(error, APIStatusError) and error.status_code in LIMIT_STATUS_CODES


def is_capacity_error(error: Exception) -> bool:
    """Return whether the exception indicates transient capacity pressure."""
    if isinstance(error, (RateLimitError, APITimeoutError)):
        return True
    return isinstance(error, APIStatusError) and error.status_code in CAPACITY_STATUS_CODES


async def find_limit(
    *,
    label: str,
    start_value: int,
    upper_bound: int,
    probe: Callable[[int], Awaitable[None]],
) -> int:
    """Find the largest accepted value using exponential search plus binary search."""
    log_stage(f"{label}: exponential probe start (start={start_value}, upper={upper_bound})")

    current = start_value
    last_success = 0
    first_failure: int | None = None

    while current <= upper_bound:
        try:
            await probe(current)
            last_success = current
            log_stage(f"{label}: accepted value={current}")
        except Exception as exc:
            if not is_limit_error(exc):
                raise
            first_failure = current
            log_stage(f"{label}: rejected value={current} ({exc.__class__.__name__})")
            break

        if current == upper_bound:
            return current
        current = min(current * 2, upper_bound)

    if last_success == 0:
        raise ValueError(f"{label} failed at the initial probe value {start_value}")

    if first_failure is None:
        return last_success

    low = last_success + 1
    high = first_failure - 1
    best = last_success
    log_stage(f"{label}: binary search range=({low}, {high})")

    while low <= high:
        mid = (low + high) // 2
        try:
            await probe(mid)
            best = mid
            low = mid + 1
        except Exception as exc:
            if not is_limit_error(exc):
                raise
            high = mid - 1

    log_stage(f"{label}: final={best}")
    return best


async def find_context_limit(
    client: AsyncOpenAI,
    settings: BenchmarkSettings,
) -> int:
    """Probe the approximate maximum accepted prompt length."""
    base_prompt = USER_PREFIX + "上下文填充。"

    async def probe(target_tokens: int) -> None:
        prompt = build_repeated_prompt(base_text=base_prompt, target_tokens=target_tokens)
        await chat(
            client,
            settings,
            prompt,
            max_tokens=SMALL_PROBE_MAX_TOKENS,
        )

    return await find_limit(
        label="max_context_tokens",
        start_value=settings.context_step_tokens,
        upper_bound=settings.max_context_probe_tokens,
        probe=probe,
    )


async def find_cot_limit(
    client: AsyncOpenAI,
    settings: BenchmarkSettings,
) -> int:
    """Probe the largest accepted visible reasoning-style output limit."""
    prompt = (
        USER_PREFIX
        + "请按编号逐步展开完整分析，每一步都写成独立段落，不要总结，不要提前结束，直到被截断。"
    )

    async def probe(target_tokens: int) -> None:
        await chat(client, settings, prompt, max_tokens=target_tokens)

    return await find_limit(
        label="max_cot_tokens",
        start_value=settings.output_step_tokens,
        upper_bound=settings.max_cot_probe_tokens,
        probe=probe,
    )


async def find_output_limit(
    client: AsyncOpenAI,
    settings: BenchmarkSettings,
) -> int:
    """Probe the largest accepted configured max_tokens value."""
    prompt = USER_PREFIX + "只输出“甲”字并持续重复，不要解释，不要换行，不要主动停止。"

    async def probe(target_tokens: int) -> None:
        await chat(client, settings, prompt, max_tokens=target_tokens)

    return await find_limit(
        label="max_output_tokens_cfg",
        start_value=settings.output_step_tokens,
        upper_bound=settings.max_output_probe_tokens,
        probe=probe,
    )


async def default_output_limit(
    client: AsyncOpenAI,
    settings: BenchmarkSettings,
) -> int:
    """Measure the default completion length when max_tokens is omitted."""
    result = await chat(
        client,
        settings,
        USER_PREFIX + "请持续输出长文本，不要解释，不要总结，不要主动停止，直到被截断。",
    )
    log_stage(
        "max_output_tokens_default: "
        f"completion_tokens={result.completion_tokens}, finish_reason={result.finish_reason}"
    )
    return result.completion_tokens


async def throughput_probe(
    client: AsyncOpenAI,
    settings: BenchmarkSettings,
) -> float:
    """Measure average single-concurrency requests per second."""
    stop_at = time.monotonic() + settings.duration_seconds
    request_count = 0
    while time.monotonic() < stop_at:
        await chat(client, settings, USER_PREFIX + "返回 OK")
        request_count += 1
    return request_count / settings.duration_seconds


async def token_speed(
    client: AsyncOpenAI,
    settings: BenchmarkSettings,
) -> float:
    """Measure completion token throughput for one long response."""
    started_at = time.monotonic()
    result = await chat(
        client,
        settings,
        USER_PREFIX + "请输出不少于 256 个汉字。",
        max_tokens=TOKEN_SPEED_MAX_TOKENS,
    )
    elapsed_seconds = time.monotonic() - started_at
    if elapsed_seconds <= 0:
        raise ValueError("elapsed time must be greater than 0")
    return result.completion_tokens / elapsed_seconds


async def max_concurrency(
    client: AsyncOpenAI,
    settings: BenchmarkSettings,
) -> int:
    """Probe the maximum concurrency that finishes without 429 or 5xx."""

    async def worker(index: int) -> None:
        await chat(client, settings, f"{USER_PREFIX}并发测试 #{index}")

    low = 1
    high = settings.max_concurrency_upper_bound
    best = 1

    while low <= high:
        mid = (low + high) // 2
        log_stage(f"max_concurrency: probing concurrency={mid}")
        tasks = [worker(index) for index in range(mid)]
        try:
            await asyncio.gather(*tasks)
            best = mid
            low = mid + 1
        except Exception as exc:
            if not is_capacity_error(exc):
                raise
            high = mid - 1

    log_stage(f"max_concurrency: final={best}")
    return best


def log_stage(message: str) -> None:
    """Print a progress log line."""
    print(f"[benchmark] {message}", flush=True)


async def run_benchmark(settings: BenchmarkSettings) -> None:
    """Run the benchmark suite and print the report."""
    client = AsyncOpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
    )

    async with client:
        log_stage(f"profile={settings.profile}")
        log_stage(f"model={settings.model_name}")
        log_stage(f"base_url={settings.base_url}")

        context_limit = await find_context_limit(client, settings)
        cot_limit = await find_cot_limit(client, settings)
        configured_output_limit = await find_output_limit(client, settings)
        default_output_tokens = await default_output_limit(client, settings)

        log_stage(f"throughput_rps: measuring for {settings.duration_seconds} seconds")
        requests_per_second = await throughput_probe(client, settings)

        log_stage("generation_tps: measuring")
        tokens_per_second = await token_speed(client, settings)

        concurrency_limit = await max_concurrency(client, settings)

    print("\n=== Benchmark Report ===")
    print(f"profile                        : {settings.profile}")
    print(f"model                          : {settings.model_name}")
    print(f"base_url                       : {settings.base_url}")
    print(f"1. max_context_tokens          : {context_limit}")
    print(f"2. max_cot_tokens              : {cot_limit}")
    print(f"3. max_output_tokens_cfg       : {configured_output_limit}")
    print(f"4. max_output_tokens_default   : {default_output_tokens}")
    print(f"5. throughput_rps              : {requests_per_second:.2f}")
    print(f"6. generation_tps              : {tokens_per_second:.2f}")
    print(f"7. max_concurrency             : {concurrency_limit}")



async def main() -> int:
    """CLI entrypoint."""
    args = build_parser().parse_args()
    settings = resolve_settings(args)
    await run_benchmark(settings)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0)
