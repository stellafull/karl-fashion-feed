# Quick Query Retrieval Core V1

## Context

RAG 入库主链已打通：publishable `article` 和其关联 `article_image` 已进入 shared collection `kff_retrieval`。下一步先完成 query 侧 retrieval core，建立可验证的文字/图片检索链路和 tool dispatch。

本阶段只做 retrieval core，不做 chat loop，不做 FastAPI router，不做 web search，不做 freshness scoring。

---

## Scope

### In

- shared collection query 能力
- text / image / fusion retrieval
- rerank
- PostgreSQL grounding
- tool arguments → `QueryPlan` → `QueryResult`

### Out

- `RagChatAgent`
- FastAPI `/chat`
- `web_search`
- 默认 freshness decay
- deep research 模式

---

## Core Decisions

| 决策 | 选择 | 理由 |
|------|------|------|
| 本阶段边界 | 只做 retrieval core | 先把检索真相链路闭环，减少调试变量 |
| Query planner | 保留 `QueryPlan` 作为 retrieval 契约；不新增独立 LLM planner 请求 | 符合 RAG 规范，同时保持最短路径 |
| 时间过滤 | 只支持显式 `time_range(start_at, end_at)` | 可以表达固定区间和最近 N 天，不引入额外 freshness 逻辑 |
| 时间字段 | 统一使用 `article.ingested_at` | 与 shared collection 规范一致 |
| Freshness | 本阶段完全不做 | 当前先不用 freshness scoring |
| Hybrid search | Qdrant 原生 RRF prefetch | 不写自定义融合 |
| Citation grounding | 必须回源 PostgreSQL / Markdown / `article_image` | Qdrant 不是业务真相源 |

---

## Step 1: Fix Shared Collection Image Content

**修改文件**: `backend/app/service/RAG/article_rag_service.py`

当前 image lane 的 `content` 只有 `observed_description`，这不符合 shared collection 规范。先修这个，再做 query。

### image `content` 生成规则

固定拼接下列字段，按顺序过滤空值后合并：

- `caption_raw`
- `alt_text`
- `credit_raw`
- `context_snippet`
- `ocr_text`
- `observed_description`
- `contextual_interpretation`
- 父 article 的：
  - `title_zh`
  - `summary_zh`
  - `tags_json`
  - `brands_json`

### 入库约束

- 父 `article.should_publish=true`
- `article_image.visual_status="done"`
- 至少存在一类文本投影信号
- 若拼接后 `content` 为空，直接跳过，不入 image lane

---

## Step 2: Extend QdrantService Query APIs

**修改文件**: `backend/app/service/RAG/qdrant_service.py`

新增方法：

- `search_dense(collection_name, query_vector, *, limit, filters)`
- `search_sparse(collection_name, query_sparse_vector, *, limit, filters)`
- `search_hybrid(collection_name, dense_vector, sparse_vector, *, limit, filters)`
- `build_metadata_filter(*, modality, source_names, categories, tags, brands, start_at, end_at)`

### 规则

- `modality` filter 在 recall 阶段生效
- metadata filter 在 recall 阶段生效
- `start_at` / `end_at` 统一作用于 `ingested_at`
- `search_hybrid` 使用 Qdrant 原生 `query_points + prefetch + FusionQuery(RRF)`
- 本阶段不引入 freshness 相关分数

---

## Step 3: Define Query Schemas

**新建文件**: `backend/app/schemas/rag_query.py`

定义最小数据契约：

### `QueryPlan`

- `plan_type`: `text_only | image_only | fusion`
- `text_query`
- `image_query`
- `filters`
- `output_goal`

### `QueryFilters`

- `source_names`
- `categories`
- `tags`
- `brands`
- `time_range`

### `TimeRange`

- `start_at`
- `end_at`

### `RetrievalHit`

- `retrieval_unit_id`
- `modality`
- `article_id`
- `article_image_id`
- `content`
- `score`
- `citation_locator`

### `QueryResult`

- `query_plan`
- `text_results`
- `image_results`
- `packages`
- `citation_locators`

### `ArticlePackage`

- `article_id`
- `title_zh`
- `summary_zh`
- `text_hits`
- `image_hits`
- `combined_score`

### `CitationLocator`

