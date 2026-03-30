from __future__ import annotations

import unittest

from backend.app.schemas.llm.digest_packaging import DigestPackagingSchema
from backend.app.schemas.llm.digest_report_writing import DigestReportWritingSchema
from backend.app.schemas.llm.facet_assignment import FacetAssignmentSchema
from backend.app.schemas.llm.story_cluster_judgment import StoryClusterJudgmentSchema


class LlmContractsTest(unittest.TestCase):
    def test_story_cluster_judgment_schema_parses_group_members(self) -> None:
        payload = (
            '{"groups":[{"seed_event_frame_id":"f1","member_event_frame_ids":["f1","f2"],'
            '"synopsis_zh":"巴黎秀场同一事件","event_type":"runway_show","anchor_json":'
            '{"brand":"A"}}]}'
        )
        parsed = StoryClusterJudgmentSchema.model_validate_json(payload)
        self.assertEqual(parsed.groups[0].member_event_frame_ids, ["f1", "f2"])

    def test_facet_assignment_schema_parses_multi_facet_membership(self) -> None:
        payload = '{"stories":[{"story_key":"s1","facets":["runway_series","trend_summary"]}]}'
        parsed = FacetAssignmentSchema.model_validate_json(payload)
        self.assertEqual(parsed.stories[0].facets, ["runway_series", "trend_summary"])

    def test_facet_assignment_schema_allows_zero_facets(self) -> None:
        payload = '{"stories":[{"story_key":"s1","facets":[]}]}'
        parsed = FacetAssignmentSchema.model_validate_json(payload)
        self.assertEqual(parsed.stories[0].facets, [])

    def test_facet_assignment_schema_rejects_missing_facets_key(self) -> None:
        payload = '{"stories":[{"story_key":"s1"}]}'
        with self.assertRaises(Exception):
            FacetAssignmentSchema.model_validate_json(payload)

    def test_digest_packaging_schema_parses_overlapping_story_plans(self) -> None:
        payload = (
            '{"digests":[{"facet":"trend_summary","story_keys":["s1","s2"],"article_ids":'
            '["a1","a2"],"editorial_angle":"秀场肩部轮廓趋势","title_zh":"肩部轮廓成为本季主线",'
            '"dek_zh":"多场发布共同推高这一轮趋势"}]}'
        )
        parsed = DigestPackagingSchema.model_validate_json(payload)
        self.assertEqual(parsed.digests[0].story_keys, ["s1", "s2"])

    def test_digest_report_writing_schema_parses_report_payload(self) -> None:
        payload = (
            '{"title_zh":"本日秀场速写","dek_zh":"导语","body_markdown":"# 正文",'
            '"source_article_ids":["a1","a2"]}'
        )
        parsed = DigestReportWritingSchema.model_validate_json(payload)
        self.assertEqual(parsed.source_article_ids, ["a1", "a2"])
