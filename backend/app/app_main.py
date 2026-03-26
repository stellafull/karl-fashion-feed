"""FastAPI application entrypoint for the backend answer API."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config.auth_config import auth_settings
from backend.app.core.database import engine
from backend.app.models import ensure_article_storage_schema, ensure_auth_chat_schema
from backend.app.router import auth_router, chat_router, memory_router, rag_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup: validate auth settings and ensure schema
    if not auth_settings.AUTH_JWT_SECRET:
        raise RuntimeError("AUTH_JWT_SECRET is required but not set")

    ensure_article_storage_schema(engine)
    ensure_auth_chat_schema(engine)

    yield

    # Shutdown: cleanup if needed


app = FastAPI(
    title="KARL Fashion Feed Backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=auth_settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(memory_router, prefix="/api/v1")
app.include_router(rag_router, prefix="/api/v1")
