from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ["QDRANT_URL"] = "http://localhost:6333"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base
from backend.app.models import Article, ArticleImage
from backend.app.service.RAG.article_rag_service import ArticleRagService
from backend.app.service.article_markdown_service import ArticleMarkdownService


class FakeQdrantService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, object]]]] = []

    def insert_data(self, collection_name: str, records: list[dict[str, object]]) -> int:
        self.calls.append((collection_name, records))
        return len(records)


class ArticleRagServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.markdown_service = ArticleMarkdownService(root_path=Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_insert_articles_builds_text_and_image_records(self) -> None:
        relative_path = "2026-03-18/article-1.md"
        self.markdown_service.write_markdown(
            relative_path=relative_path,
            content="# Title\n\nParagraph body.\n",
        )

        with self.session_factory() as session:
            session.add(
                Article(
                    article_id="article-1",
                    source_name="Vogue",
                    source_type="rss",
                    source_lang="en",
                    category="高端时装",
                    canonical_url="https://example.com/1",
                    original_url="https://example.com/1",
                    title_raw="raw title",
                    summary_raw="raw summary",
                    title_zh="中文标题",
                    summary_zh="中文摘要",
                    tags_json=["时尚"],
                    brands_json=["Karl"],
                    cluster_text="聚类文本",
                    markdown_rel_path=relative_path,
                    should_publish=True,
                    enrichment_status="done",
                    parse_status="done",
                    ingested_at=datetime(2026, 3, 18, 8, 0, 0),
                )
            )
            session.add_all(
                [
                    ArticleImage(
                        image_id="image-1",
                        article_id="article-1",
                        source_url="https://example.com/look.jpg",
                        normalized_url="https://example.com/look.jpg",
                        caption_raw="图片说明",
                        observed_description="模特穿着廓形外套",
                        contextual_interpretation="来自本季秀场",
                        visual_status="done",
                    ),
                    ArticleImage(
                        image_id="image-2",
                        article_id="article-1",
                        source_url="https://example.com/pending.jpg",
                        normalized_url="https://example.com/pending.jpg",
                        caption_raw="待分析图片",
                        visual_status="pending",
                    ),
                ]
            )
            session.commit()
            article = session.get(Article, "article-1")
            assert article is not None

        fake_qdrant = FakeQdrantService()
        service = ArticleRagService(
            session_factory=self.session_factory,
            markdown_service=self.markdown_service,
            qdrant_service=fake_qdrant,
        )

        with patch(
            "backend.app.service.RAG.article_rag_service.generate_dense_embedding",
            return_value=[[0.1, 0.2], [0.3, 0.4]],
        ) as dense_mock, patch(
            "backend.app.service.RAG.article_rag_service.generate_sparse_embedding",
            return_value=[{1: 0.5}, {2: 0.7}],
        ) as sparse_mock:
            result = service.insert_articles([article])

        self.assertEqual(result.publishable_articles, 1)
        self.assertEqual(result.text_units, 1)
        self.assertEqual(result.image_units, 1)
        self.assertEqual(result.inserted_units, 2)
        self.assertEqual(dense_mock.call_count, 1)
        self.assertEqual(sparse_mock.call_count, 1)
        self.assertEqual(len(fake_qdrant.calls), 1)
        collection_name, records = fake_qdrant.calls[0]
        self.assertEqual(collection_name, "kff_retrieval")
        self.assertEqual(records[0]["retrieval_unit_id"], "text:article-1:0")
        self.assertEqual(records[0]["content"], "Paragraph body.")
        self.assertIsNone(records[0]["article_image_id"])
        self.assertEqual(records[0]["ingested_at"], datetime(2026, 3, 18, 8, 0, 0))
        self.assertEqual(records[1]["retrieval_unit_id"], "image:image-1")
        self.assertEqual(records[1]["article_image_id"], "image-1")
        self.assertEqual(records[1]["ingested_at"], datetime(2026, 3, 18, 8, 0, 0))
        self.assertIn("图片说明", str(records[1]["content"]))
        self.assertIn("模特穿着廓形外套", str(records[1]["content"]))
        self.assertEqual(records[1]["dense_vector"], [0.3, 0.4])
        self.assertEqual(records[1]["sparse_vector"], {2: 0.7})


if __name__ == "__main__":
    unittest.main()
