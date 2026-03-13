from __future__ import annotations

import unittest

from backend.app.config.embedding_config import DENSE_EMBEDDING_CONFIG
from backend.app.config.llm_config import ARTICLE_PARSE_MODEL_CONFIG, IMAGE_ANALYSIS_MODEL_CONFIG
from backend.app.prompts.article_parse_prompt import ARTICLE_PARSE_PROMPT
from backend.app.prompts.image_analysis_prompt import IMAGE_ANALYSIS_PROMPT
from backend.app.schemas.llm.article_parse import ArticleParseSchema
from backend.app.schemas.llm.image_analysis import ImageAnalysisSchema


class PromptSchemaContractsTest(unittest.TestCase):
    def test_prompts_are_non_empty(self) -> None:
        self.assertIn("页面解析", ARTICLE_PARSE_PROMPT)
        self.assertIn("视觉分析", IMAGE_ANALYSIS_PROMPT)

    def test_llm_schemas_expose_json_schema(self) -> None:
        article_schema = ArticleParseSchema.model_json_schema()
        image_schema = ImageAnalysisSchema.model_json_schema()

        self.assertIn("markdown_blocks", article_schema["properties"])
        self.assertIn("observed_description", image_schema["properties"])

    def test_model_configs_exist(self) -> None:
        self.assertTrue(ARTICLE_PARSE_MODEL_CONFIG.model_name)
        self.assertTrue(IMAGE_ANALYSIS_MODEL_CONFIG.model_name)
        self.assertTrue(DENSE_EMBEDDING_CONFIG.model_name)
        self.assertIsInstance(ARTICLE_PARSE_MODEL_CONFIG.temperature, float)
        self.assertIsInstance(IMAGE_ANALYSIS_MODEL_CONFIG.temperature, float)
        self.assertFalse(hasattr(ARTICLE_PARSE_MODEL_CONFIG, "provider"))
        self.assertFalse(hasattr(DENSE_EMBEDDING_CONFIG, "provider"))


if __name__ == "__main__":
    unittest.main()
