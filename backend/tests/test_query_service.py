from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from qdrant_client.http import models
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.database import Base
from backend.app.models import Article, ArticleImage
from backend.app.schemas.rag_query import QueryFilters, QueryPlan, TimeRange
from backend.app.service.RAG.query_service import QueryService
from backend.app.service.article_parse_service import ArticleMarkdownService


class FakeQdrantService:
    def __init__(self, *, text_points: list[models.ScoredPoint], image_points: list[models.ScoredPoint]) -> None:
        self.text_points = text_points
        self.image_points = image_points
        self.calls: list[tuple[str, int, models.Filter | None]] = []
        self.filter_calls: list[dict[str, object]] = []

    def build_metadata_filter(
        self,
        *,
        modality: str,
        source_names: list[str] | None = None,
        categories: list[str] | None = None,
        tags: list[str] | None = None,
        brands: list[str] | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> models.Filter:
        self.filter_calls.append(
            {
                "modality": modality,
                "source_names": source_names,
                "categories": categories,
                "tags": tags,
                "brands": brands,
                "start_at": start_at,
                "end_at": end_at,
            }
        )
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="modality",
                    match=models.MatchAny(any=[modality]),
                )
            ]
        )

    def search_hybrid(
        self,
        collection_name: str,
        dense_vector: list[float],
        sparse_vector: dict[int, float],
        *,
        limit: int,
        filters: models.Filter | None = None,
    ) -> list[models.ScoredPoint]:
        del collection_name, dense_vector, sparse_vector
        self.calls.append(("hybrid", limit, filters))
        modality = filters.must[0].match.any[0] if filters is not None else "text"
        return list(self.text_points if modality == "text" else self.image_points)

    def search_dense(
        self,
        collection_name: str,
        query_vector: list[float],
        *,
        limit: int,
        filters: models.Filter | None = None,
    ) -> list[models.ScoredPoint]:
        del collection_name, query_vector
        self.calls.append(("dense", limit, filters))
        return list(self.image_points)


class FakeRerankerService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], int]] = []

    def rerank(self, query: str, documents: list[str], top_n: int):
        self.calls.append((query, documents, top_n))
        return [
            type(
                "FakeRerankResult",
                (),
                {"index": 0, "relevance_score": 0.95, "document": documents[0]},
            )()
        ]


class _InlineFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


class _InlineExecutor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        del exc_type, exc_value, traceback
        return None

    def submit(self, func, *args, **kwargs):
        return _InlineFuture(func(*args, **kwargs))


class QueryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.markdown_service = ArticleMarkdownService(root_path=Path(self.temp_dir.name))
        relative_path = "2026-03-18/article-1.md"
        self.markdown_service.write_markdown(
            relative_path=relative_path,
            content="# Title\n\nParagraph body.\n\n## Details\nSecond paragraph.\n",
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
                    markdown_rel_path=relative_path,
                    should_publish=True,
                    enrichment_status="done",
                    parse_status="done",
                    ingested_at=datetime(2026, 3, 18, 8, 0, 0),
                )
            )
            session.add(
                ArticleImage(
                    image_id="image-1",
                    article_id="article-1",
                    source_url="https://example.com/look.jpg",
                    normalized_url="https://example.com/look.jpg",
                    alt_text="look alt",
                    credit_raw="photo by x",
                    context_snippet="context line",
                    observed_description="模特穿着廓形外套",
                    ocr_text="OCR TEXT",
                    caption_raw="图片说明",
                    contextual_interpretation="来自秀场",
                    visual_status="done",
                )
            )
            session.commit()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_execute_text_only_returns_markdown_grounded_hits(self) -> None:
        qdrant_service = FakeQdrantService(
            text_points=[
                self._make_point(
                    retrieval_unit_id="text:article-1:0",
                    article_id="article-1",
                    article_image_id=None,
                    chunk_index=0,
                    modality="text",
                    content="payload stale content",
                )
            ],
            image_points=[],
        )
        reranker_service = FakeRerankerService()
        service = QueryService(
            session_factory=self.session_factory,
            markdown_service=self.markdown_service,
            qdrant_service=qdrant_service,
            reranker_service=reranker_service,
        )

        with patch(
            "backend.app.service.RAG.query_service.generate_dense_embedding",
            return_value=[[0.1, 0.2]],
        ), patch(
            "backend.app.service.RAG.query_service.generate_sparse_embedding",
            return_value=[{1: 1.0}],
        ):
            result = service.execute(
                QueryPlan(
                    plan_type="text_only",
                    text_query="structured coat",
                    filters=QueryFilters(
                        time_range=TimeRange(
                            start_at=datetime(2026, 3, 18, 0, 0, 0),
                            end_at=datetime(2026, 3, 19, 0, 0, 0),
                        )
                    ),
                )
            )

        self.assertEqual(len(result.text_results), 1)
        self.assertEqual(result.text_results[0].content, "Paragraph body.")
        self.assertEqual(result.text_results[0].citation_locator.chunk_index, 0)
        self.assertEqual(result.text_results[0].title_zh, "中文标题")
        self.assertEqual(reranker_service.calls[0][0], "structured coat")
        self.assertEqual(result.packages[0].article_id, "article-1")

    def test_execute_image_only_text_query_returns_grounded_image_hits(self) -> None:
        qdrant_service = FakeQdrantService(
            text_points=[],
            image_points=[
                self._make_point(
                    retrieval_unit_id="image:image-1",
                    article_id="article-1",
                    article_image_id="image-1",
                    chunk_index=None,
                    modality="image",
                    content="payload stale content",
                )
            ],
        )
        reranker_service = FakeRerankerService()
        service = QueryService(
            session_factory=self.session_factory,
            markdown_service=self.markdown_service,
            qdrant_service=qdrant_service,
            reranker_service=reranker_service,
        )

        with patch(
            "backend.app.service.RAG.query_service.generate_dense_embedding",
            return_value=[[0.1, 0.2]],
        ), patch(
            "backend.app.service.RAG.query_service.generate_sparse_embedding",
            return_value=[{1: 1.0}],
        ):
            result = service.execute(
                QueryPlan(
                    plan_type="image_only",
                    text_query="red coat",
                    filters=QueryFilters(),
                )
            )

        self.assertEqual(len(result.image_results), 1)
        expected_content = "\n".join(
            [
                "图片说明",
                "look alt",
                "photo by x",
                "context line",
                "OCR TEXT",
                "模特穿着廓形外套",
                "来自秀场",
                "中文标题",
                "中文摘要",
                "时尚",
                "Karl",
            ]
        )
        image_hit = result.image_results[0]
        self.assertEqual(image_hit.content, expected_content)
        self.assertEqual(image_hit.source_url, "https://example.com/look.jpg")
        self.assertEqual(image_hit.caption_raw, "图片说明")
        self.assertEqual(image_hit.alt_text, "look alt")
        self.assertEqual(image_hit.credit_raw, "photo by x")
        self.assertEqual(image_hit.context_snippet, "context line")
        self.assertEqual(image_hit.ocr_text, "OCR TEXT")
        self.assertEqual(image_hit.observed_description, "模特穿着廓形外套")
        self.assertEqual(image_hit.contextual_interpretation, "来自秀场")
        self.assertEqual(len(image_hit.grounding_texts), 2)
        self.assertEqual(
            {(locator.article_id, locator.article_image_id, locator.chunk_index) for locator in result.citation_locators},
            {
                ("article-1", "image-1", None),
                ("article-1", None, 0),
                ("article-1", None, 1),
            },
        )
        self.assertEqual(reranker_service.calls[0][0], "red coat")

    def test_execute_image_only_image_query_skips_reranker(self) -> None:
        qdrant_service = FakeQdrantService(
            text_points=[],
            image_points=[
                self._make_point(
                    retrieval_unit_id="image:image-1",
                    article_id="article-1",
                    article_image_id="image-1",
                    chunk_index=None,
                    modality="image",
                    content="payload stale content",
                )
            ],
        )
        reranker_service = FakeRerankerService()
        service = QueryService(
            session_factory=self.session_factory,
            markdown_service=self.markdown_service,
            qdrant_service=qdrant_service,
            reranker_service=reranker_service,
        )

        with patch(
            "backend.app.service.RAG.query_service.generate_dense_embedding",
            return_value=[[0.1, 0.2]],
        ) as dense_mock, patch(
            "backend.app.service.RAG.query_service.generate_sparse_embedding",
            side_effect=AssertionError("generate_sparse_embedding should not run for image query"),
        ):
            result = service.execute(
                QueryPlan(
                    plan_type="image_only",
                    image_query="https://example.com/query.jpg",
                    filters=QueryFilters(),
                )
            )

        self.assertEqual(len(result.image_results), 1)
        self.assertEqual(reranker_service.calls, [])
        dense_mock.assert_called_once_with(
            ["image query"],
            image_urls=["https://example.com/query.jpg"],
        )
        self.assertEqual(qdrant_service.calls[0][0], "dense")

    def test_execute_fusion_merges_text_and_image_hits(self) -> None:
        qdrant_service = FakeQdrantService(
            text_points=[
                self._make_point(
                    retrieval_unit_id="text:article-1:0",
                    article_id="article-1",
                    article_image_id=None,
                    chunk_index=0,
                    modality="text",
                    content="payload stale content",
                )
            ],
            image_points=[
                self._make_point(
                    retrieval_unit_id="image:image-1",
                    article_id="article-1",
                    article_image_id="image-1",
                    chunk_index=None,
                    modality="image",
                    content="payload stale content",
                )
            ],
        )
        reranker_service = FakeRerankerService()
        service = QueryService(
            session_factory=self.session_factory,
            markdown_service=self.markdown_service,
            qdrant_service=qdrant_service,
            reranker_service=reranker_service,
        )

        with patch(
            "backend.app.service.RAG.query_service.generate_dense_embedding",
            return_value=[[0.1, 0.2]],
        ), patch(
            "backend.app.service.RAG.query_service.generate_sparse_embedding",
            return_value=[{1: 1.0}],
        ), patch(
            "backend.app.service.RAG.query_service.ThreadPoolExecutor",
            return_value=_InlineExecutor(),
        ):
            result = service.execute(
                QueryPlan(
                    plan_type="fusion",
                    text_query="street look",
                    filters=QueryFilters(),
                )
            )

        self.assertEqual(len(result.text_results), 1)
        self.assertEqual(len(result.image_results), 1)
        self.assertEqual(len(result.packages), 1)
        self.assertEqual(result.packages[0].article_id, "article-1")

    def test_execute_image_only_fails_when_markdown_path_missing(self) -> None:
        with self.session_factory() as session:
            session.add(
                Article(
                    article_id="article-2",
                    source_name="Vogue",
                    source_type="rss",
                    source_lang="en",
                    category="高端时装",
                    canonical_url="https://example.com/2",
                    original_url="https://example.com/2",
                    title_raw="raw title",
                    summary_raw="raw summary",
                    title_zh="中文标题2",
                    summary_zh="中文摘要2",
                    tags_json=["时尚"],
                    brands_json=["Karl"],
                    markdown_rel_path=None,
                    should_publish=True,
                    enrichment_status="done",
                    parse_status="done",
                    ingested_at=datetime(2026, 3, 18, 8, 0, 0),
                )
            )
            session.add(
                ArticleImage(
                    image_id="image-2",
                    article_id="article-2",
                    source_url="https://example.com/look-2.jpg",
                    normalized_url="https://example.com/look-2.jpg",
                    caption_raw="图片说明2",
                    visual_status="done",
                )
            )
            session.commit()

        qdrant_service = FakeQdrantService(
            text_points=[],
            image_points=[
                self._make_point(
                    retrieval_unit_id="image:image-2",
                    article_id="article-2",
                    article_image_id="image-2",
                    chunk_index=None,
                    modality="image",
                    content="payload stale content",
                )
            ],
        )
        service = QueryService(
            session_factory=self.session_factory,
            markdown_service=self.markdown_service,
            qdrant_service=qdrant_service,
            reranker_service=FakeRerankerService(),
        )

        with patch(
            "backend.app.service.RAG.query_service.generate_dense_embedding",
            return_value=[[0.1, 0.2]],
        ), patch(
            "backend.app.service.RAG.query_service.generate_sparse_embedding",
            return_value=[{1: 1.0}],
        ), self.assertRaisesRegex(ValueError, "markdown_rel_path is required for image grounding"):
            service.execute(
                QueryPlan(
                    plan_type="image_only",
                    text_query="coat",
                    filters=QueryFilters(),
                )
            )

    def test_query_plan_rejects_invalid_query_shapes(self) -> None:
        with self.assertRaisesRegex(ValueError, "text_only plan requires non-empty text_query"):
            QueryPlan(plan_type="text_only", text_query="   ")
        with self.assertRaisesRegex(ValueError, "image_only plan requires exactly one"):
            QueryPlan(plan_type="image_only", text_query="coat", image_query="https://example.com/a.jpg")
        with self.assertRaisesRegex(ValueError, "fusion plan does not accept image_query"):
            QueryPlan(plan_type="fusion", text_query="coat", image_query="https://example.com/a.jpg")

    def _make_point(
        self,
        *,
        retrieval_unit_id: str,
        article_id: str,
        article_image_id: str | None,
        chunk_index: int | None,
        modality: str,
        content: str,
    ) -> models.ScoredPoint:
        return models.ScoredPoint(
            id=retrieval_unit_id,
            version=1,
            score=0.4,
            payload={
                "retrieval_unit_id": retrieval_unit_id,
                "article_id": article_id,
                "article_image_id": article_image_id,
                "chunk_index": chunk_index,
                "modality": modality,
                "content": content,
                "source_name": "Vogue",
            },
            vector=None,
            shard_key=None,
            order_value=None,
        )
