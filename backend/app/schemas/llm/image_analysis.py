"""Structured output schema for image analysis."""

from __future__ import annotations

from pydantic import BaseModel, Field

# 视觉分析结构化输出，包含对图像的描述、OCR文本、可见实体、风格信号、上下文解释等信息，供后续处理使用
# 后续或可接入集团自研视觉分析模型 更精准适配时尚领域图像分析需求



class ImageAnalysisSchema(BaseModel):
    image_id: str
    observed_description: str = ""
    ocr_text: str = ""
    visible_entities: list[str] = Field(default_factory=list, description="List of visible entities detected in the image")
    style_signals: list[str] = Field(default_factory=list, description="List of style signals inferred from the image, such as 'casual', 'formal', 'vintage', etc.")
    contextual_interpretation: str = Field(default="", description="Interpretation of the image based on contextual clues, which may include background setting, detected objects, color scheme, and other visual elements that contribute to understanding the scene or fashion style depicted in the image.")
    context_used: list[str] = Field(default_factory=list, description="List of contextual clues used for interpretation, such as 'background setting', 'detected objects', 'color scheme', etc.")
    confidence: float | None = None
