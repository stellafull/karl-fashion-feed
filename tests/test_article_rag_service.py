"""Tests for article RAG ingestion under the digest runtime contract."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from backend.app.core.database import Base
from backend.app.models import Article, ArticleImage
from backend.app.service.RAG.article_rag_service import (
    ArticleRagService,
    build_image_retrieval_content,
)
from backend.app.service.article_parse_service import ArticleMarkdownService


class _FakeQdrantService:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def upsert_data(self, collection_name: str, records: list[dict[str, object]]) -> int:
        self.records = list(records)
        return len(records)


class ArticleRagServiceTest(unittest.TestCase):
    """Verify RAG indexing follows the new article/digest contract."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def test_upsert_articles_indexes_parse_complete_articles(self) -> None:
        article_1_id = str(uuid4())
        article_2_id = str(uuid4())
        markdown_service = ArticleMarkdownService(Path(self.temp_dir.name))
        markdown_service.write_markdown(
            relative_path="2026-03-26/article-1.md",
            content="# A1\n\np1\n",
        )
        markdown_service.write_markdown(
            relative_path="2026-03-26/article-2.md",
            content="# A2\n\np2\n",
        )

        with self.session_factory() as session:
            session.add(
                _build_article(
                    article_id=article_1_id,
                    markdown_rel_path="2026-03-26/article-1.md",
                    ingested_at=datetime(2026, 3, 26, 8, 5, tzinfo=UTC).replace(tzinfo=None),
                )
            )
            session.add(
                _build_article(
                    article_id=article_2_id,
                    markdown_rel_path="2026-03-26/article-2.md",
                    ingested_at=datetime(2026, 3, 26, 8, 0, tzinfo=UTC).replace(tzinfo=None),
                )
            )
            session.commit()

        fake_qdrant = _FakeQdrantService()
        with (
            patch("backend.app.service.RAG.article_rag_service.SessionLocal", self.session_factory),
            patch("backend.app.service.RAG.article_rag_service.QdrantService", return_value=fake_qdrant),
            patch(
                "backend.app.service.RAG.article_rag_service.split_markdown_into_text_chunks",
                side_effect=[
                    [
                        _build_text_chunk(article_2_id, 0, "a2-chunk-0"),
                        _build_text_chunk(article_2_id, 1, "a2-chunk-1"),
                    ],
                    [
                        _build_text_chunk(article_1_id, 0, "a1-chunk-0"),
                        _build_text_chunk(article_1_id, 1, "a1-chunk-1"),
                    ],
                ],
            ),
            patch(
                "backend.app.service.RAG.article_rag_service.generate_dense_embedding",
                side_effect=lambda texts, image_inputs=None: [[1.0, 0.0] for _ in texts],
            ),
            patch(
                "backend.app.service.RAG.article_rag_service.generate_sparse_embedding",
                side_effect=lambda texts: [{0: 1.0} for _ in texts],
            ),
        ):
            service = ArticleRagService()
            service._markdown_service = markdown_service
            with patch.object(
                service._markdown_service,
                "read_markdown",
                wraps=service._markdown_service.read_markdown,
            ) as read_markdown:
                result = service.upsert_articles([article_1_id, article_2_id])

        self.assertEqual(result.indexed_articles, 2)
        self.assertEqual(result.text_units, 4)
        self.assertEqual(
            read_markdown.call_args.kwargs["relative_path"],
            "2026-03-26/article-1.md",
        )

    def test_upsert_articles_uses_parsed_source_markdown_and_source_text_image_projection(self) -> None:
        eligible_article_id = str(uuid4())
        skipped_article_id = str(uuid4())
        captured_dense_calls: list[dict[str, Any]] = []
        markdown_service = ArticleMarkdownService(Path(self.temp_dir.name))
        markdown_path = "2026-03-26/article-1.md"
        markdown_service.write_markdown(
            relative_path=markdown_path,
            content="# Source title\n\nFirst paragraph.\n\nSecond paragraph.\n",
        )

        with self.session_factory() as session:
            session.add(
                Article(
                    article_id=eligible_article_id,
                    source_name="Vogue Runway",
                    source_type="rss",
                    source_lang="en",
                    category="fashion",
                    canonical_url=f"https://example.com/{eligible_article_id}",
                    original_url=f"https://example.com/original/{eligible_article_id}",
                    title_raw="Raw title",
                    summary_raw="Raw summary",
                    discovered_at=datetime(2026, 3, 26, 7, 0, tzinfo=UTC).replace(tzinfo=None),
                    ingested_at=datetime(2026, 3, 26, 7, 5, tzinfo=UTC).replace(tzinfo=None),
                    metadata_json={},
                    parse_status="done",
                    markdown_rel_path=markdown_path,
                )
            )
            session.add(
                Article(
                    article_id=skipped_article_id,
                    source_name="WWD",
                    source_type="rss",
                    source_lang="en",
                    category="fashion",
                    canonical_url=f"https://example.com/{skipped_article_id}",
                    original_url=f"https://example.com/original/{skipped_article_id}",
                    title_raw="Skip title",
                    summary_raw="Skip summary",
                    discovered_at=datetime(2026, 3, 26, 7, 0, tzinfo=UTC).replace(tzinfo=None),
                    ingested_at=datetime(2026, 3, 26, 7, 10, tzinfo=UTC).replace(tzinfo=None),
                    metadata_json={},
                    parse_status="pending",
                    markdown_rel_path=markdown_path,
                )
            )
            session.add(
                ArticleImage(
                    image_id=str(uuid4()),
                    article_id=eligible_article_id,
                    source_url="https://example.com/image.jpg",
                    normalized_url="https://example.com/image.jpg",
                    caption_raw="Look 1 backstage",
                    alt_text="Model detail",
                    credit_raw="Photo: Karl",
                    context_snippet="Backstage fitting notes",
                    ocr_text="LOOK 1",
                    observed_description="A model standing backstage.",
                    contextual_interpretation="Backstage mood before the show.",
                    visual_status="pending",
                )
            )
            session.commit()

        fake_qdrant = _FakeQdrantService()
        with (
            patch("backend.app.service.RAG.article_rag_service.SessionLocal", self.session_factory),
            patch("backend.app.service.RAG.article_rag_service.QdrantService", return_value=fake_qdrant),
            patch(
                "backend.app.service.RAG.article_rag_service.generate_dense_embedding",
                side_effect=lambda texts, image_inputs=None: _capture_dense_embedding_call(
                    captured_dense_calls,
                    texts,
                    image_inputs,
                ),
            ),
            patch(
                "backend.app.service.RAG.article_rag_service.generate_sparse_embedding",
                side_effect=lambda texts: [{0: 1.0} for _ in texts],
            ),
        ):
            service = ArticleRagService()
            service._markdown_service = markdown_service
            result = service.upsert_articles([eligible_article_id, skipped_article_id])

        self.assertEqual(result.indexed_articles, 1)
        self.assertGreaterEqual(result.text_units, 1)
        self.assertEqual(result.image_units, 1)
        self.assertEqual(result.upserted_units, len(fake_qdrant.records))
        self.assertTrue(all(record["article_id"] == eligible_article_id for record in fake_qdrant.records))
        text_records = [record for record in fake_qdrant.records if record["modality"] == "text"]
        self.assertGreaterEqual(len(text_records), 1)
        self.assertTrue(any("First paragraph." in str(record["content"]) for record in text_records))
        image_records = [record for record in fake_qdrant.records if record["modality"] == "image"]
        self.assertEqual(len(image_records), 1)
        self.assertIn("Look 1 backstage", image_records[0]["content"])
        self.assertIn("Model detail", image_records[0]["content"])
        self.assertNotIn("Raw title", image_records[0]["content"])
        self.assertNotIn("Raw summary", image_records[0]["content"])
        self.assertNotIn("LOOK 1", image_records[0]["content"])
        self.assertNotIn("A model standing backstage.", image_records[0]["content"])
        self.assertNotIn("Backstage mood before the show.", image_records[0]["content"])
        self.assertEqual(image_records[0]["tags_json"], [])
        self.assertEqual(image_records[0]["brands_json"], [])
        image_dense_call = next(
            call
            for call in captured_dense_calls
            if str(image_records[0]["content"]) in call["texts"]
        )
        self.assertEqual(image_dense_call["image_inputs"], ["https://example.com/image.jpg"])

    def test_image_retrieval_uses_source_text_without_visual_analysis(self) -> None:
        article = _build_article(article_id=str(uuid4()), markdown_rel_path="2026-03-26/source.md")
        image = _build_image(article.article_id)

        content = build_image_retrieval_content(article, image)

        self.assertIn("caption", content)
        self.assertIn(image.caption_raw, content)
        self.assertNotIn(image.ocr_text, content)
        self.assertNotIn(image.observed_description, content)
        self.assertNotIn(image.contextual_interpretation, content)

    def test_images_without_source_text_projection_are_skipped(self) -> None:
        article = _build_article(article_id=str(uuid4()), markdown_rel_path="2026-03-26/source.md")
        image = _build_image(article.article_id)
        image.caption_raw = ""
        image.alt_text = ""
        image.credit_raw = ""
        image.context_snippet = ""

        with self.session_factory() as session:
            session.add(image)
            session.commit()

        with patch("backend.app.service.RAG.article_rag_service.SessionLocal", self.session_factory):
            service = ArticleRagService()
            records = service._build_image_records([article])

        self.assertEqual(records, [])


