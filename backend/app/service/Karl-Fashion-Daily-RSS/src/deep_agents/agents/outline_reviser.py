"""Outline reviser node — revises the research section outline based on gathered evidence.

Reads research_goal, sections, hypothesis_evidence, and outline_revision_count from state,
calls the LLM with structured output to produce a RevisedOutline, then returns the
updated sections, outline_status, and incremented outline_revision_count.
"""

import json

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from deep_agents.configuration import Configuration
from deep_agents.prompts import outline_reviser_prompt
from deep_agents.schemas import RevisedOutline
from deep_agents.state import ResearchState
from deep_agents.utils import _strip_ctrl, get_api_key_for_model, STRUCTURED_OUTPUT_RETRY_EXCEPTIONS


async def outline_reviser_node(state: ResearchState, config: RunnableConfig) -> dict:
    """Revise the research section outline based on hypothesis evidence collected so far."""
    configurable = Configuration.from_runnable_config(config)

    model = (
        init_chat_model(
            model=configurable.research_model,
            max_tokens=configurable.research_model_max_tokens,
            api_key=get_api_key_for_model(configurable.research_model, config),
            base_url=configurable.openai_compatible_base_url,
            max_retries=configurable.provider_max_retries,
            disable_streaming=True,
        )
        .with_structured_output(RevisedOutline)
        .with_retry(
            retry_if_exception_type=STRUCTURED_OUTPUT_RETRY_EXCEPTIONS,
            wait_exponential_jitter=True,
            stop_after_attempt=configurable.max_structured_output_retries,
        )
    )

    research_goal = state.get("research_goal", "")
    sections = state.get("sections", [])
    hypothesis_evidence = state.get("hypothesis_evidence", [])
    outline_revision_count = state.get("outline_revision_count", 0)

    # Only use the last 20 items of hypothesis_evidence to keep prompt size manageable
    recent_evidence = hypothesis_evidence[-20:]

    prompt_text = outline_reviser_prompt.format(
        research_goal=research_goal,
        sections=json.dumps(sections, ensure_ascii=False),
        hypothesis_evidence=json.dumps(recent_evidence, ensure_ascii=False),
    )
    prompt_text = _strip_ctrl(prompt_text)

    result: RevisedOutline = await model.ainvoke([HumanMessage(content=prompt_text)])

    return {
        "sections": [s.model_dump() for s in result.sections],
        "outline_status": result.outline_status,
        "outline_revision_count": outline_revision_count + 1,
    }
