FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app

WORKDIR /app

COPY backend /app/backend
RUN uv sync --frozen --project backend

EXPOSE 8000

CMD ["uv", "run", "--project", "backend", "uvicorn", "backend.app.app_main:app", "--host", "0.0.0.0", "--port", "8000"]
