"""config.py
- 配置文件，包含数据库连接信息、Milvus连接信息、Dashscope API Key
- Embedding模型配置
- LLM模型配置
- 其他全局配置
"""

import os
from dataclasses import dataclass, field

from dotenv import find_dotenv, load_dotenv

# 加载环境变量
_ = load_dotenv(find_dotenv())


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    milvus_uri: str | None
    milvus_token: str | None
    dashscope_api_key: str | None
    modality_embedding_model: str
    embedding_dimension: int


@dataclass
class MilvusSettings:
    milvus_uri: str
    milvus_token: str

@dataclass
class DatabaseSettings:
    database_host: str
    database_port: int
    database_user: str
    database_password: str
    database_name: str

@dataclass
class EmbeddingConfig:
    embedding_model: str
    embedding_dimension: int

@dataclass
class EmbeddingModelsConfig:
    # sparse embedding
    sparse_embedding: EmbeddingConfig = field(default_factory=lambda: EmbeddingConfig(
        embedding_model="text-embedding-v4",
        embedding_dimension=1024
    ))

    # dense embedding modality 多模态召回
    dense_embedding: EmbeddingConfig = field(default_factory=lambda: EmbeddingConfig(
        embedding_model="qwen3-vl-embedding",
        embedding_dimension=2560
    ))


@dataclass
class DashscopeSettings:
    dashscope_api_key: str






def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL"),
        milvus_uri=os.getenv("MILVUS_URI"),
        milvus_token=os.getenv("MILVUS_TOKEN"),
        dashscope_api_key=os.getenv("DASHSCOPE_API_KEY"),
        modality_embedding_model=os.getenv("MODALITY_EMBEDDING_MODEL", "qwen3-vl-embedding"),
        embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "2560")),
    )


def require_database_url() -> str:
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set. Configure PostgreSQL before ingesting documents.")
    return database_url


settings = get_settings()

database_url = settings.database_url
milvus_uri = settings.milvus_uri
milvus_token = settings.milvus_token
dashscope_api_key = settings.dashscope_api_key
modality_embedding_model = settings.modality_embedding_model
embedding_dimension = settings.embedding_dimension
