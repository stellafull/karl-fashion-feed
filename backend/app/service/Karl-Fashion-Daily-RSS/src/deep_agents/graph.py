"""LangGraph builder: main research graph + section subgraph.

Uses Send for parallel section fan-out (map-reduce) so each section_worker
is visible in LangGraph streaming/visualization.
"""

import logging
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite, Send

from deep_agents.state import (
    ResearchInputState,
    ResearchState,
    SectionState,
)

from deep_agents.agents.clarify import clarify_node
from deep_agents.agents.planner import planner_node
from deep_agents.agents.outline_reviser import outline_reviser_node
from deep_agents.agents.analyst import analyst_node
from deep_agents.agents.data_wiz import data_wiz_node
from deep_agents.agents.deep_scout import deep_scout_node
from deep_agents.agents.writer import writer_node
from deep_agents.agents.synthesizer import synthesizer_node
from deep_agents.agents.trend_triangulator import trend_triangulator_node
from deep_agents.agents.reviewer import reviewer_node
from deep_agents.agents.reviser import reviser_node
from deep_agents.agents.final_check import final_check_node

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Section subgraph (runs inside each section_worker)
# ---------------------------------------------------------------------------

_section_subgraph = None


def _get_section_subgraph():
    global _section_subgraph
    if _section_subgraph is None:
        _section_subgraph = build_section_subgraph()
    return _section_subgraph


def build_section_subgraph():
    """deep_scout → analyst → data_wiz

    deep_scout keeps its tool transcript local and emits one final compressed
    section_research artifact for downstream consumers.
    """
    graph = StateGraph(SectionState)
    graph.add_node("deep_scout", deep_scout_node)
    graph.add_node("analyst", analyst_node)
    graph.add_node("data_wiz", data_wiz_node)
    graph.add_edge(START, "deep_scout")
    graph.add_edge("deep_scout", "analyst")
    graph.add_edge("analyst", "data_wiz")
    graph.add_edge("data_wiz", END)
    return graph.compile()


# ---------------------------------------------------------------------------
# Section map-reduce nodes (replaces manual-asyncio section_pipeline_node)
# ---------------------------------------------------------------------------


def _tag_records(records: list[dict], section_id: str) -> list[dict]:
    """Attach section_id to every record before merging into parent state."""
    return [{**r, "section_id": section_id} for r in records if isinstance(r, dict)]


async def initiate_sections(
    state: ResearchState, config: RunnableConfig  # noqa: ARG001
) -> dict:
    """Clear stale evidence from any previous round before fan-out.

    Overwrite bypasses operator.add reducers so the section_workers start
    from clean accumulator lists.  The actual fan-out happens via the
    conditional edge ``fan_out_sections`` that follows this node.
    """
    return {
        "facts": Overwrite([]),
        "data_points": Overwrite([]),
        "hypothesis_evidence": Overwrite([]),
        "charts": Overwrite([]),
        "contradictions": Overwrite([]),
    }


def fan_out_sections(state: ResearchState) -> list[Send]:
    """Conditional edge: dispatch each section to section_worker via Send.

    LangGraph runs all Send targets in parallel and merges their results
    through the operator.add reducers on ResearchState.
    """
    sections = state.get("sections", [])
    if not sections:
        raise ValueError("Planner produced no sections")

    return [
        Send(
            "section_worker",
            {
                "section_id": s["id"],
                "section_title": s["title"],
                "section_description": s["description"],
                "search_queries": s["search_queries"],
                "research_goal": state["research_goal"],
                "hypotheses": state.get("hypotheses", []),
                "language": state.get("language", "zh"),
            },
        )
        for s in sections
    ]


async def section_worker(state: dict, config: RunnableConfig) -> dict:
    """Run the section subgraph for one section; tag results for parent merge.

    Invoked in parallel via Send.  Returns dicts that operator.add merges
    into the ResearchState accumulator fields.
    """
    sg = _get_section_subgraph()

    section_input: SectionState = {
        "section_id": state["section_id"],
        "section_title": state["section_title"],
        "section_description": state["section_description"],
        "search_queries": state["search_queries"],
        "research_goal": state["research_goal"],
        "hypotheses": state.get("hypotheses", []),
        "language": state.get("language", "zh"),
        "section_research": "",
        "section_facts": [],
        "section_insights": [],
        "section_hypothesis_evidence": [],
        "section_contradictions": [],
        "missing_info": [],
        "section_data_points": [],
        "section_charts": [],
    }

    result = await sg.ainvoke(section_input, config)
    sid = state["section_id"]

    return {
        "facts": _tag_records(result.get("section_facts", []), sid),
        "data_points": _tag_records(result.get("section_data_points", []), sid),
        "hypothesis_evidence": _tag_records(
            result.get("section_hypothesis_evidence", []), sid
        ),
        "charts": _tag_records(result.get("section_charts", []), sid),
        "contradictions": _tag_records(
            result.get("section_contradictions", []), sid
        ),
    }


def route_after_sections(
    state: ResearchState,
) -> Literal["lead_writer", "outline_reviser"]:
    """Route to outline_reviser when >=2 hypotheses refuted and revision count < 1."""
    refuted = sum(
        1
        for h in state.get("hypothesis_evidence", [])
        if h.get("evidence_type") == "refutes"
    )
    if refuted >= 2 and state.get("outline_revision_count", 0) < 1:
        return "outline_reviser"
    return "lead_writer"


# ---------------------------------------------------------------------------
# Main research graph
# ---------------------------------------------------------------------------


def build_research_graph(*, checkpointer: Any):
    """Compile the main research graph with an explicit checkpointer."""
    graph = StateGraph(ResearchState, input_schema=ResearchInputState)

    graph.add_node("clarify", clarify_node)
    graph.add_node("planner", planner_node)
    graph.add_node("outline_reviser", outline_reviser_node)
    graph.add_node("initiate_sections", initiate_sections)
    graph.add_node("section_worker", section_worker)
    graph.add_node("lead_writer", writer_node)
    graph.add_node("synthesizer", synthesizer_node)
    graph.add_node("trend_triangulator", trend_triangulator_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("reviser", reviser_node)
    graph.add_node("final_check", final_check_node)

    # clarify → planner (via Command) or END
    graph.add_edge(START, "clarify")

    # planner / outline_reviser → clear evidence → fan-out to section_workers
    graph.add_edge("planner", "initiate_sections")
    graph.add_edge("outline_reviser", "initiate_sections")
    graph.add_conditional_edges(
        "initiate_sections", fan_out_sections, ["section_worker"]
    )

    # After ALL section_workers complete → route based on hypothesis evidence
    graph.add_conditional_edges(
        "section_worker",
        route_after_sections,
        ["lead_writer", "outline_reviser"],
    )

    # writer → synthesizer → (Command) trend_triangulator | reviewer
    graph.add_edge("lead_writer", "synthesizer")

    # reviewer → (Command) reviser | final_check
    # reviser always loops back to reviewer for re-evaluation
    graph.add_edge("trend_triangulator", "reviewer")
    graph.add_edge("reviser", "reviewer")

    graph.add_edge("final_check", END)

    return graph.compile(checkpointer=checkpointer)
