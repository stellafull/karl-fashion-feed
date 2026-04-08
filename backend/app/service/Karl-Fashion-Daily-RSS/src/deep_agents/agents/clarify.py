"""Clarify node — entry point of the research graph.

Reads the conversation messages (and optional image context), calls the model with
structured output to produce a ResearchBrief, then returns a Command routing to
"planner" or END based on whether clarification is needed.
"""
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command

from deep_agents.configuration import Configuration
from deep_agents.prompts import clarify_prompt
from deep_agents.schemas import ResearchBrief
from deep_agents.state import ResearchState
from deep_agents.utils import _strip_ctrl, get_api_key_for_model, STRUCTURED_OUTPUT_RETRY_EXCEPTIONS, get_today_str


def _format_messages(messages: list) -> str:
    """Format a list of LangChain message objects or plain dicts into a readable string."""
    lines = []
    for msg in messages:
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content", "")
        else:
            class_name = type(msg).__name__.lower()
            if "human" in class_name:
                role = "user"
            elif "ai" in class_name or "assistant" in class_name:
                role = "assistant"
            elif "system" in class_name:
                role = "system"
            else:
                role = class_name
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def clarify_node(
    state: ResearchState, config: RunnableConfig
) -> Command[Literal["planner"]]:
    """Entry-point node. Returns Command(goto=END) if clarification needed, else Command(goto='planner')."""
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
        .with_structured_output(ResearchBrief)
        .with_retry(
            retry_if_exception_type=STRUCTURED_OUTPUT_RETRY_EXCEPTIONS,
            wait_exponential_jitter=True,
            stop_after_attempt=configurable.max_structured_output_retries,
        )
    )

    messages = state.get("messages", [])
    object_context = state.get("object_context")
    messages_text = _format_messages(messages)

    image_context_str = ""
    if object_context:
        image_context_str = f"\n用户还提供了一张图片供参考：{object_context}"

    prompt_text = clarify_prompt.format(
        date=get_today_str(),
        messages=messages_text,
        image_context=image_context_str,
    )
    prompt_text = _strip_ctrl(prompt_text)

    if object_context:
        invoke_input = [
            HumanMessage(
                content=[
                    {"type": "image_url", "image_url": {"url": object_context}},
                    {"type": "text", "text": prompt_text},
                ]
            )
        ]
    else:
        invoke_input = [HumanMessage(content=prompt_text)]

    brief: ResearchBrief = await model.ainvoke(invoke_input)

    update = {
        "need_clarification": brief.need_clarification,
        "clarification_question": brief.clarification_question,
        "research_goal": brief.research_goal,
        "confirmed_constraints": brief.confirmed_constraints,
        "open_dimensions": brief.open_dimensions,
        "language": brief.language,
    }

    if brief.need_clarification:
        return Command(goto=END, update=update)
    return Command(goto="planner", update=update)
