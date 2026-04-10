"""HTTP DTOs and request-scoped context for the RAG answer API."""

from __future__ import annotations

import base64
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.app.schemas.rag_query import ArticlePackage, QueryFilters, QueryPlan, RetrievalHit


class RequestImageInput(BaseModel):
    """One request-scoped image payload used by LLM perception and retrieval."""

    mime_type: str
    base64_data: str

    @model_validator(mode="after")
    def validate_payload(self) -> "RequestImageInput":
        if not self.mime_type.strip():
            raise ValueError("request image mime_type must not be empty")
        if not self.base64_data.strip():
            raise ValueError("request image base64_data must not be empty")
        return self

    def to_data_url(self) -> str:
        """Return the image as a data URL for multimodal model calls."""
        return f"data:{self.mime_type};base64,{self.base64_data}"

    @classmethod
    def from_bytes(cls, *, mime_type: str, content: bytes) -> "RequestImageInput":
        """Build a request image payload from uploaded bytes."""
        if not content:
            raise ValueError("uploaded image content must not be empty")
        encoded = base64.b64encode(content).decode("ascii")
        return cls(mime_type=mime_type, base64_data=encoded)


class RagQueryRequest(BaseModel):
    """Validated request payload for one RAG answer query."""

    query: str | None = None
    filters: QueryFilters = Field(default_factory=QueryFilters)
    limit: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def normalize_query(self) -> "RagQueryRequest":
        if self.query is not None:
            normalized_query = self.query.strip()
            self.query = normalized_query or None
        return self


class RagRequestContext(BaseModel):
    """Request-scoped runtime context injected into retrieval tools."""

    filters: QueryFilters = Field(default_factory=QueryFilters)
    limit: int = Field(default=10, ge=1, le=50)
    request_images: list[RequestImageInput] = Field(default_factory=list)

    @property
    def has_request_images(self) -> bool:
        """Return whether the current request includes uploaded images."""
        return bool(self.request_images)


class WebSearchResult(BaseModel):
    """One Brave search result returned to the answer layer."""

    title: str
    url: str
    snippet: str
    content: str = ""


class ExternalVisualResult(BaseModel):
    """One normalized external visual evidence result."""

    provider: Literal["brave_image", "brave_llm_context"]
    query: str
    title: str
    url: str
    source_name: str | None = None
    source_page_url: str | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None
    snippet: str = ""
    content: str = ""


class AnswerVisiblePackage(BaseModel):
    """Filtered evidence package used only for answer synthesis."""

    article_id: str
    title: str | None = None
    summary: str | None = None
    text_hits: list[RetrievalHit] = Field(default_factory=list)
    image_hits: list[RetrievalHit] = Field(default_factory=list)
    combined_score: float


class AnswerVisibleEvidence(BaseModel):
    """Filtered answer-visible evidence separate from raw retrieval output."""

    packages: list[AnswerVisiblePackage] = Field(default_factory=list)
    suppressed_image_hits: list[RetrievalHit] = Field(default_factory=list)
    external_visual_results: list[ExternalVisualResult] = Field(default_factory=list)


class AnswerCitation(BaseModel):
    """One stable citation emitted by the answer API."""

    marker: str
    source_type: Literal["rag", "web"]
    title: str | None = None
    source_name: str | None = None
    url: str
    snippet: str | None = None
    article_id: str | None = None
    article_image_id: str | None = None
    chunk_index: int | None = None


class AssistantImageResult(BaseModel):
    """One assistant-visible image result returned with the answer payload."""

    id: str
    source_type: Literal["rag", "external"]
    image_url: str
    title: str | None = None
    source_name: str | None = None
    source_page_url: str | None = None
    snippet: str | None = None
    article_id: str | None = None
    article_image_id: str | None = None
    citation_marker: str | None = None


class RagAnswerResponse(BaseModel):
    """Final HTTP response returned by the single-entry answer API."""

    answer: str
    citations: list[AnswerCitation] = Field(default_factory=list)
    packages: list[ArticlePackage] = Field(default_factory=list)
    query_plans: list[QueryPlan] = Field(default_factory=list)
    web_results: list[WebSearchResult] = Field(default_factory=list)
    external_visual_results: list[ExternalVisualResult] = Field(default_factory=list)
    image_results: list[AssistantImageResult] = Field(default_factory=list)
