"""DataWiz node — quantitative data extraction from final section research.

Reads section_research, section_title, and research_goal from SectionState,
calls the LLM with structured output to produce a DataWizOutput, then returns
the relevant state fields as plain dicts/lists.
"""

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from deep_agents.configuration import Configuration
from deep_agents.prompts import data_wiz_prompt
from deep_agents.schemas import DataWizOutput
from deep_agents.state import SectionState
from deep_agents.utils import _strip_ctrl, get_api_key_for_model, STRUCTURED_OUTPUT_RETRY_EXCEPTIONS


async def data_wiz_node(state: SectionState, config: RunnableConfig) -> dict:
    """Extract data points and chart configurations from compressed section research."""
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
        .with_structured_output(DataWizOutput)
        .with_retry(
            retry_if_exception_type=STRUCTURED_OUTPUT_RETRY_EXCEPTIONS,
            wait_exponential_jitter=True,
            stop_after_attempt=configurable.max_structured_output_retries,
        )
    )

    research_goal = state.get("research_goal", "")
    section_title = state.get("section_title", "")
    section_research = state.get("section_research", "")

    prompt_text = data_wiz_prompt.format(
        research_goal=research_goal,
        section_title=section_title,
        section_research=section_research or "（无章节研究素材）",
    )
    prompt_text = _strip_ctrl(prompt_text)

    output: DataWizOutput = await model.ainvoke([HumanMessage(content=prompt_text)])

    def _dump_items(items: list) -> list[dict]:
        dumped: list[dict] = []
        for item in items:
            if isinstance(item, dict):
                dumped.append(item)
                continue
            if hasattr(item, "model_dump"):
                dumped.append(item.model_dump())
                continue
            raise TypeError(f"Unsupported data_wiz output item type: {type(item)!r}")
        return dumped

    return {
        "section_data_points": _dump_items(output.section_data_points),
        "section_charts": _dump_items(output.section_charts),
    }
