"""Security utilities for password hashing and JWT token management."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt
from pydantic import BaseModel

from backend.app.config.auth_config import auth_settings


class TokenPayload(BaseModel):
    """JWT token payload structure."""

    sub: str  # user_id
    login_name: str
    is_admin: bool
    iat: int
    exp: int


class PasswordHasher:
    """bcrypt password hashing utilities."""

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt with 12 rounds."""
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )


class JWTManager:
    """JWT token generation and validation."""

    @staticmethod
    def create_access_token(
        user_id: str,
        login_name: str,
        is_admin: bool = False,
    ) -> str:
        """Create a new access token."""
        now = datetime.now(UTC)
        expire = now + timedelta(minutes=auth_settings.AUTH_ACCESS_TOKEN_EXPIRE_MINUTES)

        payload = {
            "sub": user_id,
            "login_name": login_name,
            "is_admin": is_admin,
            "iat": int(now.timestamp()),
            "exp": int(expire.timestamp()),
        }

        return jwt.encode(
            payload,
            auth_settings.AUTH_JWT_SECRET,
            algorithm=auth_settings.AUTH_JWT_ALGORITHM,
        )

    @staticmethod
    def decode_token(token: str) -> TokenPayload:
        """Decode and validate a JWT token."""
        try:
            payload = jwt.decode(
                token,
                auth_settings.AUTH_JWT_SECRET,
                algorithms=[auth_settings.AUTH_JWT_ALGORITHM],
            )
            return TokenPayload(**payload)
        except JWTError as e:
            raise ValueError(f"Invalid token: {e}") from e
