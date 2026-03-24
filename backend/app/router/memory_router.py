"""Long-term memory router for CRUD operations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.auth_dependencies import get_current_user
from backend.app.core.database import get_db
from backend.app.models.chat import LongTermMemory
from backend.app.models.user import User
from backend.app.schemas.memory import (
    CreateMemoryRequest,
    MemoryListResponse,
    MemoryResponse,
    UpdateMemoryRequest,
)

router = APIRouter(prefix="/memories", tags=["memory"])


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    memory_type: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemoryListResponse:
    """List all memories for current user, optionally filtered by type."""
    query = select(LongTermMemory).where(LongTermMemory.user_id == current_user.user_id)

    if memory_type:
        query = query.where(LongTermMemory.memory_type == memory_type)

    memories = db.execute(query.order_by(LongTermMemory.created_at.desc())).scalars().all()

    return MemoryListResponse(
        memories=[MemoryResponse.model_validate(m) for m in memories]
    )


@router.post("", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_or_update_memory(
    request: CreateMemoryRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemoryResponse:
    """Create or update a memory (upsert on user_id, memory_type, memory_key)."""
    # Check if memory already exists
    existing = db.execute(
        select(LongTermMemory).where(
            LongTermMemory.user_id == current_user.user_id,
            LongTermMemory.memory_type == request.memory_type,
            LongTermMemory.memory_key == request.memory_key,
        )
    ).scalar_one_or_none()

    if existing:
        # Update existing memory
        existing.memory_value = request.memory_value
        db.commit()
        db.refresh(existing)
        return MemoryResponse.model_validate(existing)
    else:
        # Create new memory
        memory = LongTermMemory(
            user_id=current_user.user_id,
            memory_type=request.memory_type,
            memory_key=request.memory_key,
            memory_value=request.memory_value,
            source="manual",
        )
        db.add(memory)
        db.commit()
        db.refresh(memory)
        return MemoryResponse.model_validate(memory)


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: str,
    request: UpdateMemoryRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemoryResponse:
    """Update a memory value."""
    memory = db.get(LongTermMemory, memory_id)
    if not memory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )

    if memory.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this memory",
        )

    memory.memory_value = request.memory_value
    db.commit()
    db.refresh(memory)

    return MemoryResponse.model_validate(memory)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a memory."""
    memory = db.get(LongTermMemory, memory_id)
    if not memory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )

    if memory.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this memory",
        )

    db.delete(memory)
    db.commit()
