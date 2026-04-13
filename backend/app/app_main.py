"""FastAPI application entrypoint for the backend answer API."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config.auth_config import auth_settings
from backend.app.core.database import engine
from backend.app.models import ensure_article_storage_schema, ensure_auth_chat_schema
from backend.app.router.auth_router import router as auth_router
from backend.app.router.chat_router import router as chat_router
from backend.app.router.deep_research_router import router as deep_research_router
from backend.app.router.digest_router import router as digest_router
from backend.app.router.memory_router import router as memory_router
from backend.app.router.rag_router import router as rag_router
from backend.app.service.deep_research_graph_service import DeepResearchGraphService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup: validate auth settings and ensure schema
    if not auth_settings.AUTH_JWT_SECRET:
        raise RuntimeError("AUTH_JWT_SECRET is required but not set")

    auth_settings.validate_feishu_settings()
    ensure_article_storage_schema(engine)
    ensure_auth_chat_schema(engine)
    deep_research_graph_service = DeepResearchGraphService.from_environment()
    await deep_research_graph_service.start()
    app.state.deep_research_graph_service = deep_research_graph_service

    yield

    # Shutdown: cleanup if needed
    await app.state.deep_research_graph_service.close()


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
app.include_router(deep_research_router, prefix="/api/v1")
app.include_router(memory_router, prefix="/api/v1")
app.include_router(rag_router, prefix="/api/v1")
app.include_router(digest_router, prefix="/api/v1")
