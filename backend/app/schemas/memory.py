"""Long-term memory request and response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CreateMemoryRequest(BaseModel):
    """Request to create or update a memory."""

    memory_type: str
    memory_key: str
    memory_value: str


class UpdateMemoryRequest(BaseModel):
    """Request to update a memory value."""

    memory_value: str


class MemoryResponse(BaseModel):
    """Memory response."""

    memory_id: str
    memory_type: str
    memory_key: str
    memory_value: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MemoryListResponse(BaseModel):
    """List of memories."""

    memories: list[MemoryResponse]
