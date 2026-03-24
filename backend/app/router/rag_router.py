"""Single-entry RAG answer router."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from backend.app.schemas.rag_api import (
    RagAnswerResponse,
    RagQueryRequest,
    RagRequestContext,
    RequestImageInput,
)
from backend.app.schemas.rag_query import QueryFilters, TimeRange
from backend.app.service.RAG.embedding_service import encode_bytes_as_base64
from backend.app.service.RAG.rag_answer_service import RagAnswerService

router = APIRouter(prefix="/rag", tags=["rag"])


def get_rag_answer_service() -> RagAnswerService:
    """Return the singleton answer service dependency."""
    return RagAnswerService()


@router.post("/query")
async def query_rag(
    query: Annotated[str | None, Form()] = None,
    images: Annotated[list[UploadFile] | None, File()] = None,
    source_names: Annotated[list[str] | None, Form()] = None,
    categories: Annotated[list[str] | None, Form()] = None,
    brands: Annotated[list[str] | None, Form()] = None,
    tags: Annotated[list[str] | None, Form()] = None,
    start_at: Annotated[str | None, Form()] = None,
    end_at: Annotated[str | None, Form()] = None,
    limit: Annotated[int, Form()] = 10,
    rag_answer_service: RagAnswerService = Depends(get_rag_answer_service),
) -> RagAnswerResponse:
    """Answer one grounded RAG query from text, image, or both."""
    request_images = await _read_request_images(images)
    try:
        filters = _build_filters(
            source_names=source_names,
            categories=categories,
            brands=brands,
            tags=tags,
            start_at=start_at,
            end_at=end_at,
        )
        request = RagQueryRequest(query=query, filters=filters, limit=limit)
    except (ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if request.query is None and not request_images:
        raise HTTPException(
            status_code=422,
            detail="rag query requires text query or uploaded images",
        )

    request_context = RagRequestContext(
        filters=filters,
        limit=limit,
        request_images=request_images,
    )
    return await rag_answer_service.answer(
        request=request,
        request_context=request_context,
    )


def _build_filters(
    *,
    source_names: list[str] | None,
    categories: list[str] | None,
    brands: list[str] | None,
    tags: list[str] | None,
    start_at: str | None,
    end_at: str | None,
) -> QueryFilters:
    time_range = None
    parsed_start_at = _parse_optional_datetime(start_at)
    parsed_end_at = _parse_optional_datetime(end_at)
    if parsed_start_at is not None or parsed_end_at is not None:
        time_range = TimeRange(start_at=parsed_start_at, end_at=parsed_end_at)
    return QueryFilters(
        source_names=_normalize_terms(source_names),
        categories=_normalize_terms(categories),
        brands=_normalize_terms(brands),
        tags=_normalize_terms(tags),
        time_range=time_range,
    )


def _normalize_terms(values: list[str] | None) -> list[str]:
    return [value.strip() for value in values or [] if value and value.strip()]


def _parse_optional_datetime(value: str | None):
    if value is None:
        return None
    normalized_value = value.strip()
    if not normalized_value:
        return None
    if normalized_value.endswith("Z"):
        normalized_value = normalized_value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized_value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def _read_request_images(
    images: list[UploadFile] | None,
) -> list[RequestImageInput]:
    request_images: list[RequestImageInput] = []
    for image in images or []:
        content = await image.read()
        mime_type = (image.content_type or "").strip()
        if not mime_type:
            raise HTTPException(
                status_code=422,
                detail="uploaded image content_type must not be empty",
            )
        try:
            request_images.append(
                RequestImageInput(
                    mime_type=mime_type,
                    base64_data=encode_bytes_as_base64(content),
                )
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return request_images
