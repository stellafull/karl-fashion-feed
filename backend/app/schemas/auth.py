"""Authentication request and response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TokenResponse(BaseModel):
    """Token response after successful authentication."""

    access_token: str
    token_type: str
    expires_in: int
    user: UserProfile


class UserProfile(BaseModel):
    """User profile information."""

    user_id: str
    login_name: str
    display_name: str
    email: str | None
    is_admin: bool
    created_at: datetime

    class Config:
        from_attributes = True
