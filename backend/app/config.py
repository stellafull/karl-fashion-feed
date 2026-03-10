from dotenv import load_dotenv, find_dotenv
import os


# load environment variables from .env file
_ = load_dotenv(find_dotenv())

# Milvus 配置
milvus_uri = os.getenv("MILVUS_URI")
milvus_token = os.getenv("MILVUS_TOKEN")

# 阿里百炼配置
dashscope_api_key = os.getenv("DASHSCOPE_API_KEY")

# 多模态embedding模型
modality_embedding_model = "qwen3-vl-embedding"

# 向量维度
embedding_dimension = 2560

