"""Authentication router for login and user profile."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config.auth_config import auth_settings
from backend.app.core.auth_dependencies import get_current_user
from backend.app.core.database import get_db
from backend.app.core.security import JWTManager, PasswordHasher
from backend.app.models.user import User
from backend.app.schemas.auth import TokenResponse, UserProfile

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/token", response_model=TokenResponse)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Authenticate user and return access token."""
    # Load user by login_name (username field in OAuth2 form)
    user = db.execute(
        select(User).where(User.login_name == form_data.username)
    ).scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect login name or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify this is a local account
    if user.auth_source != "local":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account uses SSO authentication",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify password
    if not user.password_hash or not PasswordHasher.verify_password(
        form_data.password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect login name or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if account is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    # Update last login time
    user.last_login_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()

    # Generate access token
    access_token = JWTManager.create_access_token(
        user_id=user.user_id,
        login_name=user.login_name,
        is_admin=user.is_admin,
    )

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=auth_settings.AUTH_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserProfile.model_validate(user),
    )


@router.get("/me", response_model=UserProfile)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
) -> UserProfile:
    """Get current user profile."""
    return UserProfile.model_validate(current_user)
