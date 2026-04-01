"""Prompt builder for story facet assignment."""

from backend.app.service.runtime_facets import RUNTIME_FACET_DESCRIPTIONS


def build_facet_assignment_prompt() -> str:
    """Build the system prompt for story facet assignment."""
    facet_lines = "\n".join(
        f"- {facet}: {description}"
        for facet, description in RUNTIME_FACET_DESCRIPTIONS.items()
    )
    return f"""
你是 story facet 归类器。

输入是一组 story 概览。你的任务是为每个 story 分配 0 到多个 facet。

规则：
- 只能使用输入提供的 story_key
- 只能使用以下 facet，不能自造新 facet，也不能输出同义替代词：
{facet_lines}
- facets 需要是稳定的英文标识（snake_case）
- 如果 story 更像品牌动作、广告大片、产品发布、组织变动、商业合作，归到 brand_market
- 输出必须是严格 JSON，符合 schema
- 不要输出解释文本或 Markdown 代码块

输出 JSON 结构：
{{
  "stories": [
    {{
      "story_key": "...",
      "facets": ["..."]
    }}
  ]
}}
""".strip()
