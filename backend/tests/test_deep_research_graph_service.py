from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.service.deep_research_graph_service import DeepResearchGraphService


class DeepResearchGraphServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_initializes_checkpointer_and_compiles_graph(self) -> None:
        checkpointer = AsyncMock()
        context = AsyncMock()
        context.__aenter__.return_value = checkpointer
        compiled_graph = object()
        from_conn_string_mock = MagicMock(return_value=context)
        fake_saver_class = SimpleNamespace(from_conn_string=from_conn_string_mock)

        with (
            patch(
                "backend.app.service.deep_research_graph_service.AsyncPostgresSaver",
                fake_saver_class,
            ),
            patch(
                "backend.app.service.deep_research_graph_service.build_research_graph",
                return_value=compiled_graph,
            ) as build_research_graph_mock,
        ):
            service = DeepResearchGraphService(checkpoint_dsn="postgresql://example")
            await service.start()

            self.assertIs(service.graph, compiled_graph)
            from_conn_string_mock.assert_called_once_with("postgresql://example")
            checkpointer.setup.assert_awaited_once()
            build_research_graph_mock.assert_called_once_with(checkpointer=checkpointer)

            await service.close()
            context.__aexit__.assert_awaited_once()
