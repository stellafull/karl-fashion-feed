"""Database bootstrap and session helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from threading import RLock
from typing import Iterator, Mapping

from dotenv import find_dotenv, load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

_ = load_dotenv(find_dotenv())

Base = declarative_base()

_ENGINE_CACHE: dict[str, Engine] = {}
_SESSION_FACTORY_CACHE: dict[str, sessionmaker[Session]] = {}
_DEFAULT_LOCK = RLock()


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    user: str
    password: str
    name: str
    port: int = 5432


def build_database_url(settings: DatabaseSettings) -> str:
    """Build the canonical PostgreSQL URL from explicit settings."""
    return (
        "postgresql://"
        f"{settings.user}:{settings.password}@{settings.host}:{settings.port}/{settings.name}"
    )


def require_database_url(environ: Mapping[str, str] | None = None) -> str:
    """Return the runtime database URL or raise when required env is missing."""
    env = environ if environ is not None else os.environ
    required_fields = {
        "POSTGRES_HOST": env.get("POSTGRES_HOST", "").strip(),
        "POSTGRES_USER": env.get("POSTGRES_USER", "").strip(),
        "POSTGRES_PASSWORD": env.get("POSTGRES_PASSWORD", "").strip(),
        "POSTGRES_DB": env.get("POSTGRES_DB", "").strip(),
    }
    missing = [name for name, value in required_fields.items() if not value]
    if missing:
        missing_names = ", ".join(missing)
        raise RuntimeError(
            "Database configuration is incomplete. Missing environment variables: "
            f"{missing_names}"
        )

    raw_port = env.get("POSTGRES_PORT", "5432").strip() or "5432"
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError(f"POSTGRES_PORT must be an integer, got {raw_port!r}") from exc

    return build_database_url(
        DatabaseSettings(
            host=required_fields["POSTGRES_HOST"],
            port=port,
            user=required_fields["POSTGRES_USER"],
            password=required_fields["POSTGRES_PASSWORD"],
            name=required_fields["POSTGRES_DB"],
        )
    )


def create_engine_from_url(database_url: str, **engine_kwargs: object) -> Engine:
    """Create an engine from an explicit URL without touching env-driven globals."""
    normalized_url = _normalize_database_url(database_url)
    options: dict[str, object] = {"pool_pre_ping": True, **engine_kwargs}
    if normalized_url.startswith("sqlite") and ":memory:" in normalized_url:
        options.setdefault("connect_args", {"check_same_thread": False})
        options.setdefault("poolclass", StaticPool)
    return create_engine(normalized_url, **options)


def get_engine(database_url: str | None = None) -> Engine:
    """Return a cached engine for an explicit URL or the current env-backed URL."""
    cache_key = _resolve_database_url(database_url)
    with _DEFAULT_LOCK:
        return _get_or_create_engine(cache_key)


def get_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    """Return a cached session factory bound to the resolved engine URL."""
    cache_key = _resolve_database_url(database_url)
    with _DEFAULT_LOCK:
        session_factory = _SESSION_FACTORY_CACHE.get(cache_key)
        if session_factory is None:
            session_factory = _build_session_factory(_get_or_create_engine(cache_key))
            _SESSION_FACTORY_CACHE[cache_key] = session_factory
        return session_factory


def reset_database_caches(database_url: str | None = None) -> None:
    """Dispose cached engines/session factories for tests and in-process reconfiguration."""
    with _DEFAULT_LOCK:
        if database_url is None:
            cache_keys = set(_ENGINE_CACHE) | set(_SESSION_FACTORY_CACHE)
        else:
            cache_keys = {_normalize_database_url(database_url)}

        for cache_key in cache_keys:
            _SESSION_FACTORY_CACHE.pop(cache_key, None)
            engine = _ENGINE_CACHE.pop(cache_key, None)
            if engine is not None:
                engine.dispose()


def create_all_tables(engine: Engine | None = None) -> None:
    """Create all registered ORM tables on the target engine."""
    import backend.app.models  # noqa: F401

    Base.metadata.create_all(bind=engine or get_engine())


def get_db(session_factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """Yield a database session and close it after use."""
    factory = session_factory or get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _get_or_create_engine(cache_key: str) -> Engine:
    engine = _ENGINE_CACHE.get(cache_key)
    if engine is None:
        engine = create_engine_from_url(cache_key)
        _ENGINE_CACHE[cache_key] = engine
    return engine


def _resolve_database_url(database_url: str | None) -> str:
    if database_url is None:
        return _normalize_database_url(require_database_url())
    return _normalize_database_url(database_url)


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url
