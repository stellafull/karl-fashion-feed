"""Lead writer: drafts all sections in parallel and returns all SectionDrafts."""

import asyncio
import json

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from deep_agents.configuration import Configuration
from deep_agents.prompts import writer_prompt
from deep_agents.schemas import SectionDraft
from deep_agents.state import ResearchState
from deep_agents.utils import _strip_ctrl, get_api_key_for_model, STRUCTURED_OUTPUT_RETRY_EXCEPTIONS


async def writer_node(state: ResearchState, config: RunnableConfig) -> dict:
    """Write all section drafts in parallel using section-local evidence only."""
    sections = state.get("sections", [])
    if not sections:
        raise ValueError("Cannot write report without sections")

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
        .with_structured_output(SectionDraft)
        .with_retry(
            retry_if_exception_type=STRUCTURED_OUTPUT_RETRY_EXCEPTIONS,
            wait_exponential_jitter=True,
            stop_after_attempt=configurable.max_structured_output_retries,
        )
    )

    all_facts = state.get("facts", [])
    all_data_points = state.get("data_points", [])
    all_charts = state.get("charts", [])
    all_contradictions = state.get("contradictions", [])
    all_hypothesis_evidence = state.get("hypothesis_evidence", [])
    language = state.get("language", "zh")

    sections_list = json.dumps(
        [{"title": s["title"]} for s in sections],
        ensure_ascii=False,
    )

    try:
        stream_writer = get_stream_writer()
    except RuntimeError:
        stream_writer = None

    def _strip_runtime_section_id(records: list[dict]) -> list[dict]:
        return [{k: v for k, v in record.items() if k != "section_id"} for record in records]

    async def _write_section(section: dict) -> dict:
        sec_id = section["id"]

        # Filter runtime evidence to this section only.
        sec_facts = [f for f in all_facts if f.get("section_id") == sec_id]
        sec_data = [d for d in all_data_points if d.get("section_id") == sec_id]
        sec_charts = [c for c in all_charts if c.get("section_id") == sec_id]
        sec_contradictions = [c for c in all_contradictions if c.get("section_id") == sec_id]
        sec_hypothesis_evidence = [
            h for h in all_hypothesis_evidence if h.get("section_id") == sec_id
        ]

        prompt_text = writer_prompt.format(
            research_goal=state["research_goal"],
            sections_list=sections_list,
            hypothesis_evidence=json.dumps(
                _strip_ctrl(_strip_runtime_section_id(sec_hypothesis_evidence[:10])),
                ensure_ascii=False,
            ),
            section_title=section["title"],
            section_description=section["description"],
            section_facts=json.dumps(
                _strip_ctrl(_strip_runtime_section_id(sec_facts[:20])),
                ensure_ascii=False,
            ),
            section_data_points=json.dumps(
                _strip_ctrl(_strip_runtime_section_id(sec_data[:10])),
                ensure_ascii=False,
            ),
            charts=json.dumps(
                _strip_ctrl(_strip_runtime_section_id(sec_charts[:5])),
                ensure_ascii=False,
            ),
            contradictions=json.dumps(
                _strip_ctrl(_strip_runtime_section_id(sec_contradictions[:5])),
                ensure_ascii=False,
            ),
            language=language,
        )
        prompt_text = _strip_ctrl(prompt_text)

        draft: SectionDraft = await model.ainvoke([HumanMessage(content=prompt_text)])

        draft_dict = draft.model_dump()
        draft_dict["section_id"] = sec_id
        if stream_writer is not None:
            stream_writer({"type": "section_done", "section_id": sec_id})
        return draft_dict

    tasks: list[asyncio.Task[dict]] = []
    try:
        async with asyncio.TaskGroup() as task_group:
            for section in sections:
                tasks.append(task_group.create_task(_write_section(section)))
    except* Exception as exc_group:
        # Preserve failfast root cause instead of leaking ExceptionGroup upstream.
        raise exc_group.exceptions[0]

    drafts = [task.result() for task in tasks]

    return {"section_drafts": drafts}
