"""Structured output schemas for LLM/VLM tasks."""

from backend.app.schemas.llm.digest_packaging import DigestPackagingPlan, DigestPackagingSchema
from backend.app.schemas.llm.digest_report_writing import DigestReportWritingSchema
from backend.app.schemas.llm.event_frame_extraction import EventFrameExtractionSchema, ExtractedEventFrame
from backend.app.schemas.llm.facet_assignment import FacetAssignmentSchema, StoryFacetAssignment
from backend.app.schemas.llm.story_cluster_judgment import StoryClusterGroup, StoryClusterJudgmentSchema

__all__ = [
    "DigestPackagingPlan",
    "DigestPackagingSchema",
    "DigestReportWritingSchema",
    "EventFrameExtractionSchema",
    "ExtractedEventFrame",
    "FacetAssignmentSchema",
    "StoryFacetAssignment",
    "StoryClusterGroup",
    "StoryClusterJudgmentSchema",
]
