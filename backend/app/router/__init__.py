"""FastAPI router package with lazy router loading."""

from __future__ import annotations

from importlib import import_module

_ROUTER_MODULES = {
    "auth_router": "backend.app.router.auth_router",
    "chat_router": "backend.app.router.chat_router",
    "deep_research_router": "backend.app.router.deep_research_router",
    "digest_router": "backend.app.router.digest_router",
    "memory_router": "backend.app.router.memory_router",
    "rag_router": "backend.app.router.rag_router",
}

__all__ = list(_ROUTER_MODULES)


def __getattr__(name: str):
    """Lazily resolve router objects to avoid importing heavy modules at package import time."""
    module_path = _ROUTER_MODULES.get(name)
    if not module_path:
        raise AttributeError(name)
    return import_module(module_path).router
