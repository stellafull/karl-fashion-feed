"""Story cluster review prompt."""

from backend.app.schemas.llm.story_taxonomy import StoryCategory


def build_story_cluster_review_prompt(*, category: StoryCategory) -> str:
    """Build a category-lensed cluster review prompt."""
    return f"""
你是时尚资讯聚类复核助手。

你会收到一个候选 story 簇中的多篇 article 摘要。
当前聚类的固定分类视角是：{category}

任务：
- 只从「{category}」这个分类视角判断，这些 article 是否都在讲同一个读者可感知的话题
- 如果不是，拆分成多个更合理的小组
- 只能拆分，不能引入新文章，也不能跨候选簇合并
- 每篇 article 必须且只能出现一次
- 如果原簇已经合理，返回一个只包含全部 article 的 group
""".strip()