def _capture_dense_embedding_call(
    captured_dense_calls: list[dict[str, Any]],
    texts: list[str],
    image_inputs: list[str | None] | None,
) -> list[list[float]]:
    captured_dense_calls.append(
        {
            "texts": list(texts),
            "image_inputs": None if image_inputs is None else list(image_inputs),
        }
    )
    return [[1.0, 0.0] for _ in texts]


def _build_article(
    article_id: str,
    markdown_rel_path: str,
    ingested_at: datetime | None = None,
) -> Article:
    discovered = datetime(2026, 3, 26, 7, 0, tzinfo=UTC).replace(tzinfo=None)
    return Article(
        article_id=article_id,
        source_name="Vogue Runway",
        source_type="rss",
        source_lang="en",
        category="fashion",
        canonical_url=f"https://example.com/{article_id}",
        original_url=f"https://example.com/original/{article_id}",
        title_raw="Raw title",
        summary_raw="Raw summary",
        discovered_at=discovered,
        ingested_at=ingested_at or datetime(2026, 3, 26, 7, 5, tzinfo=UTC).replace(tzinfo=None),
        metadata_json={},
        parse_status="done",
        markdown_rel_path=markdown_rel_path,
    )


def _build_image(article_id: str) -> ArticleImage:
    return ArticleImage(
        image_id=str(uuid4()),
        article_id=article_id,
        source_url="https://example.com/image.jpg",
        normalized_url="https://example.com/image.jpg",
        caption_raw="caption from source",
        alt_text="alt from source",
        credit_raw="credit from source",
        context_snippet="context from source",
        ocr_text="OCR SHOULD NOT ENTER RETRIEVAL",
        observed_description="VISUAL DESCRIPTION SHOULD NOT ENTER RETRIEVAL",
        contextual_interpretation="VISUAL INTERPRETATION SHOULD NOT ENTER RETRIEVAL",
        visual_status="pending",
    )


def _build_text_chunk(article_id: str, chunk_index: int, content: str) -> dict[str, Any]:
    return {
        "chunk_id": f"{article_id}:text:{chunk_index}",
        "type": "text",
        "order": chunk_index,
        "source_id": article_id,
        "page_content": content,
        "metadata": {
            "modality": "text",
            "chunk_index": chunk_index,
            "section_index": 0,
            "heading_path": [],
            "text_start": 0,
            "text_end": len(content),
            "prev_chunk_id": None,
            "next_chunk_id": None,
        },
    }
