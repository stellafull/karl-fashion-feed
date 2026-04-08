"""FastAPI router package."""

from backend.app.router.auth_router import router as auth_router
from backend.app.router.chat_router import router as chat_router
from backend.app.router.deep_research_router import router as deep_research_router
from backend.app.router.digest_router import router as digest_router
from backend.app.router.memory_router import router as memory_router
from backend.app.router.rag_router import router as rag_router

__all__ = [
    "auth_router",
    "chat_router",
    "deep_research_router",
    "digest_router",
    "memory_router",
    "rag_router",
]
