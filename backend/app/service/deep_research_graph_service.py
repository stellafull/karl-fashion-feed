"""Lifecycle-managed deep research graph service."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from os import getenv


class _AsyncPostgresSaverPlaceholder:
    @staticmethod
    def from_conn_string(*args, **kwargs):
        raise ModuleNotFoundError("langgraph.checkpoint.postgres.aio is not installed")


def _missing_build_research_graph(*args, **kwargs):
    raise ModuleNotFoundError("deep_agents.graph is not importable")


AsyncPostgresSaver = _AsyncPostgresSaverPlaceholder
build_research_graph = _missing_build_research_graph


def _build_postgres_checkpoint_dsn() -> str:
    host = getenv("POSTGRES_HOST", "localhost")
    port = getenv("POSTGRES_PORT", "5432")
    user = getenv("POSTGRES_USER", "postgres")
    password = getenv("POSTGRES_PASSWORD", "postgres123")
    database = getenv("POSTGRES_DB", "karl_feed_db")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


@dataclass(slots=True)
class DeepResearchGraphService:
    """Own the persistent LangGraph checkpointer and compiled research graph."""

    checkpoint_dsn: str
    _checkpointer_context: object | None = None
    _checkpointer: object | None = None
    _graph: object | None = None

    @classmethod
    def from_environment(cls) -> "DeepResearchGraphService":
        return cls(checkpoint_dsn=_build_postgres_checkpoint_dsn())

    async def start(self) -> None:
        """Open the Postgres checkpointer and compile the graph once."""
        if self._graph is not None:
            return

        global AsyncPostgresSaver, build_research_graph
        if AsyncPostgresSaver is _AsyncPostgresSaverPlaceholder:
            postgres_module = import_module("langgraph.checkpoint.postgres.aio")
            AsyncPostgresSaver = getattr(postgres_module, "AsyncPostgresSaver")
        if build_research_graph is _missing_build_research_graph:
            graph_module = import_module("deep_agents.graph")
            build_research_graph = getattr(graph_module, "build_research_graph")

        context = AsyncPostgresSaver.from_conn_string(self.checkpoint_dsn)
        checkpointer = await context.__aenter__()
        await checkpointer.setup()

        self._checkpointer_context = context
        self._checkpointer = checkpointer
        self._graph = build_research_graph(checkpointer=checkpointer)

    async def close(self) -> None:
        """Close the Postgres checkpointer cleanly."""
        if self._checkpointer_context is None:
            return

        await self._checkpointer_context.__aexit__(None, None, None)
        self._checkpointer_context = None
        self._checkpointer = None
        self._graph = None

    @property
    def graph(self):
        """Return the compiled graph after startup."""
        if self._graph is None:
            raise RuntimeError("deep research graph service is not started")
        return self._graph