- text hit: `article_id`, `chunk_index`
- image hit: `article_id`, `article_image_id`
- 公共字段: `source_name`, `canonical_url`

---

## Step 4: Implement QueryService

**新建文件**: `backend/app/service/RAG/query_service.py`

`QueryService.execute(query_plan) -> QueryResult`

### `text_only`

1. `generate_dense_embedding([text_query])`
2. `generate_sparse_embedding([text_query])`
3. `build_metadata_filter(modality="text", ...)`
4. `search_hybrid(limit=40)`
5. `rerank(query=text_query, documents=text contents, top_n=10)`
6. 输出 text hits，并为每条 hit 生成 text citation locator

### `image_only` 文搜图

1. `generate_dense_embedding([text_query])`
2. `generate_sparse_embedding([text_query])`
3. `build_metadata_filter(modality="image", ...)`
4. `search_hybrid(limit=30)`
5. `rerank(query=text_query, documents=image contents, top_n=10)`
6. 回源 `article_image` + 父 `article`
7. 补同 article 的 grounding text references

### `image_only` 图搜图

1. `generate_dense_embedding([""], image_urls=[image_url])`
2. `build_metadata_filter(modality="image", ...)`
3. `search_dense(limit=30)`
4. 不做 rerank
5. 回源 `article_image` + 父 `article`
6. 补同 article 的 grounding text references

### `fusion`

1. 并行执行 text lane 与 image lane
2. 各 lane 各自排序
3. 按 `article_id` merge + dedupe
4. 组装 `ArticlePackage`

### grounding 规则

- text hit:
  - 回源 `Article`
  - locator 必须能定位 `article_id + chunk_index`
- image hit:
  - 回源 `ArticleImage` + `Article`
  - 返回 `source_url`、视觉字段、父文章标题摘要
- 不使用 Qdrant payload 作为最终引用真相

---

## Step 5: RerankerService

**新建文件**: `backend/app/service/RAG/reranker_service.py`
**修改文件**: `backend/app/config/embedding_config.py`

### 目标

- 封装 cross-encoder rerank
- 只负责 relevance，不承担 freshness

### 接口

- `RerankerService.rerank(query, documents, top_n) -> list[RerankResult]`

### 规则

- text lane: query = `text_query`
- image 文搜图 lane: query = `text_query`
- 图搜图 lane: 不调用 reranker

---

## Step 6: Tool Dispatch Only

**新建文件**: `backend/app/service/RAG/rag_tools.py`

本阶段只做工具定义和分发，不做 chat loop。

### Tool 1: `search_fashion_articles`

参数：

- `query`
- `brands`
- `categories`
- `start_at`
- `end_at`
- `include_images`
- `limit`

路由规则：

- `include_images=false` → `text_only`
- `include_images=true` → `fusion`

### Tool 2: `search_fashion_images`

参数：

- `text_query`
- `image_url`
- `brands`
- `categories`
- `start_at`
- `end_at`
- `limit`

路由规则：

- 有 `image_url` → 图搜图 `image_only`
- 仅 `text_query` → 文搜图 `image_only`

### `execute_tool(tool_name, arguments)`

- 校验参数
- 构建确定性的 `QueryPlan`
- 调用 `QueryService.execute()`
- 返回结构化 retrieval result

---

## Implementation Order

| 顺序 | 内容 |
|------|------|
| 1 | 修正 image lane shared collection content |
| 2 | QdrantService query APIs |
| 3 | `schemas/rag_query.py` |
| 4 | `RerankerService` |
| 5 | `QueryService` |
| 6 | `rag_tools.py` |
| 7 | 端到端测试与脚本 |

---

## Validation

1. 验证 image `content` 已包含 caption/ocr/context/article tags/brands
2. 验证无文本投影信号的图片不会入库
3. 验证 Qdrant `modality` / brands / time_range filter 生效
4. 验证 `text_only` / 文搜图 `image_only` / 图搜图 `image_only` / `fusion` 四条链路
5. 验证图搜图路径不会调用 reranker
6. 验证 image grounding 回源 `article_image`
7. 验证 tool arguments → `QueryPlan` 转换正确

---

## Assumptions

- 时间过滤只看 `ingested_at`
- `time_range` 语义固定为 `start_at <= ingested_at < end_at`
- 本阶段 relevance 是唯一排序分数
- freshness scoring 完全延后
