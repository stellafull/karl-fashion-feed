from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from app.config.embedding_config import (
    DENSE_EMBEDDING_CONFIG,
    DENSE_SUMMARIZATION_EMBEDDING_CONFIG,
    SPARSE_EMBEDDING_CONFIG,
)
from app.service.RAG.embedding_service import (
    generate_article_summary_embedding,
    generate_dense_embedding,
    generate_sparse_embedding,
)


class EmbeddingServiceTest(unittest.TestCase):
    def test_generate_dense_embedding_uses_text_only_when_images_missing(self) -> None:
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
                                {"embedding": [float(index)]} for index, _ in enumerate(texts)
                            ]
                        }
                    },
                )()

        with patch("app.service.RAG.embedding_service.TextEmbedding", FakeTextEmbedding):
            embeddings = generate_dense_embedding(["foo", "bar"], [])

        self.assertEqual(embeddings, [[0.0], [1.0]])
        self.assertEqual(
            calls,
            [
                {
                    "model": unittest.mock.ANY,
                    "input": ["foo", "bar"],
                    "api_key": unittest.mock.ANY,
                    "dimension": DENSE_EMBEDDING_CONFIG.vector_dimension,
                }
            ],
        )

    def test_generate_dense_embedding_splits_text_only_and_multimodal_batches(self) -> None:
        text_calls: list[dict[str, object]] = []
        multimodal_calls: list[dict[str, object]] = []

        class FakeTextEmbedding:
            @staticmethod
            def call(**kwargs: object):
                text_calls.append(kwargs)
                texts = kwargs["input"]
                return type(
                    "Response",
                    (),
                    {
                        "output": {
                            "embeddings": [
                                {"embedding": [float(index)]} for index, _ in enumerate(texts)
                            ]
                        }
                    },
                )()

        class FakeMultiModalEmbedding:
            @staticmethod
            def call(**kwargs: object):
                multimodal_calls.append(kwargs)
                items = kwargs["input"]
                return type(
                    "Response",
                    (),
                    {
                        "output": {
                            "embeddings": [
                                {"embedding": [100.0 + index]} for index, _ in enumerate(items)
                            ]
                        }
                    },
                )()

        with patch("app.service.RAG.embedding_service.TextEmbedding", FakeTextEmbedding), patch(
            "app.service.RAG.embedding_service.MultiModalEmbedding",
            FakeMultiModalEmbedding,
        ):
            embeddings = generate_dense_embedding(
                ["text-only", "image+text", "empty-image"],
                [None, "https://cdn.example.com/look.jpg", "   "],
            )

        self.assertEqual(embeddings, [[0.0], [100.0], [1.0]])
        self.assertEqual(len(text_calls), 1)
        self.assertEqual(text_calls[0]["input"], ["text-only", "empty-image"])
        self.assertEqual(text_calls[0]["dimension"], DENSE_EMBEDDING_CONFIG.vector_dimension)
        self.assertEqual(len(multimodal_calls), 1)
        self.assertEqual(len(multimodal_calls[0]["input"]), 1)
        self.assertEqual(multimodal_calls[0]["dimension"], DENSE_EMBEDDING_CONFIG.vector_dimension)

    def test_generate_dense_embedding_batches_text_only_requests(self) -> None:
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
                            "embeddings": [{"embedding": [float(index)]} for index, _ in enumerate(texts)]
                        }
                    },
                )()

        with patch("app.service.RAG.embedding_service.TextEmbedding", FakeTextEmbedding), patch(
            "app.service.RAG.embedding_service.DENSE_EMBEDDING_CONFIG",
            replace(DENSE_EMBEDDING_CONFIG, batch_size=2),
        ):
            embeddings = generate_dense_embedding(["a", "b", "c", "d", "e"], None)

        self.assertEqual(len(embeddings), 5)
        self.assertEqual([call["input"] for call in calls], [["a", "b"], ["c", "d"], ["e"]])
        self.assertTrue(all(call["dimension"] == DENSE_EMBEDDING_CONFIG.vector_dimension for call in calls))

    def test_generate_dense_embedding_batches_multimodal_requests(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeMultiModalEmbedding:
            @staticmethod
            def call(**kwargs: object):
                calls.append(kwargs)
                items = kwargs["input"]
                return type(
                    "Response",
                    (),
                    {
                        "output": {
                            "embeddings": [{"embedding": [100.0 + index]} for index, _ in enumerate(items)]
                        }
                    },
                )()

        with patch("app.service.RAG.embedding_service.MultiModalEmbedding", FakeMultiModalEmbedding), patch(
            "app.service.RAG.embedding_service.DENSE_EMBEDDING_CONFIG",
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
        self.assertEqual([len(call["input"]) for call in calls], [2, 2, 1])
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

        with patch("app.service.RAG.embedding_service.TextEmbedding", FakeTextEmbedding):
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

        with patch("app.service.RAG.embedding_service.TextEmbedding", FakeTextEmbedding), patch(
            "app.service.RAG.embedding_service.SPARSE_EMBEDDING_CONFIG",
            replace(SPARSE_EMBEDDING_CONFIG, batch_size=2),
        ):
            embeddings = generate_sparse_embedding(["a", "b", "c"])

        self.assertEqual(len(embeddings), 3)
        self.assertEqual([call["input"] for call in calls], [["a", "b"], ["c"]])
        self.assertEqual([call["output_type"] for call in calls], ["sparse", "sparse"])
        self.assertEqual(embeddings, [{0: 1.0}, {1: 2.0}, {0: 1.0}])


if __name__ == "__main__":
    unittest.main()
