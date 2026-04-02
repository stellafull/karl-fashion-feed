"""LangChain ChatOpenAI model factory."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from backend.app.config.llm_config import Configuration


def build_story_model(configuration: Configuration):
    """Build a story summarization model."""
    return _build_model(
        configuration=configuration,
        model_name=configuration.story_summarization_model,
        temperature=configuration.story_summarization_temperature,
        max_completion_tokens=configuration.story_summarization_model_max_tokens,
        timeout_seconds=configuration.story_summarization_timeout_seconds,
    )


def build_rag_model(configuration: Configuration):
    """Build a RAG chat model."""
    return _build_model(
        configuration=configuration,
        model_name=configuration.rag_model,
        temperature=configuration.rag_temperature,
        max_completion_tokens=configuration.rag_model_max_tokens,
        timeout_seconds=configuration.rag_timeout_seconds,
    )


def _build_model(
    *,
    configuration: Configuration,
    model_name: str,
    temperature: float,
    max_completion_tokens: int,
    timeout_seconds: int,
):
    model = ChatOpenAI(
        model=model_name,
        api_key=configuration.api_key,
        base_url=configuration.base_url,
        temperature=temperature,
        max_completion_tokens=max_completion_tokens,
        timeout=timeout_seconds,
        max_retries=0,
        use_responses_api=True,
    )
    return model.with_retry(stop_after_attempt=configuration.max_structured_output_retries)
