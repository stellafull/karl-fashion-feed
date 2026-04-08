"""Configuration management for the Open Deep Research system."""

import os
from enum import Enum
from typing import Any, List, Optional
from urllib.parse import urlparse

from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, field_validator

class SearchAPI(Enum):
    """Enumeration of available search API providers."""
    
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    TAVILY = "tavily"
    NONE = "none"


class MCPConfig(BaseModel):
    """Configuration for Model Context Protocol (MCP) servers."""
    
    url: Optional[str] = Field(
        default=None,
        optional=True,
    )
    """The URL of the MCP server"""
    tools: Optional[List[str]] = Field(
        default=None,
        optional=True,
    )
    """The tools to make available to the LLM"""
    auth_required: Optional[bool] = Field(
        default=False,
        optional=True,
    )
    """Whether the MCP server requires authentication"""


FINAL_REPORT_RATE_LIMITER = InMemoryRateLimiter(
    requests_per_second=0.5,
    check_every_n_seconds=0.1,
    max_bucket_size=1,
)

class Configuration(BaseModel):
    """Main configuration class for the Deep Research agent."""
    
    # General Configuration
    max_structured_output_retries: int = Field(
        default=3,
    )
    provider_max_retries: int = Field(
        default=6,
    )
    allow_clarification: bool = Field(
        default=True,
    )
    max_concurrent_research_units: int = Field(
        default=5,
    )
    # Research Configuration
    search_api: SearchAPI = Field(
        default=SearchAPI.TAVILY,
    )
    max_deep_scout_iterations: int = Field(
        default=2,
    )
    tavily_timeout: int = Field(
        default=120,
    )
    # Model Configuration
    summarization_model: str = Field(
        default="openai:qwen3.6-plus",
    )
    summarization_model_max_tokens: int = Field(
        default=8192,
    )
    max_content_length: int = Field(
        default=50000,
    )
    research_model: str = Field(
        default="openai:kimi-k2.5",
    )
    research_model_max_tokens: int = Field(
        default=10000,
    )
    compression_model: str = Field(
        default="openai:qwen3.6-plus",
    )
    compression_model_max_tokens: int = Field(
        default=8192,
    )
    final_report_model: str = Field(
        default="openai:kimi/kimi-k2.5",
    )
    final_report_model_max_tokens: int = Field(
        default=10000,
    )
    # MCP server configuration
    mcp_config: Optional[MCPConfig] = Field(
        default=None,
        optional=True,
    )
    mcp_prompt: Optional[str] = Field(
        default=None,
        optional=True,
    )
    # openai compatible endpoint base url
    openai_compatible_base_url: Optional[str] = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    @field_validator("openai_compatible_base_url")
    @classmethod
    def validate_openai_compatible_base_url(
        cls, value: Optional[str]
    ) -> Optional[str]:
        """Require a provider base URL, not a request endpoint URL."""
        if value is None:
            return value

        parsed = urlparse(value)
        path = parsed.path.rstrip("/")
        request_endpoints = (
            "/chat/completions",
            "/completions",
            "/responses",
        )
        if any(path.endswith(endpoint) for endpoint in request_endpoints):
            raise ValueError(
                "openai_compatible_base_url must be a provider base URL, "
                "not a request endpoint URL."
            )
        return value


    @classmethod
    def from_runnable_config(
        cls, config: Optional[RunnableConfig] = None
    ) -> "Configuration":
        """Create a Configuration instance from a RunnableConfig."""
        configurable = config.get("configurable", {}) if config else {}
        field_names = list(cls.model_fields.keys())
        values: dict[str, Any] = {
            field_name: os.environ.get(field_name.upper(), configurable.get(field_name))
            for field_name in field_names
        }
        return cls(**{k: v for k, v in values.items() if v is not None})

    class Config:
        """Pydantic configuration."""
        
        arbitrary_types_allowed = True
