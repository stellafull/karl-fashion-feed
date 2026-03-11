from pymilvus import MilvusClient

from config import MILVUS_URI, MILVUS_USER, MILVUS_PASSWORD

## 链接Milvus客户端
client = MilvusClient(uri = MILVUS_URI,
                      user= MILVUS_USER,
                      password=MILVUS_PASSWORD)


## Milvus Schema
collection_name = "fashion_news"

