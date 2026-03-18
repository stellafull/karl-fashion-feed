from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from backend.app.config.embedding_config import (
    DENSE_EMBEDDING_CONFIG,
    DENSE_SUMMARIZATION_EMBEDDING_CONFIG,
    SPARSE_EMBEDDING_CONFIG,
)
from backend.app.service.RAG.embedding_service import (
    generate_article_summary_embedding,
    generate_dense_embedding,
    generate_sparse_embedding,
)


class EmbeddingServiceTest(unittest.TestCase):
    def test_generate_dense_embedding_uses_multimodal_api_for_text_only_inputs(self) -> None:
        calls: list[dict[str, object]] = []
        next_value = 0.0

        class FakeMultiModalEmbedding:
            @staticmethod
            def call(**kwargs: object):
                nonlocal next_value
                calls.append(kwargs)
                return type(
                    "Response",
                    (),
                    {
                        "output": {
                            "embeddings": [{"embedding": [next_value]}]
                        }
                    },
                )()
                
        def fake_call(**kwargs: object):
            nonlocal next_value
            response = FakeMultiModalEmbedding.call(**kwargs)
            next_value += 1.0
            return response

        with patch("backend.app.service.RAG.embedding_service.MultiModalEmbedding.call", side_effect=fake_call):
            embeddings = generate_dense_embedding(["foo", "bar"], None)

        self.assertEqual(embeddings, [[0.0], [1.0]])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(len(call["input"]) == 1 for call in calls))
        self.assertTrue(all(call["dimension"] == DENSE_EMBEDDING_CONFIG.vector_dimension for call in calls))

    def test_generate_dense_embedding_sends_text_and_image_together_for_image_lane(self) -> None:
        calls: list[dict[str, object]] = []
        next_value = 100.0

        class FakeMultiModalEmbedding:
            @staticmethod
            def call(**kwargs: object):
                nonlocal next_value
                calls.append(kwargs)
                return type(
                    "Response",
                    (),
                    {
                        "output": {
                            "embeddings": [{"embedding": [next_value]}]
                        }
                    },
                )()
        
        def fake_call(**kwargs: object):
            nonlocal next_value
            response = FakeMultiModalEmbedding.call(**kwargs)
            next_value += 1.0
            return response

        with patch("backend.app.service.RAG.embedding_service.MultiModalEmbedding.call", side_effect=fake_call):
            embeddings = generate_dense_embedding(
                ["text-only", "image+text", "empty-image"],
                [None, "https://cdn.example.com/look.jpg", "   "],
            )

        self.assertEqual(embeddings, [[100.0], [101.0], [102.0]])
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(calls[0]["input"]), 1)
        self.assertEqual(len(calls[1]["input"]), 2)
        self.assertEqual(len(calls[2]["input"]), 1)
        self.assertTrue(all(call["dimension"] == DENSE_EMBEDDING_CONFIG.vector_dimension for call in calls))

    def test_generate_dense_embedding_batches_text_only_requests(self) -> None:
        calls: list[dict[str, object]] = []
        next_value = 0.0

        class FakeMultiModalEmbedding:
            @staticmethod
            def call(**kwargs: object):
                nonlocal next_value
                calls.append(kwargs)
                return type(
                    "Response",
                    (),
                    {
                        "output": {
                            "embeddings": [{"embedding": [next_value]}]
                        }
                    },
                )()
        
        def fake_call(**kwargs: object):
            nonlocal next_value
            response = FakeMultiModalEmbedding.call(**kwargs)
            next_value += 1.0
            return response

        with patch("backend.app.service.RAG.embedding_service.MultiModalEmbedding.call", side_effect=fake_call), patch(
            "backend.app.service.RAG.embedding_service.DENSE_EMBEDDING_CONFIG",
            replace(DENSE_EMBEDDING_CONFIG, batch_size=2),
        ):
            embeddings = generate_dense_embedding(["a", "b", "c", "d", "e"], None)

        self.assertEqual(len(embeddings), 5)
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(len(call["input"]) == 1 for call in calls))
        self.assertTrue(all(call["dimension"] == DENSE_EMBEDDING_CONFIG.vector_dimension for call in calls))

    def test_generate_dense_embedding_batches_multimodal_requests(self) -> None:
        calls: list[dict[str, object]] = []
        next_value = 100.0

        class FakeMultiModalEmbedding:
            @staticmethod
            def call(**kwargs: object):
                nonlocal next_value
                calls.append(kwargs)
                return type(
                    "Response",
                    (),
                    {
                        "output": {
                            "embeddings": [{"embedding": [next_value]}]
                        }
                    },
                )()
        
        def fake_call(**kwargs: object):
            nonlocal next_value
            response = FakeMultiModalEmbedding.call(**kwargs)
            next_value += 1.0
            return response

        with patch("backend.app.service.RAG.embedding_service.MultiModalEmbedding.call", side_effect=fake_call), patch(
            "backend.app.service.RAG.embedding_service.DENSE_EMBEDDING_CONFIG",
            replace(DENSE_EMBEDDING_CONFIG, batch_size=2),
        ):
            embeddings = generate_dense_embedding(
                ["a", "b", "c", "d", "e"],
                [
                    "https://cdn.example.com/1.jpg",
                    "https://cdn.example.com/2.jpg",
                    "https://cdn.example.com/3.jpg",
                    "https://cdn.example.com/4.jpg",
                    "https://cdn.example.com/5.jpg",
                ],
            )

        self.assertEqual(len(embeddings), 5)
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(len(call["input"]) == 2 for call in calls))
        self.assertTrue(all(call["dimension"] == DENSE_EMBEDDING_CONFIG.vector_dimension for call in calls))

    def test_generate_article_summary_embedding_passes_dimension(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeTextEmbedding:
            @staticmethod
            def call(**kwargs: object):
                calls.append(kwargs)
                return type(
                    "Response",
                    (),
                    {"output": {"embeddings": [{"embedding": [1.0, 2.0]}]}},
                )()

        with patch("backend.app.service.RAG.embedding_service.TextEmbedding", FakeTextEmbedding):
            embedding = generate_article_summary_embedding("summary")

        self.assertEqual(embedding, [1.0, 2.0])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["input"], "summary")
        self.assertEqual(
            calls[0]["dimension"],
            DENSE_SUMMARIZATION_EMBEDDING_CONFIG.vector_dimension,
        )

    def test_generate_sparse_embedding_batches_requests(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeTextEmbedding:
            @staticmethod
            def call(**kwargs: object):
                calls.append(kwargs)
                texts = kwargs["input"]
                return type(
                    "Response",
                    (),
                    {
                        "output": {
                            "embeddings": [
                                {"sparse_embedding": {"indices": [index], "values": [float(index + 1)]}}
                                for index, _ in enumerate(texts)
                            ]
                        }
                    },
                )()

        with patch("backend.app.service.RAG.embedding_service.TextEmbedding", FakeTextEmbedding), patch(
            "backend.app.service.RAG.embedding_service.SPARSE_EMBEDDING_CONFIG",
            replace(SPARSE_EMBEDDING_CONFIG, batch_size=2),
        ):
            embeddings = generate_sparse_embedding(["a", "b", "c"])

        self.assertEqual(len(embeddings), 3)
        self.assertEqual([call["input"] for call in calls], [["a", "b"], ["c"]])
        self.assertEqual([call["output_type"] for call in calls], ["sparse", "sparse"])
        self.assertEqual(embeddings, [{0: 1.0}, {1: 2.0}, {0: 1.0}])


if __name__ == "__main__":
    unittest.main()
