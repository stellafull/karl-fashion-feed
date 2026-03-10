input_text = "Chloé 2026秋冬系列：献给母亲的颂歌，田园诗意与手工温度的交织"
input_image = "https://media.wwdjapan.com/wp-content/uploads/2026/03/09101339/OG-56.jpg"
input_vedio = ""

import dashscope
import json
import os
from http import HTTPStatus
from dotenv import load_dotenv, find_dotenv

_ = load_dotenv(find_dotenv())

input_data = [
    {
        "text": input_text,
    }
]

# 使用 qwen3-vl-embedding 生成融合向量
resp = dashscope.MultiModalEmbedding.call(
    # 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx",
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    model="qwen3-vl-embedding",
    input=input_data,
    # 可选参数：指定向量维度（支持 2560, 2048, 1536, 1024, 768, 512, 256，默认 2560）
    # dimension = 1024
)

print(json.dumps(resp.output, indent=4))