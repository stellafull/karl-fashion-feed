# KARL FASHION FEED
全球时尚资讯平台 + Agent

每天北京时间8点进行article抓取 新增article聚合story 
目标中国区的同事 -> 多语言来源 -> 中文翻译



## 项目概览
backend/app/​
├── config/              # 配置文件模块​
├── core/                # 核心工具模块​
├── models/              # 数据库模型（ORM）​
├── router/              # API路由层（Controller）​
├── schemas/             # 请求/响应模式（DTO）​
├── scripts/             # 工具脚本​
├── service/             # 业务逻辑层（Service）​
│  └── agents/           # 各个Agent实现​
│  └── RAG/              # Milvus RAG 实现
├── sources.yaml         # 信息采集源头
└── app_main.py          # FastAPI应用入口


### /config/ 配置文件模块
集中管理项目所有配置项 避免硬编码
- llm_config.py         # llm模型配置
- embedding_config.py   # dense/sparse embedding模型配置
.....                   # 其他config 暂未决定

### /core/ 核心工具模块
底层基础设施的初始化和管理
- database.py           # postgresSQL 连接
- redis_client.py       # RedisClient
- security.py           # jwt token
- feishu_auth.py        # 上线后接入 预留入口
......                  # 其他未定


### /models/ 数据库模型(ORM)
定义所有数据库表的结构 SQLAIchemy ORM
- user.py
- chat.py               # sessions/messages/memory
- knowledge.py          # RAG
- article.py            # 去重后article持久化
- story.py              # 每日新增article聚合story
......                  # 其他未定


### /router/ API 路由层
定义所有http接口 处理请求/响应 调用service业务逻辑 前端行为对应的后端操作
- auth_router.py        # /auth 仅登录 不提供用户注册权限
- chat_router.py        # /chat
- knowledge_router.py   # /knowledge
- database_router.py    # /database
- memory_router.py      # /memory
- article_router.py     # /article
- story_router.py       # /story
.....                   # 其他待定

### /schemas/ 请求响应模式
使用pydantic 定义数据传出对象 DTO 实现自动校验和文档生成
- user.py               # user schema
- chat.py               # chat schema message/history/...
- knowledge.py          # chunk/query/upload ....
- search.py             # request/result
.....                   # 其他待定


### /scripts/ 工具脚本
数据初始化
- init_article_data.py  # 一次性抓取过去30天的article 按天进行article聚合 构建story
......                  # 其他待定

### /service/ 业务逻辑
核心业务逻辑, 调用外部API 数据库 LLM
- chat_service.py       # 聊天消息处理 上下文管理
- memory_service.py     # 长期记忆压缩 向量检索
- text2sql_service.py   # text2sql, schema感知
- checkpoint_service.py # checkpoint保存恢复
- schedular_service.py  # APScheduler定时任务
- article_collection_service.py     # 资讯采集去重
- database_explorer.py  # 数据库Schema探索
- article_summarization_service.py  # 资讯总结
- article_cluster_service.py        # article聚合为story
- embedding_service.py  # dense/sparse embedding支持
.....                   # 其他待定


### /service/agents/
agents核心逻辑 各agent实现 当前目标仅实现知识库问答与
- rag_agent.py          # 知识库与websearch问答agent
...                     # 其他待定

### app_main.py FastAPI应用入口
初始化FastAPI应用
注册所有router
配置CORS 中间件
启动定时任务



