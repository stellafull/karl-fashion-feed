"""Milvus vector database service."""
import os
from typing import Any, Dict
from dotenv import find_dotenv, load_dotenv
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

class MilvusService:
    """Milvus vector database service."""

    def __init__(self):
        self.uri = os.getenv("MILVUS_uri", "localhost:19530")
        self.token = os.getenv("MILVUS_token", "")
        self.vector_dim = int(os.getenv("DENSE_EMBEDDING_DIMENSION", 2560))         # 多模态向量维度 与 config/embedding_config.py 中 DENSE_EMBEDDING_CONFIG.vector_dimension 保持一致

    def _connect(self):
        """Connect to Milvus."""
        try:
            connections.connect(alias="default", uri=self.uri, token=self.token)
            print(f"Connected to Milvus {self.uri} successfully.")
        except Exception as e:
            print(f"Failed to connect to Milvus: {e}")
            raise

    def create_collection(self, collection_name: str) -> Collection:
        """创建 Milvus collection. 如果不存在
        Args:
            collection_name (str): 集合名称
        Returns:
            Collection 对象.
        """
        # 检查集合是否存在
        if utility.has_collection(collection_name):
            print(f"Collection '{collection_name}' already exists.")
            collection = Collection(collection_name)
            collection.load()
            return collection

        """
        Field schema Args
        - id 文档id
        - article_id 来源文章id
        - vector 向量数据
        - content 文本内容/图片文字描述
        - chunk_index 文本块索引
        - type 数据类型 text/image 
        """

        # 定义字段
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="article_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=self.vector_dim),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="chunk_index", dtype=DataType.INT64),
            FieldSchema(name="type", dtype=DataType.VARCHAR, max_length=32)
        ]

        schema = CollectionSchema(fields=fields, description="Collection for storing article chunks and their embeddings.")
        collection = Collection(name=collection_name, schema=schema)
        
        # 创建索引
        index_params = {
            "metric_type": "COSINE",  # 使用余弦相似度
            "index_type": "IVF_FLAT", # 使用 IVF_FLAT 索引类型
            "params": {"nlist": 128} # IVF 索引参数，nlist 是聚类中心的数量
        }

        collection.create_index(field_name="vector", index_params=index_params)

        # load to memory
        collection.load()

        print(f"Collection '{collection_name}' created and loaded successfully.")
        return collection

    
    def insert_data(self, collection_name:str, articles: list[Dict[str, Any]]) -> int:
        """插入数据到 Milvus collection.
        Args:
            collection_name (str): 集合名称
            articles (list[dict]): 文章列表，每个 dict 包含 id, article_id, vector, content, chunk_index, type 字段
        Returns:
            插入的记录数
        """
        collection = self.create_collection(collection_name)
        

        # 准备数据
        ids = [item["id"] for item in articles]
        article_ids = [item["article_id"] for item in articles]
        vectors = [item["vector"] for item in articles]
        contents = [item["content"] for item in articles]
        chunk_indices = [item["chunk_index"] for item in articles]
        types = [item["type"] for item in articles]

        # 插入数据
        try:
            collection.insert([ids, article_ids, vectors, contents, chunk_indices, types])
            collection.flush()  # 确保数据被写入磁盘
            print(f"Inserted {len(articles)} records into collection '{collection_name}' successfully.")
            return len(articles)
        except Exception as e:
            print(f"Failed to insert data into Milvus: {e}")
            raise

    # query检索
    