"""Authentication router for Feishu login, dev login, and user profile."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config.auth_config import auth_settings
from backend.app.core.auth_dependencies import get_current_user
from backend.app.core.database import get_db
from backend.app.core.security import JWTManager, PasswordHasher
from backend.app.models.user import User
from backend.app.schemas.auth import FeishuClientExchangeRequest, TokenResponse, UserProfile
from backend.app.service.feishu_auth_service import FeishuAuthService, FeishuUserIdentity

router = APIRouter(prefix="/auth", tags=["authentication"])


def get_feishu_auth_service() -> FeishuAuthService:
    """Create a Feishu auth service for the current request."""
    return FeishuAuthService()


def _build_token_response(user: User) -> TokenResponse:
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


def _touch_last_login(user: User) -> None:
    user.last_login_at = datetime.now(UTC).replace(tzinfo=None)


def _resolve_or_create_feishu_user(db: Session, identity: FeishuUserIdentity) -> User:
    user = db.execute(
        select(User).where(User.feishu_user_id == identity.feishu_user_id)
    ).scalar_one_or_none()
    if user is None:
        user = User(
            login_name=None,
            display_name=identity.display_name,
            email=identity.email,
            password_hash=None,
            auth_source="feishu",
            feishu_user_id=identity.feishu_user_id,
            feishu_open_id=identity.open_id,
            feishu_union_id=identity.union_id,
            feishu_avatar_url=identity.avatar_url,
            is_active=True,
            is_admin=False,
        )
        db.add(user)
    else:
        user.display_name = identity.display_name
        user.email = identity.email
        user.auth_source = "feishu"
        user.feishu_open_id = identity.open_id
        user.feishu_union_id = identity.union_id
        user.feishu_avatar_url = identity.avatar_url
    _touch_last_login(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/feishu/client/exchange", response_model=TokenResponse)
async def exchange_feishu_client_code(
    request: FeishuClientExchangeRequest,
    db: Session = Depends(get_db),
    feishu_auth_service: FeishuAuthService = Depends(get_feishu_auth_service),
) -> TokenResponse:
    """Exchange a Feishu requestAccess code for a local JWT."""
    try:
        identity = await feishu_auth_service.exchange_client_code(request.code)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error

    user = _resolve_or_create_feishu_user(db, identity)
    return _build_token_response(user)


@router.get("/feishu/browser/start")
async def start_feishu_browser_login(
    feishu_auth_service: FeishuAuthService = Depends(get_feishu_auth_service),
) -> RedirectResponse:
    """Redirect the browser to the Feishu OAuth authorization page."""
    state = JWTManager.create_browser_oauth_state()
    return RedirectResponse(
        url=feishu_auth_service.build_browser_authorize_url(state=state),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@router.get("/feishu/browser/callback")
async def finish_feishu_browser_login(
    code: str,
    state: str,
    db: Session = Depends(get_db),
    feishu_auth_service: FeishuAuthService = Depends(get_feishu_auth_service),
) -> RedirectResponse:
    """Exchange a Feishu browser OAuth code and redirect with the app JWT."""
    try:
        JWTManager.decode_browser_oauth_state(state)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error

    try:
        identity = await feishu_auth_service.exchange_browser_code(code)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error

    user = _resolve_or_create_feishu_user(db, identity)
    token_response = _build_token_response(user)
    redirect_target = (
        f"{auth_settings.FEISHU_FRONTEND_AUTH_COMPLETE_URL}?token={token_response.access_token}"
    )
    return RedirectResponse(
        url=redirect_target,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@router.post("/dev/token", response_model=TokenResponse)
async def login_dev_root(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Authenticate the dedicated dev-root local account only."""
    user = db.execute(
        select(User).where(User.login_name == form_data.username)
    ).scalar_one_or_none()

    if not user or user.auth_source != "local":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect login name or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.login_name != "dev-root":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only dev-root may use dev login",
        )

    if not user.password_hash or not PasswordHasher.verify_password(
        form_data.password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect login name or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    _touch_last_login(user)
    db.commit()
    db.refresh(user)
    return _build_token_response(user)


@router.get("/me", response_model=UserProfile)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
) -> UserProfile:
    """Get current user profile."""
    return UserProfile.model_validate(current_user)
