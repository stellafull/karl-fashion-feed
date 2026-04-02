"""LangChain ChatOpenAI model factory."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from backend.app.config.llm_config import Configuration


def build_story_model(configuration: Configuration):
    """Build a story summarization model."""
    return _build_model(configuration)


def build_rag_model(configuration: Configuration):
    """Build a RAG chat model."""
    return _build_model(configuration)


def _build_model(configuration: Configuration):
    model = ChatOpenAI(
        model=configuration.model,
        api_key=configuration.api_key,
        base_url=configuration.base_url,
        temperature=configuration.temperature,
        max_completion_tokens=configuration.max_completion_tokens,
        timeout=configuration.timeout_seconds,
        max_retries=0,
        use_responses_api=True,
    )
    return model.with_retry(stop_after_attempt=configuration.max_structured_output_retries)
