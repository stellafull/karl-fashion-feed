"""Database infrastructure helpers."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config.env import get_env


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    user: str
    password: str
    name: str


def get_database_settings() -> DatabaseSettings | None:
    values = {
        "POSTGRES_HOST": get_env("POSTGRES_HOST"),
        "POSTGRES_PORT": get_env("POSTGRES_PORT"),
        "POSTGRES_USER": get_env("POSTGRES_USER"),
        "POSTGRES_PASSWORD": get_env("POSTGRES_PASSWORD"),
        "POSTGRES_DB": get_env("POSTGRES_DB"),
    }
    if not any(values.values()):
        return None

    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} is not set. Configure PostgreSQL before starting backend services."
        )

    return DatabaseSettings(
        host=values["POSTGRES_HOST"] or "",
        port=int(values["POSTGRES_PORT"] or "0"),
        user=values["POSTGRES_USER"] or "",
        password=values["POSTGRES_PASSWORD"] or "",
        name=values["POSTGRES_DB"] or "",
    )


def build_database_url(settings: DatabaseSettings | None = None) -> str:
    resolved = settings or get_database_settings()
    if resolved is None:
        raise RuntimeError("PostgreSQL is not configured. Set POSTGRES_* environment variables first.")
    return (
        f"postgresql://{resolved.user}:{resolved.password}"
        f"@{resolved.host}:{resolved.port}/{resolved.name}"
    )


def require_database_url() -> str:
    return build_database_url()


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(require_database_url(), pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def get_db():
    """Yield a database session for request-scoped usage."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
