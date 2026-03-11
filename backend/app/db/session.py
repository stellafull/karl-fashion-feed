"""SQLAlchemy engine and session helpers."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.config.database import require_database_url
from backend.app.db.base import Base


def create_engine_from_url(database_url: str) -> Engine:
    engine_kwargs = {"future": True}
    if database_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            engine_kwargs["poolclass"] = StaticPool
    return create_engine(database_url, **engine_kwargs)


def get_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    engine = create_engine_from_url(database_url or require_database_url())
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def create_all_tables(database_url: str | None = None) -> None:
    engine = create_engine_from_url(database_url or require_database_url())
    Base.metadata.create_all(engine)
