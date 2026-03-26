"""Prompt builder for durable article normalization."""

from __future__ import annotations

from datetime import date


def build_article_normalization_prompt(
    *,
    source_name: str,
    source_lang: str,
    canonical_url: str,
    business_day: date,
    markdown: str,
) -> str:
    """Build the normalization prompt from truth-source article materials."""
    return f"""
你是时尚资讯中文编辑助手。

你会收到单篇 article 的来源信息和原始 canonical markdown。
你的任务只有一件事：生成可持久化的中文材料。

输出要求：
- 生成准确、克制的 `title_zh`
- 生成简洁、可读的 `summary_zh`
- 生成完整的 `body_zh`
- 严禁编造正文不存在的事实
- 不要做发布判断，不要输出 `should_publish`、`reject_reason` 或任何筛选结论
- 保留事实信息、品牌名、时间、地点、人物、数字与引述含义
- 中文表达以中国区同事阅读为目标，避免营销腔
- 只输出符合 schema 的 JSON

article metadata:
- source_name: {source_name}
- source_lang: {source_lang}
- canonical_url: {canonical_url}
- business_day: {business_day.isoformat()}

canonical markdown:
```markdown
{markdown}
```
""".strip()
