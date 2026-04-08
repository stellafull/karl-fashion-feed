# src/deep_agents/agents/reviser.py
"""Reviser: apply targeted edits based on reviewer feedback."""

import json
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from deep_agents.configuration import Configuration
from deep_agents.prompts import reviser_prompt
from deep_agents.schemas import ReviserOutput
from deep_agents.state import ResearchState
from deep_agents.utils import _strip_ctrl, get_api_key_for_model, STRUCTURED_OUTPUT_RETRY_EXCEPTIONS


async def reviser_node(state: ResearchState, config: RunnableConfig) -> dict:
    """Apply targeted fixes from reviewer feedback; increment revision_count."""
    configurable = Configuration.from_runnable_config(config)
    model = (
        init_chat_model(
            model=configurable.final_report_model,
            max_tokens=configurable.final_report_model_max_tokens,
            api_key=get_api_key_for_model(configurable.final_report_model, config),
            base_url=configurable.openai_compatible_base_url,
            max_retries=configurable.provider_max_retries,
            disable_streaming=True,
        )
        .with_structured_output(ReviserOutput)
        .with_retry(
            retry_if_exception_type=STRUCTURED_OUTPUT_RETRY_EXCEPTIONS,
            wait_exponential_jitter=True,
            stop_after_attempt=configurable.max_structured_output_retries,
        )
    )

    prompt_text = reviser_prompt.format(
        full_report=state.get("full_report", ""),
        review_result=json.dumps(state.get("review_result", {}), ensure_ascii=False, indent=2),
    )
    prompt_text = _strip_ctrl(prompt_text)

    result: ReviserOutput = await model.ainvoke([HumanMessage(content=prompt_text)])

    return {
        "full_report": result.full_report,
        "revision_count": state.get("revision_count", 0) + 1,
    }
