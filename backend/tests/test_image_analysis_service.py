from __future__ import annotations

import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base
from backend.app.models.article import Article, ArticleImage
from backend.app.schemas.llm.image_analysis import ImageAnalysisSchema
from backend.app.service.image_analysis_service import ImageAnalysisService


class StubLLMClient:
    def __init__(self, result: ImageAnalysisSchema | Exception) -> None:
        self._result = result

    def complete_json(self, **_: object) -> ImageAnalysisSchema:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class ImageAnalysisServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def test_build_messages_include_image_url_content(self) -> None:
        with self.session_factory() as session:
            article = Article(
                article_id="article-1",
                source_name="Vogue",
                source_type="rss",
                source_lang="en",
                category="高端时装",
                canonical_url="https://example.com/story",
                original_url="https://example.com/story",
                title_raw="Runway Story",
                summary_raw="Front row summary",
            )
            image = ArticleImage(
                image_id="img-1",
                article_id="article-1",
                source_url="https://example.com/image.jpg",
                normalized_url="https://example.com/image.jpg",
                caption_raw="A caption",
                context_snippet="Nearby runway text",
            )
            session.add(article)
            session.add(image)
            session.commit()

            service = ImageAnalysisService()
            payload = service.build_input(article=article, image=image)
            messages = service.build_messages(payload=payload)

            self.assertEqual(payload.image_id, "img-1")
            self.assertEqual(payload.context_snippet, "Nearby runway text")
            self.assertEqual(messages[1]["content"][1]["image_url"]["url"], "https://example.com/image.jpg")

    def test_analyze_image_runs_vlm_and_persists_result(self) -> None:
        with self.session_factory() as session:
            article = Article(
                article_id="article-1",
                source_name="Vogue",
                source_type="rss",
                source_lang="en",
                category="高端时装",
                canonical_url="https://example.com/story",
                original_url="https://example.com/story",
                title_raw="Runway Story",
                summary_raw="Front row summary",
            )
            image = ArticleImage(
                image_id="img-1",
                article_id="article-1",
                source_url="https://example.com/image.jpg",
                normalized_url="https://example.com/image.jpg",
                caption_raw="A caption",
                context_snippet="Nearby runway text",
            )
            session.add(article)
            session.add(image)
            session.commit()

            service = ImageAnalysisService(
                llm_client=StubLLMClient(
                    ImageAnalysisSchema(
                        image_id="img-1",
                        observed_description="A model in a structured coat.",
                        ocr_text="PARIS",
                        visible_entities=["model", "coat"],
                        style_signals=["structured tailoring"],
                        contextual_interpretation="Likely from a runway look.",
                        context_used=["article_summary", "caption_raw"],
                        confidence=0.93,
                    )
                )
            )
            changed = service.analyze_image(session, article=article, image=image)
            session.commit()

            self.assertTrue(changed)

        with self.session_factory() as session:
            stored = session.scalar(select(ArticleImage).where(ArticleImage.image_id == "img-1"))
            assert stored is not None
            self.assertEqual(stored.visual_status, "done")
            self.assertEqual(stored.observed_description, "A model in a structured coat.")
            self.assertEqual(stored.visible_entities_json, ["model", "coat"])


if __name__ == "__main__":
    unittest.main()
