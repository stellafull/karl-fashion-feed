"""Prompt builder for story facet assignment."""


def build_facet_assignment_prompt() -> str:
    """Build the system prompt for story facet assignment."""
    return """
你是 story facet 归类器。

输入是一组 story 概览。你的任务是为每个 story 分配 0 到多个 facet。

规则：
- 只能使用输入提供的 story_key
- facets 需要是稳定的英文标识（snake_case）
- 输出必须是严格 JSON，符合 schema
- 不要输出解释文本或 Markdown 代码块

输出 JSON 结构：
{
  "stories": [
    {
      "story_key": "...",
      "facets": ["..."]
    }
  ]
}
""".strip()
