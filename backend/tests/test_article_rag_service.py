from __future__ import annotations

import unittest

from backend.app.models import Article, ArticleImage
from backend.app.service.RAG.article_rag_service import (
    build_image_retrieval_content,
    has_image_text_projection,
)


class ArticleRagServiceHelpersTest(unittest.TestCase):
    def test_build_image_retrieval_content_uses_only_source_provided_text(self) -> None:
        article = Article(
            article_id="article-1",
            source_name="Vogue",
            category="trend_summary",
            title_raw="Runway Story",
            summary_raw="Summary",
            canonical_url="https://example.com/story",
        )
        image = ArticleImage(
            image_id="image-1",
            article_id="article-1",
            source_url="https://example.com/image.jpg",
            normalized_url="https://example.com/image.jpg",
            caption_raw="模特穿浅金色缎面长裙",
            alt_text="runway look",
            credit_raw="Launchmetrics",
            context_snippet="这一造型强调垂坠感和金属光泽",
            observed_description="可见细肩带、收腰线条与高光泽面料",
            ocr_text="LOOK 12",
            visible_entities_json=["缎面长裙", {"name": "细肩带"}],
            style_signals_json=["液态金属感", {"label": "极简晚装"}],
            contextual_interpretation="适合回答颜色、材质、廓形相关问题",
        )

        content = build_image_retrieval_content(article, image)

        self.assertIn("模特穿浅金色缎面长裙", content)
        self.assertIn("runway look", content)
        self.assertIn("Launchmetrics", content)
        self.assertIn("这一造型强调垂坠感和金属光泽", content)
        self.assertNotIn("LOOK 12", content)
        self.assertNotIn("液态金属感", content)
        self.assertNotIn("适合回答颜色、材质、廓形相关问题", content)

    def test_has_image_text_projection_rejects_visual_only_images(self) -> None:
        image = ArticleImage(
            image_id="image-2",
            article_id="article-1",
            source_url="https://example.com/image-2.jpg",
            normalized_url="https://example.com/image-2.jpg",
            observed_description="近景珠饰细节",
            style_signals_json=["工艺细节"],
        )

        self.assertFalse(has_image_text_projection(image))


if __name__ == "__main__":
    unittest.main()
