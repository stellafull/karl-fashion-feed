from __future__ import annotations

import unittest

from backend.app.prompts.digest_report_writing_prompt import build_digest_report_writing_prompt


class DigestReportWritingPromptContractTest(unittest.TestCase):
    def test_prompt_targets_internal_readers(self) -> None:
        prompt = build_digest_report_writing_prompt()

        self.assertIn("inside digest", prompt)
        self.assertIn("面向公司内部读者", prompt)
        self.assertIn("不是面向大众消费者", prompt)

    def test_prompt_keeps_title_and_one_line_dek_requirements(self) -> None:
        prompt = build_digest_report_writing_prompt()

        self.assertIn("title_zh", prompt)
        self.assertIn("dek_zh", prompt)
        self.assertIn("一行导语", prompt)
        self.assertIn("保持一句话且简洁", prompt)
        self.assertIn("避免并列分句过多导致句子过长", prompt)

    def test_prompt_requires_flexible_body_organization(self) -> None:
        prompt = build_digest_report_writing_prompt()

        self.assertIn("正文组织方式可灵活调整", prompt)
        self.assertIn("不强制固定模板", prompt)
        self.assertIn("正文默认使用连续 digest 段落推进", prompt)
        self.assertIn("不要搭建杂志式分节脚手架", prompt)
        self.assertIn("避免多级标题", prompt)
        self.assertIn("不要写固定收尾段", prompt)
        self.assertIn("不要机械地按品牌逐一写一个小节", prompt)

    def test_prompt_emphasizes_inside_digest_signals(self) -> None:
        prompt = build_digest_report_writing_prompt()

        self.assertIn("品牌动作", prompt)
        self.assertIn("产品与品类信号", prompt)
        self.assertIn("趋势变化", prompt)
        self.assertIn("可被设计师直接使用的信号", prompt)

    def test_prompt_bans_magazine_feature_tone(self) -> None:
        prompt = build_digest_report_writing_prompt()

        self.assertIn("不是时尚杂志稿", prompt)
        self.assertIn("避免夸张修辞", prompt)
        self.assertIn("避免情绪化开场", prompt)
        self.assertIn("避免空泛审美形容", prompt)
        self.assertIn("避免杂志特稿腔调", prompt)

    def test_prompt_sets_short_default_with_multi_story_expansion(self) -> None:
        prompt = build_digest_report_writing_prompt()

        self.assertIn("默认写成短篇内部 digest", prompt)
        self.assertIn("仅在单条 digest 覆盖多个 story 时可自然变长", prompt)

    def test_prompt_requires_titles_to_anchor_to_concrete_signals(self) -> None:
        prompt = build_digest_report_writing_prompt()

        self.assertIn("标题必须锚定具体品牌、品类、事件或主题信号", prompt)
        self.assertIn("避免抽象编辑标签", prompt)


if __name__ == "__main__":
    unittest.main()
