from __future__ import annotations

import unittest

from backend.app.prompts.rag_answer_synthesis_prompt import RAG_ANSWER_SYNTHESIS_PROMPT
from backend.app.prompts.rag_tool_loop_prompt import RAG_TOOL_LOOP_PROMPT


class RagPromptContractsTest(unittest.TestCase):
    def test_tool_loop_prompt_requires_visual_queries_to_use_image_or_fusion(self) -> None:
        self.assertIn("眼镜、包、鞋", RAG_TOOL_LOOP_PROMPT)
        self.assertIn("必须至少调用一次 `search_fashion_images` 或 `search_fashion_fusion`", RAG_TOOL_LOOP_PROMPT)
        self.assertIn("优先用 `search_fashion_images(text_query=...)`", RAG_TOOL_LOOP_PROMPT)

    def test_synthesis_prompt_requires_visual_answers_to_prefer_image_hits(self) -> None:
        self.assertIn("类似风格的眼镜、包、鞋", RAG_ANSWER_SYNTHESIS_PROMPT)
        self.assertIn("不要只复述文章摘要", RAG_ANSWER_SYNTHESIS_PROMPT)
        self.assertIn("单品类别、形状/廓形、颜色、材质、装饰细节", RAG_ANSWER_SYNTHESIS_PROMPT)
        self.assertIn("visual_result_count", RAG_ANSWER_SYNTHESIS_PROMPT)
        self.assertIn("不要武断下结论", RAG_ANSWER_SYNTHESIS_PROMPT)
        self.assertIn("answer-visible internal evidence", RAG_ANSWER_SYNTHESIS_PROMPT)
        self.assertIn("external visual evidence", RAG_ANSWER_SYNTHESIS_PROMPT)
        self.assertIn("不要先写“根据检索结果”", RAG_ANSWER_SYNTHESIS_PROMPT)
        self.assertIn("不要写“我需要调用 web_search”", RAG_ANSWER_SYNTHESIS_PROMPT)
        self.assertIn("回答应该像面向同事的成稿", RAG_ANSWER_SYNTHESIS_PROMPT)
        self.assertIn("不要输出“检索结果总结”", RAG_ANSWER_SYNTHESIS_PROMPT)


if __name__ == "__main__":
    unittest.main()
