"""Build image analysis payloads and persist analysis results."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.article import Article, ArticleImage
from backend.app.prompts.image_analysis_prompt import IMAGE_ANALYSIS_PROMPT
from backend.app.schemas.llm.image_analysis import ImageAnalysisSchema


@dataclass(frozen=True)
class ImageAnalysisInput:
    image_id: str
    source_url: str
    article_title: str
    article_summary: str
    source_name: str
    source_lang: str
    category: str
    alt_text: str
    caption_raw: str
    credit_raw: str
    context_snippet: str


class ImageAnalysisService:
    def build_input(self, *, article: Article, image: ArticleImage) -> ImageAnalysisInput:
        return ImageAnalysisInput(
            image_id=image.image_id,
            source_url=image.source_url,
            article_title=article.title_raw,
            article_summary=article.summary_raw,
            source_name=article.source_name,
            source_lang=article.source_lang,
            category=article.category,
            alt_text=image.alt_text,
            caption_raw=image.caption_raw,
            credit_raw=image.credit_raw,
            context_snippet=image.context_snippet,
        )

    def build_messages(self, *, payload: ImageAnalysisInput) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": IMAGE_ANALYSIS_PROMPT},
            {"role": "user", "content": str(asdict(payload))},
        ]

    def list_pending_image_ids(self, session: Session, *, limit: int = 100) -> list[str]:
        return list(
            session.scalars(
                select(ArticleImage.image_id)
                .where(ArticleImage.visual_status == "pending")
                .order_by(ArticleImage.position.asc())
                .limit(limit)
            )
        )

    def apply_analysis(
        self,
        session: Session,
        *,
        image_id: str,
        analysis: ImageAnalysisSchema,
    ) -> None:
        image = session.get(ArticleImage, image_id)
        if image is None:
            raise ValueError(f"image not found: {image_id}")

        image.visual_status = "done"
        image.observed_description = analysis.observed_description
        image.ocr_text = analysis.ocr_text
        image.visible_entities_json = analysis.visible_entities
        image.style_signals_json = analysis.style_signals
        image.contextual_interpretation = analysis.contextual_interpretation
        image.analysis_metadata_json = {
            "context_used": analysis.context_used,
            "confidence": analysis.confidence,
        }

    def mark_failed(self, session: Session, *, image_id: str, error_message: str) -> None:
        image = session.get(ArticleImage, image_id)
        if image is None:
            raise ValueError(f"image not found: {image_id}")

        image.visual_status = "failed"
        image.analysis_metadata_json = {"error_message": error_message}
