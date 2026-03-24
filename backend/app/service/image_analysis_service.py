"""Build image analysis payloads and persist analysis results."""

from __future__ import annotations

import json

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from backend.app.config.llm_config import IMAGE_ANALYSIS_MODEL_CONFIG
from backend.app.models import Article, ArticleImage
from backend.app.prompts.image_analysis_prompt import IMAGE_ANALYSIS_PROMPT
from backend.app.schemas.llm.image_analysis import ImageAnalysisSchema

MAX_IMAGE_ANALYSIS_ATTEMPTS = 3


class ImageAnalysisService:
    def __init__(self) -> None:
        api_key = IMAGE_ANALYSIS_MODEL_CONFIG.api_key
        if not api_key:
            raise ValueError(f"missing API key for {IMAGE_ANALYSIS_MODEL_CONFIG.model_name}")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=IMAGE_ANALYSIS_MODEL_CONFIG.base_url,
            timeout=IMAGE_ANALYSIS_MODEL_CONFIG.timeout_seconds,
        )

    async def analyze_image(self, session: Session, *, article: Article, image: ArticleImage) -> bool:
        if self.is_complete(image):
            return False
        if image.visual_status == "abandoned":
            return False
        if image.visual_attempts >= MAX_IMAGE_ANALYSIS_ATTEMPTS:
            image.visual_status = "abandoned"
            session.flush()
            return False

        payload = self.build_input(article=article, image=image)
        try:
            result = await self.infer_payload(payload)
        except Exception as exc:
            self.apply_failure(image=image, error=exc)
            session.flush()
            return False

        self.apply_result(image=image, result=result)
        session.flush()
        return True

    @staticmethod
    def build_input(*, article: Article, image: ArticleImage) -> dict[str, str]:
        return {
            "image_id": image.image_id,
            "image_url": image.source_url,
            "article_title": (article.title_zh or article.title_raw or "").strip(),
            "article_summary": (article.summary_zh or article.summary_raw or "").strip(),
            "alt_text": (image.alt_text or "").strip(),
            "caption_raw": (image.caption_raw or "").strip(),
            "credit_raw": (image.credit_raw or "").strip(),
            "context_snippet": (image.context_snippet or "").strip(),
        }

    def build_messages(self, payload: dict[str, str]) -> list[dict[str, object]]:
        context = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        return [
            {"role": "system", "content": IMAGE_ANALYSIS_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": context},
                    {"type": "image_url", "image_url": {"url": payload["image_url"]}},
                ],
            },
        ]

    async def infer_payload(self, payload: dict[str, str]) -> ImageAnalysisSchema:
        response = await self._client.beta.chat.completions.parse(
            model=IMAGE_ANALYSIS_MODEL_CONFIG.model_name,
            temperature=IMAGE_ANALYSIS_MODEL_CONFIG.temperature,
            response_format=ImageAnalysisSchema,
            messages=self.build_messages(payload),
        )
        result = response.choices[0].message.parsed
        if result is None:
            raise ValueError("image analysis response missing parsed payload")
        return result

    @staticmethod
    def apply_result(*, image: ArticleImage, result: ImageAnalysisSchema) -> None:
        image.visual_status = "done"
        image.visual_attempts = 0
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
        image.visual_attempts += 1
        if image.visual_attempts >= MAX_IMAGE_ANALYSIS_ATTEMPTS:
            image.visual_status = "abandoned"
        else:
            image.visual_status = "failed"
        image.analysis_metadata_json = {
            **dict(image.analysis_metadata_json or {}),
            "error": f"{error.__class__.__name__}: {error}",
        }

    @staticmethod
    def is_complete(image: ArticleImage) -> bool:
        return image.visual_status == "done" and bool((image.observed_description or "").strip())
