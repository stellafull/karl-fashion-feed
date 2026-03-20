from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.service.RAG.reranker_service import RerankResult, RerankerService


class RerankerServiceTest(unittest.TestCase):
    def test_rerank_returns_ranked_documents(self) -> None:
        service = RerankerService()

        class FakeResponse:
            status_code = 200
            code = ""
            message = ""
            output = type(
                "Output",
                (),
                {
                    "results": [
                        type("Item", (), {"index": 1, "relevance_score": 0.91})(),
                        type("Item", (), {"index": 0, "relevance_score": 0.72})(),
                    ]
                },
            )()

        with patch(
            "backend.app.service.RAG.reranker_service.TextReRank.call",
            return_value=FakeResponse(),
        ) as rerank_call:
            results = service.rerank(
                "structured coat",
                ["doc-0", "doc-1"],
                top_n=2,
            )

        self.assertEqual(
            results,
            [
                RerankResult(index=1, relevance_score=0.91, document="doc-1"),
                RerankResult(index=0, relevance_score=0.72, document="doc-0"),
            ],
        )
        rerank_call.assert_called_once()

    def test_rerank_rejects_empty_query(self) -> None:
        service = RerankerService()

        with self.assertRaisesRegex(ValueError, "must not be empty"):
            service.rerank("   ", ["doc-0"], top_n=1)
