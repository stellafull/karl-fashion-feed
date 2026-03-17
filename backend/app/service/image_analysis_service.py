"""Build image analysis payloads and persist analysis results."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config.llm_config import IMAGE_ANALYSIS_MODEL_CONFIG
from backend.app.models.article import Article, ArticleImage
from backend.app.prompts.image_analysis_prompt import IMAGE_ANALYSIS_PROMPT
from backend.app.schemas.llm.image_analysis import ImageAnalysisSchema
from backend.app.service.llm_client_service import (
    BatchChatRequest,
    BatchChatResult,
    OpenAICompatibleClient,
)


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
    def __init__(
        self,
        *,
        llm_client: OpenAICompatibleClient | Any | None = None,
    ) -> None:
        self._llm_client = llm_client or OpenAICompatibleClient()

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
        metadata = asdict(payload)
        source_url = metadata.pop("source_url")
        return [
            {"role": "system", "content": IMAGE_ANALYSIS_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _render_json_payload(metadata)},
                    {"type": "image_url", "image_url": {"url": source_url}},
                ],
            },
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

    def analyze_image(self, session: Session, *, article: Article, image: ArticleImage) -> bool:
        if self.is_complete(image):
            return False

        payload = self.build_input(article=article, image=image)
        try:
            analysis = self.infer_payload(payload)
        except Exception as exc:
            self.mark_failed(session, image_id=image.image_id, error_message=f"{exc.__class__.__name__}: {exc}")
            raise

        self.apply_analysis(session, image_id=image.image_id, analysis=analysis)
        session.flush()
        return True

    def infer_payload(self, payload: ImageAnalysisInput) -> ImageAnalysisSchema:
        return self._llm_client.complete_json(
            model_config=IMAGE_ANALYSIS_MODEL_CONFIG,
            messages=self.build_messages(payload=payload),
            schema=ImageAnalysisSchema,
        )

    def infer_batch(
        self,
        payloads: list[ImageAnalysisInput],
    ) -> dict[str, BatchChatResult]:
        if not payloads:
            return {}

        if not hasattr(self._llm_client, "complete_json_batch"):
            return self._fallback_infer_batch(payloads)

        requests = [
            BatchChatRequest(
                custom_id=f"image:{payload.image_id}",
                messages=self.build_messages(payload=payload),
            )
            for payload in payloads
        ]
        try:
            raw_results = self._llm_client.complete_json_batch(
                model_config=IMAGE_ANALYSIS_MODEL_CONFIG,
                requests=requests,
                schema=ImageAnalysisSchema,
                metadata={"stage": "image_analysis"},
            )
        except Exception:
            return self._fallback_infer_batch(payloads)

        return {
            custom_id.split(":", 1)[1]: BatchChatResult(
                custom_id=custom_id.split(":", 1)[1],
                value=result.value,
                error=result.error,
            )
            for custom_id, result in raw_results.items()
        }

    def _fallback_infer_batch(
        self,
        payloads: list[ImageAnalysisInput],
    ) -> dict[str, BatchChatResult]:
        results: dict[str, BatchChatResult] = {}
        for payload in payloads:
            try:
                results[payload.image_id] = BatchChatResult(
                    custom_id=payload.image_id,
                    value=self.infer_payload(payload),
                )
            except Exception as exc:
                results[payload.image_id] = BatchChatResult(
                    custom_id=payload.image_id,
                    error=f"{exc.__class__.__name__}: {exc}",
                )
        return results

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

    @staticmethod
    def is_complete(image: ArticleImage) -> bool:
        return image.visual_status == "done" and bool((image.observed_description or "").strip())


def _render_json_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
