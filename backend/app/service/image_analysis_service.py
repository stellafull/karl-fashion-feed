"""Build image analysis payloads and persist analysis results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from sqlalchemy.orm import Session

from backend.app.config.llm_config import IMAGE_ANALYSIS_MODEL_CONFIG
from backend.app.models import Article, ArticleImage
from backend.app.prompts.image_analysis_prompt import IMAGE_ANALYSIS_PROMPT
from backend.app.schemas.llm.image_analysis import ImageAnalysisSchema
from backend.app.service.llm_client_service import OpenAICompatibleClient


@dataclass(frozen=True)
class ImageAnalysisInput:
    image_id: str
    image_url: str
    article_title: str
    article_summary: str
    alt_text: str
    caption_raw: str
    credit_raw: str
    context_snippet: str


class ImageAnalysisService:
    def __init__(
        self,
        *,
        llm_client: OpenAICompatibleClient | Any | None = None,
    ) -> None:
        self._llm_client = llm_client or OpenAICompatibleClient()

    def build_input(self, *, article: Article, image: ArticleImage) -> ImageAnalysisInput:
        return ImageAnalysisInput(
            image_id=image.image_id,
            image_url=image.source_url,
            article_title=(article.title_zh or article.title_raw or "").strip(),
            article_summary=(article.summary_zh or article.summary_raw or "").strip(),
            alt_text=(image.alt_text or "").strip(),
            caption_raw=(image.caption_raw or "").strip(),
            credit_raw=(image.credit_raw or "").strip(),
            context_snippet=(image.context_snippet or "").strip(),
        )

    def build_messages(self, *, payload: ImageAnalysisInput) -> list[dict[str, Any]]:
        context = json.dumps(asdict(payload), ensure_ascii=False, indent=2, sort_keys=True)
        return [
            {"role": "system", "content": IMAGE_ANALYSIS_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": context},
                    {"type": "image_url", "image_url": {"url": payload.image_url}},
                ],
            },
        ]

    def infer_payload(self, payload: ImageAnalysisInput) -> ImageAnalysisSchema:
        return self._llm_client.complete_json(
            model_config=IMAGE_ANALYSIS_MODEL_CONFIG,
            messages=self.build_messages(payload=payload),
            schema=ImageAnalysisSchema,
        )

    def analyze_image(self, session: Session, *, article: Article, image: ArticleImage) -> bool:
        if self.is_complete(image):
            return False

        payload = self.build_input(article=article, image=image)
        try:
            result = self.infer_payload(payload)
        except Exception as exc:
            self.apply_failure(image=image, error=exc)
            raise

        self.apply_result(image=image, result=result)
        session.flush()
        return True

    @staticmethod
    def apply_result(*, image: ArticleImage, result: ImageAnalysisSchema) -> None:
        image.visual_status = "done"
        image.observed_description = (result.observed_description or "").strip()
        image.ocr_text = (result.ocr_text or "").strip()
        image.visible_entities_json = list(result.visible_entities)
        image.style_signals_json = list(result.style_signals)
        image.contextual_interpretation = (result.contextual_interpretation or "").strip()
        image.analysis_metadata_json = {
            **dict(image.analysis_metadata_json or {}),
            "context_used": list(result.context_used),
            "confidence": result.confidence,
        }

    @staticmethod
    def apply_failure(*, image: ArticleImage, error: Exception) -> None:
        image.visual_status = "failed"
        image.analysis_metadata_json = {
            **dict(image.analysis_metadata_json or {}),
            "error": f"{error.__class__.__name__}: {error}",
        }

    @staticmethod
    def is_complete(image: ArticleImage) -> bool:
        return image.visual_status == "done" and bool((image.observed_description or "").strip())
