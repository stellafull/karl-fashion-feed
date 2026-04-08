"""DeepScout node — explicit tool loop with one final compressed research artifact."""
import json
import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from deep_agents.configuration import Configuration
from deep_agents.prompts import deep_scout_prompt
from deep_agents.state import SectionState
from deep_agents.utils import (
    _strip_ctrl,
    get_all_tools,
    get_api_key_for_model,
    get_today_str,
)

logger = logging.getLogger(__name__)

_RESEARCH_COMPLETE_TOOL_NAME = "ResearchComplete"
_THINK_TOOL_NAME = "think_tool"

_COMPRESS_RESEARCH_PROMPT = """
今天的日期是 {date}。
研究目标：{research_goal}
当前章节：{section_title} — {section_description}
待验证假设：
{hypotheses}

以下是 deep_scout 本地工具循环收集的原始研究素材：

{raw_research_material}

你是研究信息压缩专家。请将上述研究素材压缩为一份供下游节点使用的最终章节研究摘要。

压缩原则：
1. 保留所有 URL，格式优先为 [标题](URL)
2. 保留精确数据：数字、百分比、金额、日期不得改写
3. 保留相互矛盾的信息，不要强行统一
4. 优先保留支持或反驳假设的关键证据
5. 删除与当前章节无关或重复冗余的内容

输出规则：
- 直接输出压缩后的 Markdown 文本
- 不要输出 JSON
- 不要输出代码块
- 不要输出解释、前言或总结
""".strip()


def _message_content_to_text(content: object) -> str:
    """Normalize provider/tool payloads into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def _format_research_material(tool_name: str, content: str) -> str:
    """Label evidence-bearing tool output before final compression."""
    return f"## {tool_name}\n{content.strip()}"


async def _compress_section_research(
    *,
    raw_research_material: str,
    research_goal: str,
    section_title: str,
    section_description: str,
    hypotheses: list[str],
    config: RunnableConfig,
) -> str:
    """Compress the full local research transcript once after the tool loop."""
    if not raw_research_material.strip():
        return ""

    configurable = Configuration.from_runnable_config(config)
    model = init_chat_model(
        model=configurable.compression_model,
        max_tokens=configurable.compression_model_max_tokens,
        api_key=get_api_key_for_model(configurable.compression_model, config),
        base_url=configurable.openai_compatible_base_url,
        max_retries=configurable.provider_max_retries,
        disable_streaming=True,
    )

    prompt_text = _COMPRESS_RESEARCH_PROMPT.format(
        date=get_today_str(),
        research_goal=research_goal,
        section_title=section_title,
        section_description=section_description,
        hypotheses=json.dumps(hypotheses, ensure_ascii=False),
        raw_research_material=raw_research_material,
    )
    prompt_text = _strip_ctrl(prompt_text)

    response = await model.ainvoke([HumanMessage(content=prompt_text)])
    return _message_content_to_text(response.content)


async def deep_scout_node(state: SectionState, config: RunnableConfig) -> dict:
    """Run a local tool loop and emit one final compressed section artifact."""
    configurable = Configuration.from_runnable_config(config)
    tools = await get_all_tools(config)

    callable_tools = [
        t for t in tools if hasattr(t, "name") and callable(getattr(t, "ainvoke", None))
    ]
    tool_map = {t.name: t for t in callable_tools}

    model = init_chat_model(
        model=configurable.research_model,
        max_tokens=configurable.research_model_max_tokens,
        api_key=get_api_key_for_model(configurable.research_model, config),
        base_url=configurable.openai_compatible_base_url,
        max_retries=configurable.provider_max_retries,
    )
    bound_model = model.bind_tools(callable_tools)

    research_goal = state.get("research_goal", "")
    section_title = state.get("section_title", "")
    section_description = state.get("section_description", "")
    hypotheses = state.get("hypotheses", [])

    system_prompt = deep_scout_prompt.format(
        date=get_today_str(),
        research_goal=research_goal,
        section_title=section_title,
        section_description=section_description,
        search_queries="\n".join(state.get("search_queries", [])),
        hypotheses=json.dumps(hypotheses, ensure_ascii=False),
    )
    system_prompt = _strip_ctrl(system_prompt)

    messages: list = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="请开始研究当前章节，收集足够的证据。"),
    ]

    research_materials: list[str] = []

    for _ in range(configurable.max_deep_scout_iterations):
        response: AIMessage = await bound_model.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        done = any(
            tc["name"] == _RESEARCH_COMPLETE_TOOL_NAME
            for tc in response.tool_calls
        )
        tool_messages = []
        for tc in response.tool_calls:
            if tc["name"] == _RESEARCH_COMPLETE_TOOL_NAME:
                continue

            tool = tool_map.get(tc["name"])
            if tool is None:
                tool_messages.append(
                    ToolMessage(
                        content=f"Unknown tool: {tc['name']}",
                        tool_call_id=tc["id"],
                        name=tc["name"],
                    )
                )
                continue

            try:
                raw_result = await tool.ainvoke(tc, config=config)
                if isinstance(raw_result, ToolMessage):
                    content = raw_result.content
                else:
                    content = raw_result

                content = _strip_ctrl(_message_content_to_text(content))

                if tc["name"] != _THINK_TOOL_NAME and content.strip():
                    research_materials.append(
                        _format_research_material(tc["name"], content)
                    )

                tool_messages.append(
                    ToolMessage(
                        content=content,
                        tool_call_id=tc["id"],
                        name=tc["name"],
                    )
                )
            except Exception as tool_exc:
                logger.warning("Tool %s failed: %s", tc["name"], tool_exc)
                tool_messages.append(
                    ToolMessage(
                        content=f"Tool error: {tool_exc}",
                        tool_call_id=tc["id"],
                        name=tc["name"],
                    )
                )

        messages.extend(tool_messages)

        if done:
            break

    raw_section_research = "\n\n".join(research_materials)
    if not raw_section_research:
        return {"section_research": ""}

    try:
        section_research = await _compress_section_research(
            raw_research_material=raw_section_research,
            research_goal=research_goal,
            section_title=section_title,
            section_description=section_description,
            hypotheses=hypotheses,
            config=config,
        )
    except Exception as compress_exc:
        logger.warning(
            "Final research compression failed, using raw transcript: %s",
            compress_exc,
        )
        section_research = raw_section_research

    return {"section_research": section_research}
