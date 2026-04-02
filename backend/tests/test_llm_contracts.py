from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from backend.app.config.llm_config import Configuration
from backend.app.service.langchain_model_factory import build_rag_model, build_story_model
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

    def test_configuration_from_runnable_config_uses_global_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            configuration = Configuration.from_runnable_config()

        self.assertEqual(configuration.story_summarization_model, "kimi-k2.5")
        self.assertEqual(configuration.story_summarization_temperature, 0.3)
        self.assertEqual(configuration.story_summarization_timeout_seconds, 600)
        self.assertEqual(configuration.story_summarization_model_max_tokens, 4000)
        self.assertEqual(configuration.rag_model, "kimi-k2.5")
        self.assertEqual(configuration.rag_temperature, 0.2)
        self.assertEqual(configuration.rag_timeout_seconds, 120)
        self.assertEqual(configuration.rag_model_max_tokens, 2000)
        self.assertEqual(configuration.max_structured_output_retries, 3)
        self.assertEqual(configuration.base_url, "https://dashscope.aliyuncs.com/compatible-mode/v1")

    def test_configuration_from_runnable_config_prefers_env_over_runnable(self) -> None:
        runnable_config = {
            "configurable": {
                "api_key": "configurable-key",
                "base_url": "https://configurable.example/v1",
                "story_summarization_model": "configurable-story-model",
                "story_summarization_temperature": 0.9,
                "story_summarization_timeout_seconds": 55,
                "story_summarization_model_max_tokens": 777,
                "rag_model": "configurable-rag-model",
                "rag_temperature": 0.8,
                "rag_timeout_seconds": 44,
                "rag_model_max_tokens": 666,
                "max_structured_output_retries": 4,
            }
        }
        env = {
            "OPENAI_API_KEY": "env-key",
            "OPENAI_BASE_URL": "https://env.example/v1",
            "STORY_SUMMARIZATION_MODEL": "env-story-model",
            "STORY_SUMMARIZATION_TEMPERATURE": "0.4",
            "STORY_SUMMARIZATION_TIMEOUT_SECONDS": "666",
            "STORY_SUMMARIZATION_MODEL_MAX_TOKENS": "1234",
            "RAG_CHAT_MODEL": "env-rag-chat-model",
            "RAG_TEMPERATURE": "0.25",
            "RAG_TIMEOUT_SECONDS": "321",
            "RAG_MODEL_MAX_TOKENS": "987",
            "STORY_SUMMARIZATION_MAX_STRUCTURED_OUTPUT_RETRIES": "5",
        }
        with patch.dict(os.environ, env, clear=True):
            configuration = Configuration.from_runnable_config(runnable_config=runnable_config)

        self.assertEqual(configuration.api_key, "env-key")
        self.assertEqual(configuration.base_url, "https://env.example/v1")
        self.assertEqual(configuration.story_summarization_model, "env-story-model")
        self.assertEqual(configuration.story_summarization_temperature, 0.4)
        self.assertEqual(configuration.story_summarization_timeout_seconds, 666)
        self.assertEqual(configuration.story_summarization_model_max_tokens, 1234)
        self.assertEqual(configuration.rag_model, "env-rag-chat-model")
        self.assertEqual(configuration.rag_temperature, 0.25)
        self.assertEqual(configuration.rag_timeout_seconds, 321)
        self.assertEqual(configuration.rag_model_max_tokens, 987)
        self.assertEqual(configuration.max_structured_output_retries, 5)

    def test_configuration_from_runnable_config_supports_rag_model_env(self) -> None:
        with patch.dict(os.environ, {"RAG_MODEL": "env-rag-model"}, clear=True):
            configuration = Configuration.from_runnable_config()

        self.assertEqual(configuration.rag_model, "env-rag-model")

    @patch("backend.app.service.langchain_model_factory.ChatOpenAI")
    def test_build_story_model_wires_chat_openai_and_retry(self, chat_openai_mock: MagicMock) -> None:
        runnable = MagicMock(name="story-runnable")
        model_instance = MagicMock(name="chat-openai-instance")
        model_instance.with_retry.return_value = runnable
        chat_openai_mock.return_value = model_instance
        configuration = Configuration(
            api_key="test-key",
            base_url="https://openai.example/v1",
            story_summarization_model="kimi-k2.5",
            story_summarization_model_max_tokens=999,
            story_summarization_temperature=0.15,
            story_summarization_timeout_seconds=88,
            rag_model="unused-rag-model",
            rag_model_max_tokens=444,
            rag_temperature=0.2,
            rag_timeout_seconds=33,
            max_structured_output_retries=6,
            max_react_tool_calls=8,
        )

        built_model = build_story_model(configuration)

        self.assertIs(built_model, runnable)
        chat_openai_mock.assert_called_once_with(
            model="kimi-k2.5",
            api_key="test-key",
            base_url="https://openai.example/v1",
            temperature=0.15,
            max_completion_tokens=999,
            timeout=88,
            max_retries=0,
            use_responses_api=True,
        )
        model_instance.with_retry.assert_called_once_with(stop_after_attempt=6)

    @patch("backend.app.service.langchain_model_factory.ChatOpenAI")
    def test_build_rag_model_wires_chat_openai_and_retry(self, chat_openai_mock: MagicMock) -> None:
        runnable = MagicMock(name="rag-runnable")
        model_instance = MagicMock(name="chat-openai-instance")
        model_instance.with_retry.return_value = runnable
        chat_openai_mock.return_value = model_instance
        configuration = Configuration(
            api_key="rag-key",
            base_url="https://rag-openai.example/v1",
            story_summarization_model="unused-story-model",
            story_summarization_model_max_tokens=999,
            story_summarization_temperature=0.15,
            story_summarization_timeout_seconds=88,
            rag_model="rag-model",
            rag_model_max_tokens=444,
            rag_temperature=0.2,
            rag_timeout_seconds=33,
            max_structured_output_retries=3,
            max_react_tool_calls=8,
        )

        built_model = build_rag_model(configuration)

        self.assertIs(built_model, runnable)
        chat_openai_mock.assert_called_once_with(
            model="rag-model",
            api_key="rag-key",
            base_url="https://rag-openai.example/v1",
            temperature=0.2,
            max_completion_tokens=444,
            timeout=33,
            max_retries=0,
            use_responses_api=True,
        )
        model_instance.with_retry.assert_called_once_with(stop_after_attempt=3)
