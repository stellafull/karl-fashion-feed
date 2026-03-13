"""Centralized LLM settings for backend news collection."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

_ = load_dotenv(find_dotenv())

DEFAULT_OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_HTTP_REFERER = "https://fashion-feed.manus.space"
DEFAULT_ARTICLE_ANALYSIS_MODEL = "google/gemini-2.0-flash-001"

ARTICLE_ANALYSIS_SYSTEM_PROMPT = (
    "你是轻奢品牌内部情报平台的资深编辑。你负责做文章摘要、分类和相关性判断。只输出 JSON。"
)

ARTICLE_ANALYSIS_USER_PROMPT_TEMPLATE = """请判断下面这篇报道是否应保留在时尚情报平台中。

保留标准：
1. 与时尚、奢侈品、美妆、生活方式、名人风格、品牌营销、零售、秀场、趋势、文化议题相关。
2. 品牌联名、campaign、代言、跨界合作、时尚科技、Apple 等科技品牌与时尚行业的交叉内容，应视为相关。
3. 纯泛科技、纯汽车、纯财经、纯社会新闻，且与时尚产业/审美/品牌动作无明显关系，才判定为不保留。

请严格输出 JSON：
{{
  "keep": true,
  "relevance_score": 0,
  "reason": "一句中文原因",
  "summary_zh": "100字以内中文摘要",
  "category": "从以下选一个: 秀场/系列, 街拍/造型, 趋势总结, 品牌/市场",
  "tags": ["标签1", "标签2", "标签3"],
  "content_type": "如 brand-collab / fashion-tech / runway / market / celebrity-style / beauty / lifestyle / culture",
  "is_sensitive": false
}}

原始内容：
{article_text}"""


@dataclass(frozen=True)
class ProviderConfig:
    api_key: str
    chat_url: str
    http_referer: str


@dataclass(frozen=True)
class ModelConfig:
    model: str
    temperature: float
    max_tokens: int
    input_chars: int


@dataclass(frozen=True)
class PromptConfig:
    system_prompt: str
    user_prompt_template: str

    def build_messages(self, *, article_text: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": self.user_prompt_template.format(article_text=article_text),
            },
        ]


@dataclass(frozen=True)
class ArticleAnalysisConfig:
    model: ModelConfig
    prompt: PromptConfig

    def build_messages(self, *, article_text: str) -> list[dict[str, str]]:
        return self.prompt.build_messages(article_text=article_text)


@dataclass(frozen=True)
class NewsCollectionLLMConfig:
    provider: ProviderConfig
    article_analysis: ArticleAnalysisConfig

    @property
    def is_configured(self) -> bool:
        return bool(self.provider.api_key)


def get_news_collection_llm_config() -> NewsCollectionLLMConfig:
    return NewsCollectionLLMConfig(
        provider=ProviderConfig(
            api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
            chat_url=os.getenv("OPENROUTER_CHAT_URL", DEFAULT_OPENROUTER_CHAT_URL).strip()
            or DEFAULT_OPENROUTER_CHAT_URL,
            http_referer=os.getenv("OPENROUTER_HTTP_REFERER", DEFAULT_HTTP_REFERER).strip()
            or DEFAULT_HTTP_REFERER,
        ),
        article_analysis=ArticleAnalysisConfig(
            model=ModelConfig(
                model=os.getenv("LLM_MODEL", DEFAULT_ARTICLE_ANALYSIS_MODEL).strip()
                or DEFAULT_ARTICLE_ANALYSIS_MODEL,
                temperature=0.1,
                max_tokens=1200,
                input_chars=2200,
            ),
            prompt=PromptConfig(
                system_prompt=ARTICLE_ANALYSIS_SYSTEM_PROMPT,
                user_prompt_template=ARTICLE_ANALYSIS_USER_PROMPT_TEMPLATE,
            ),
        ),
    )
