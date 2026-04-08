"""Planner node — generates the initial research plan.

Reads research_goal, confirmed_constraints, open_dimensions, and language from state,
calls the LLM with structured output to produce a SimplifiedPlan, then normalizes the
result into the runtime shape expected by downstream nodes.
"""

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from deep_agents.configuration import Configuration
from deep_agents.prompts import planner_prompt
from deep_agents.schemas import SimplifiedPlan
from deep_agents.state import ResearchState
from deep_agents.utils import _strip_ctrl, get_api_key_for_model, STRUCTURED_OUTPUT_RETRY_EXCEPTIONS, get_today_str


async def planner_node(state: ResearchState, config: RunnableConfig) -> dict:
    """Generate the initial research plan from the research goal and constraints."""
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
        .with_structured_output(SimplifiedPlan)
        .with_retry(
            retry_if_exception_type=STRUCTURED_OUTPUT_RETRY_EXCEPTIONS,
            wait_exponential_jitter=True,
            stop_after_attempt=configurable.max_structured_output_retries,
        )
    )

    research_goal = state.get("research_goal", "")
    confirmed_constraints = state.get("confirmed_constraints", [])
    open_dimensions = state.get("open_dimensions", [])
    language = state.get("language", "zh")

    prompt_text = planner_prompt.format(
        date=get_today_str(),
        research_goal=research_goal,
        confirmed_constraints=confirmed_constraints,
        open_dimensions=open_dimensions,
        language=language,
    )
    prompt_text = _strip_ctrl(prompt_text)

    plan: SimplifiedPlan = await model.ainvoke([HumanMessage(content=prompt_text)])

    sections = [
        {
            "id": f"sec_{i + 1}",
            "title": s.title,
            "description": s.description,
            "search_queries": s.search_queries,
            "priority": i + 1,
        }
        for i, s in enumerate(plan.sections)
    ]

    return {
        "research_type": plan.research_type,
        "hypotheses": plan.hypotheses,
        "sections": sections,
        "outline_status": "provisional",
        "outline_revision_count": 0,
    }
