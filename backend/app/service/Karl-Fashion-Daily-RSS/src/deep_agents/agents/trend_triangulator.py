# src/deep_agents/agents/trend_triangulator.py
"""Trend triangulator: validate trend claims using 3-signal cross-check (conditional node)."""

import json
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from deep_agents.configuration import Configuration
from deep_agents.prompts import trend_triangulator_prompt
from deep_agents.state import ResearchState
from deep_agents.utils import _strip_ctrl, get_api_key_for_model


async def trend_triangulator_node(state: ResearchState, config: RunnableConfig) -> dict:
    """Only runs for trend_analysis research_type. Validates trend claims via 3 signals."""
    configurable = Configuration.from_runnable_config(config)
    model = init_chat_model(
        model=configurable.final_report_model,
        max_tokens=configurable.final_report_model_max_tokens,
        api_key=get_api_key_for_model(configurable.final_report_model, config),
        base_url=configurable.openai_compatible_base_url,
        max_retries=configurable.provider_max_retries,
    )

    prompt_text = trend_triangulator_prompt.format(
        full_report=state.get("full_report", ""),
        facts=json.dumps(state.get("facts", [])[:30], ensure_ascii=False),
    )
    prompt_text = _strip_ctrl(prompt_text)

    response = await model.ainvoke([HumanMessage(content=prompt_text)])
    return {"full_report": response.content}
