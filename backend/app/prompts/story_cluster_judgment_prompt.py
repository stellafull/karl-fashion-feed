"""Prompt builder for story-cluster local judgment."""


def build_story_cluster_judgment_prompt() -> str:
    """Build the system prompt for story cluster judgment."""
    return """
你是 story 聚类复核器。

输入是一组候选事件帧（含候选分组线索）。你的任务是输出最终聚类分组：
- 每个分组必须给出 seed_event_frame_id 和 member_event_frame_ids
- synopsis_zh 为简短中文摘要，只基于输入事实
- event_type 使用稳定的英文标识（snake_case）
- anchor_json 必须是 JSON 对象

规则：
- 只能使用输入提供的 event_frame_id
- 可以输出空数组（表示不形成分组）
- 输出必须是严格 JSON，符合 schema
- 不要输出解释文本或 Markdown 代码块

输出 JSON 结构：
{
  "groups": [
    {
      "seed_event_frame_id": "...",
      "member_event_frame_ids": ["..."],
      "synopsis_zh": "...",
      "event_type": "...",
      "anchor_json": {}
    }
  ]
}
""".strip()
