"""Analyst node — qualitative analysis of final section research for a section.

Reads section_research, hypotheses, section_title, section_description, and
research_goal from SectionState, calls the LLM with structured output to
produce an AnalystOutput, then returns the relevant state fields as plain
dicts/lists.
"""

import json

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from deep_agents.configuration import Configuration
from deep_agents.prompts import analyst_prompt
from deep_agents.schemas import AnalystOutput
from deep_agents.state import SectionState
from deep_agents.utils import _strip_ctrl, get_api_key_for_model, STRUCTURED_OUTPUT_RETRY_EXCEPTIONS


async def analyst_node(state: SectionState, config: RunnableConfig) -> dict:
    """Perform qualitative analysis of the section's compressed research artifact."""
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
        .with_structured_output(AnalystOutput)
        .with_retry(
            retry_if_exception_type=STRUCTURED_OUTPUT_RETRY_EXCEPTIONS,
            wait_exponential_jitter=True,
            stop_after_attempt=configurable.max_structured_output_retries,
        )
    )

    research_goal = state.get("research_goal", "")
    section_title = state.get("section_title", "")
    section_description = state.get("section_description", "")
    hypotheses = state.get("hypotheses", [])
    section_research = state.get("section_research", "")

    prompt_text = analyst_prompt.format(
        research_goal=research_goal,
        section_title=section_title,
        section_description=section_description,
        hypotheses=json.dumps(hypotheses, ensure_ascii=False),
        section_research=section_research or "（无章节研究素材）",
    )
    prompt_text = _strip_ctrl(prompt_text)

    output: AnalystOutput = await model.ainvoke([HumanMessage(content=prompt_text)])

    def _dump_items(items: list) -> list[dict]:
        dumped: list[dict] = []
        for item in items:
            if isinstance(item, dict):
                dumped.append(item)
                continue
            if hasattr(item, "model_dump"):
                dumped.append(item.model_dump())
                continue
            raise TypeError(f"Unsupported analyst output item type: {type(item)!r}")
        return dumped

    return {
        "section_facts": _dump_items(output.section_facts),
        "section_insights": output.section_insights,
        "section_hypothesis_evidence": _dump_items(output.section_hypothesis_evidence),
        "section_contradictions": _dump_items(output.section_contradictions),
        "missing_info": output.missing_info,
    }
