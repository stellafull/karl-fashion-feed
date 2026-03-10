"""ASGI entrypoint for the Fashion Feed FastAPI backend."""

from __future__ import annotations

try:
    from fastapi import FastAPI
except ImportError:  # pragma: no cover - exercised in unit tests when dependency is absent.
    FastAPI = None


APP_NAME = "fashion-feed-backend"
APP_VERSION = "0.1.0"


def create_app() -> "FastAPI":
    """Build the FastAPI application when runtime dependencies are installed."""
    if FastAPI is None:
        raise RuntimeError(
            "fastapi is not installed. Install backend dependencies before starting the API."
        )

    app = FastAPI(
        title="Fashion Feed Backend",
        version=APP_VERSION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    @app.get("/healthz", tags=["system"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app() if FastAPI is not None else None
