"""Reviewer: strict quality review -> ReviewResult with quality_score and issues."""
import json
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from deep_agents.configuration import Configuration, FINAL_REPORT_RATE_LIMITER
from deep_agents.prompts import reviewer_prompt
from deep_agents.schemas import ReviewResult
from deep_agents.state import ResearchState
from deep_agents.utils import _strip_ctrl, get_api_key_for_model, STRUCTURED_OUTPUT_RETRY_EXCEPTIONS


async def reviewer_node(
    state: ResearchState, config: RunnableConfig
) -> Command[Literal["reviser", "final_check"]]:
    """Review the full report; route to reviser if quality insufficient, else final_check."""
    configurable = Configuration.from_runnable_config(config)
    model = (
        init_chat_model(
            model=configurable.final_report_model,
            max_tokens=configurable.final_report_model_max_tokens,
            api_key=get_api_key_for_model(configurable.final_report_model, config),
            base_url=configurable.openai_compatible_base_url,
            max_retries=configurable.provider_max_retries,
            rate_limiter=FINAL_REPORT_RATE_LIMITER,
            disable_streaming=True,
        )
        .with_structured_output(ReviewResult)
        .with_retry(
            retry_if_exception_type=STRUCTURED_OUTPUT_RETRY_EXCEPTIONS,
            wait_exponential_jitter=True,
            stop_after_attempt=configurable.max_structured_output_retries,
        )
    )

    prompt_text = reviewer_prompt.format(
        research_goal=state["research_goal"],
        sections=json.dumps(
            [{"id": s["id"], "title": s["title"]} for s in state.get("sections", [])],
            ensure_ascii=False,
        ),
        full_report=state.get("full_report", ""),
        facts=json.dumps(state.get("facts", [])[:30], ensure_ascii=False),
        data_points=json.dumps(state.get("data_points", [])[:20], ensure_ascii=False),
    )
    prompt_text = _strip_ctrl(prompt_text)

    result: ReviewResult = await model.ainvoke([HumanMessage(content=prompt_text)])
    review_result = result.model_dump()

    if review_result.get("verdict") != "pass" and state.get("revision_count", 0) < 2:
        return Command(goto="reviser", update={"review_result": review_result})
    return Command(goto="final_check", update={"review_result": review_result})
