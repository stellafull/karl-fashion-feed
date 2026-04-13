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

    AUTH_JWT_SECRET: str
    AUTH_JWT_ALGORITHM: str = "HS256"
    AUTH_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    AUTH_BROWSER_STATE_EXPIRE_SECONDS: int = 300

    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000"
    CHAT_ATTACHMENT_ROOT: str = _default_chat_attachment_root()

    FEISHU_APP_ID: str | None = None
    FEISHU_APP_SECRET: str | None = None
    FEISHU_BROWSER_REDIRECT_URI: str | None = None
    FEISHU_FRONTEND_AUTH_COMPLETE_URL: str | None = None
    FEISHU_OAUTH_SCOPE: str = "contact:contact.base:readonly"
    FEISHU_REQUEST_TIMEOUT_SECONDS: int = 15

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]

    @property
    def feishu_scope_list(self) -> list[str]:
        """Return the configured Feishu scopes as a list."""
        return [scope.strip() for scope in self.FEISHU_OAUTH_SCOPE.split(",") if scope.strip()]

    def validate_feishu_settings(self) -> None:
        """Fail fast when required Feishu settings are missing."""
        missing = [
            name
            for name, value in (
                ("FEISHU_APP_ID", self.FEISHU_APP_ID),
                ("FEISHU_APP_SECRET", self.FEISHU_APP_SECRET),
                ("FEISHU_BROWSER_REDIRECT_URI", self.FEISHU_BROWSER_REDIRECT_URI),
                ("FEISHU_FRONTEND_AUTH_COMPLETE_URL", self.FEISHU_FRONTEND_AUTH_COMPLETE_URL),
            )
            if not value
        ]
        if missing:
            missing_text = ", ".join(missing)
            raise RuntimeError(f"Missing required Feishu auth settings: {missing_text}")


# Global settings instance
auth_settings = AuthSettings()
