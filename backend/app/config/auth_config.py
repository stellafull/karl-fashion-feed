"""Authentication configuration using pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_chat_attachment_root() -> str:
    """Return the workspace-local default attachment storage path."""
    return str(Path(__file__).resolve().parents[2] / "data" / "chat_attachments")


class AuthSettings(BaseSettings):
    """Authentication and security settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # JWT settings
    AUTH_JWT_SECRET: str
    AUTH_JWT_ALGORITHM: str = "HS256"
    AUTH_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # CORS settings
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000"

    # Chat attachment storage
    CHAT_ATTACHMENT_ROOT: str = _default_chat_attachment_root()

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",")]


# Global settings instance
auth_settings = AuthSettings()
