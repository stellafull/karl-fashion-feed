from __future__ import annotations

import unittest

from backend.app.config.embedding_config import (
    DENSE_EMBEDDING_CONFIG,
    DENSE_SUMMARIZATION_EMBEDDING_CONFIG,
)
from backend.app.config.llm_config import (
    IMAGE_ANALYSIS_MODEL_CONFIG,
    STORY_SUMMARIZATION_MODEL_CONFIG,
)
from backend.app.prompts.article_enrichment_prompt import ARTICLE_ENRICHMENT_PROMPT
from backend.app.prompts.image_analysis_prompt import IMAGE_ANALYSIS_PROMPT
from backend.app.prompts.story_cluster_review_prompt import STORY_CLUSTER_REVIEW_PROMPT
from backend.app.prompts.story_generation_prompt import STORY_GENERATION_PROMPT
from backend.app.schemas.llm.article_enrichment import ArticleEnrichmentSchema
from backend.app.schemas.llm.image_analysis import ImageAnalysisSchema
from backend.app.schemas.llm.story_cluster_review import StoryClusterReviewSchema
from backend.app.schemas.llm.story_generation import StoryGenerationSchema


class PromptSchemaContractsTest(unittest.TestCase):
    def test_prompts_are_non_empty(self) -> None:
        self.assertIn("视觉分析", IMAGE_ANALYSIS_PROMPT)
        self.assertIn("中文编辑", ARTICLE_ENRICHMENT_PROMPT)
        self.assertIn("聚类复核", STORY_CLUSTER_REVIEW_PROMPT)
        self.assertIn("聚合编辑", STORY_GENERATION_PROMPT)

    def test_llm_schemas_expose_json_schema(self) -> None:
        image_schema = ImageAnalysisSchema.model_json_schema()
        enrichment_schema = ArticleEnrichmentSchema.model_json_schema()
        cluster_review_schema = StoryClusterReviewSchema.model_json_schema()
        story_generation_schema = StoryGenerationSchema.model_json_schema()

        self.assertIn("observed_description", image_schema["properties"])
        self.assertIn("should_publish", enrichment_schema["properties"])
        self.assertIn("groups", cluster_review_schema["properties"])
        self.assertIn("key_points", story_generation_schema["properties"])

    def test_model_configs_exist(self) -> None:
        self.assertTrue(IMAGE_ANALYSIS_MODEL_CONFIG.model_name)
        self.assertTrue(STORY_SUMMARIZATION_MODEL_CONFIG.model_name)
        self.assertTrue(DENSE_EMBEDDING_CONFIG.model_name)
        self.assertTrue(DENSE_SUMMARIZATION_EMBEDDING_CONFIG.model_name)
        self.assertIsInstance(IMAGE_ANALYSIS_MODEL_CONFIG.temperature, float)
        self.assertIsInstance(STORY_SUMMARIZATION_MODEL_CONFIG.temperature, float)
        self.assertFalse(hasattr(DENSE_EMBEDDING_CONFIG, "provider"))
        self.assertFalse(hasattr(DENSE_SUMMARIZATION_EMBEDDING_CONFIG, "provider"))
        self.assertEqual(STORY_SUMMARIZATION_MODEL_CONFIG.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(STORY_SUMMARIZATION_MODEL_CONFIG.model_name, "google/gemini-2.5-flash")
        self.assertEqual(DENSE_EMBEDDING_CONFIG.api_key_env, "DASHSCOPE_API_KEY")
        self.assertEqual(DENSE_EMBEDDING_CONFIG.batch_size, 10)
        self.assertEqual(DENSE_SUMMARIZATION_EMBEDDING_CONFIG.api_key_env, "DASHSCOPE_API_KEY")
        self.assertEqual(DENSE_SUMMARIZATION_EMBEDDING_CONFIG.batch_size, 10)


if __name__ == "__main__":
    unittest.main()
