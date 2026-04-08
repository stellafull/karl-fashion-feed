import operator
from typing import Annotated

from langgraph.graph import MessagesState
from typing_extensions import TypedDict


class ResearchInputState(MessagesState, total=False):
    object_context: str | None


class ResearchState(ResearchInputState):
    need_clarification: bool
    clarification_question: str

    research_goal: str
    confirmed_constraints: list[str]
    open_dimensions: list[str]
    language: str

    research_type: str
    hypotheses: list[str]
    sections: list[dict]
    outline_status: str
    outline_revision_count: int

    facts: Annotated[list[dict], operator.add]
    data_points: Annotated[list[dict], operator.add]
    hypothesis_evidence: Annotated[list[dict], operator.add]
    charts: Annotated[list[dict], operator.add]
    contradictions: Annotated[list[dict], operator.add]
    section_drafts: Annotated[list[dict], operator.add]

    full_report: str
    review_result: dict | None
    revision_count: int
    final_result: dict | None


class SectionState(TypedDict):
    section_id: str
    section_title: str
    section_description: str
    search_queries: list[str]
    research_goal: str
    hypotheses: list[str]
    language: str

    section_research: str
    section_facts: list[dict]
    section_insights: list[str]
    section_hypothesis_evidence: list[dict]
    section_contradictions: list[dict]
    missing_info: list[str]
    section_data_points: list[dict]
    section_charts: list[dict]
