"""Story generation prompt."""

from backend.app.schemas.llm.story_taxonomy import StoryCategory


def build_story_generation_prompt(*, category: StoryCategory) -> str:
    """Build a category-lensed story generation prompt."""
    return f"""
你是时尚资讯聚合编辑。

你会收到一个 story 簇内的多篇 article 中文摘要。
当前 story 的固定分类视角是：{category}

输出要求：
- 生成面向中国区同事的中文 `title_zh`
- 生成简洁、可读的 `summary_zh`
- 提炼 `key_points`
- 提取 story 级 `tags`
- 整体叙述必须围绕「{category}」这个阅读视角展开
- 如果这些 article 同时也可以被其他分类理解，也不要转移重心
- 不要输出 `category` 字段，分类由系统固定注入
- 不要回写历史，不要输出任何数据库字段以外的说明
""".strip()
