"""Synthesizer: merge all section drafts into a complete full_report."""
import json
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from deep_agents.configuration import Configuration, FINAL_REPORT_RATE_LIMITER
from deep_agents.prompts import synthesizer_prompt
from deep_agents.state import ResearchState
from deep_agents.utils import _strip_ctrl, get_api_key_for_model


async def synthesizer_node(
    state: ResearchState, config: RunnableConfig
) -> Command[Literal["trend_triangulator", "reviewer"]]:
    """Merge section drafts into full report; route to trend_triangulator or reviewer."""
    configurable = Configuration.from_runnable_config(config)
    model = init_chat_model(
        model=configurable.final_report_model,
        max_tokens=configurable.final_report_model_max_tokens,
        api_key=get_api_key_for_model(configurable.final_report_model, config),
        base_url=configurable.openai_compatible_base_url,
        max_retries=configurable.provider_max_retries,
        rate_limiter=FINAL_REPORT_RATE_LIMITER,
    )

    prompt_text = synthesizer_prompt.format(
        research_goal=state["research_goal"],
        language=state.get("language", "zh"),
        section_drafts=json.dumps(
            state.get("section_drafts", []), ensure_ascii=False, indent=2
        ),
        hypothesis_evidence=json.dumps(
            state.get("hypothesis_evidence", [])[:20], ensure_ascii=False
        ),
        contradictions=json.dumps(state.get("contradictions", [])[:10], ensure_ascii=False),
    )
    prompt_text = _strip_ctrl(prompt_text)

    response = await model.ainvoke([HumanMessage(content=prompt_text)])

    next_node = (
        "trend_triangulator"
        if state.get("research_type") == "trend_analysis"
        else "reviewer"
    )
    return Command(goto=next_node, update={"full_report": response.content})
