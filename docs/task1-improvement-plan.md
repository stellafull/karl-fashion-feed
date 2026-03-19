# KARL Fashion Feed — Task 1 Review & Improvement Plan

## Context

Task 1 目标：**采集 article 每日新增去重入库，对网页做解析 text/image，然后构建 RAG**。

项目已有较完整的采集→解析→富化→聚类→Story 管线，RAG 设计文档 (`service/RAG/AGENTS.md`) 规范详尽，但代码实现与设计文档存在显著差距。以下按 **已有能力 → 问题 → 改进方案** 逐层梳理。

---

## 一、采集 & 去重：问题与改进

### 已有能力
- RSS + Web 异步采集 (`news_collection_service.py`)
- URL 追踪参数清洗 (`QUERY_PARAM_BLOCKLIST`)
- `canonical_url` UNIQUE 约束去重
- 批内 `seen_urls` 去重

### 问题

| # | 问题 | 严重度 |
|---|------|--------|
| 1 | **仅 URL 去重，无内容级去重** — 同一篇新闻被 Vogue/WWD/BoF 等转载时 URL 不同但内容几乎相同 | P0 |
| 2 | **无 per-domain 限速** — `global_http_concurrency=16` 可能对同一站点并发过高，易被封 IP | P0 |
| 3 | **无失败重试策略** — `parse_attempts` 字段存在但未见指数退避 + dead-letter 逻辑 | P1 |
| 4 | **未利用 HTTP 缓存头** — 无 `ETag` / `If-Modified-Since` 条件 GET | P2 |
| 5 | **无 `robots.txt` 遵从** | P2 |

### 改进方案

1. **内容级 SimHash 去重** — 在 parse 完成后对 Markdown 正文计算 SimHash，相似度 > 0.85 标记为 near-duplicate，链接到最早/最权威源
   - 新增 `content_hash` 字段到 Article 模型
   - 新建 `article_dedup_service.py`
2. **per-domain 限速** — 引入 `aiolimiter.AsyncLimiter`，在 `sources.yaml` 中配置每源 rate
3. **指数退避重试** — 封装统一 retry 装饰器，3 次失败后标记 `parse_status='failed_permanent'`
4. **条件 GET** — 在 Redis 中缓存 `ETag` / `Last-Modified`，下次请求附带

---

## 二、网页解析（Text / Image）：问题与改进

### 已有能力
- BeautifulSoup HTML 解析 + CSS selector 文本提取
- Image 提取 + `ArticleImage` 模型（含 role, position, alt_text, caption）
- Image 视觉分析 (`image_analysis_service.py`) — OCR, description, style signals
- Markdown 落地 (`article_markdown_service.py`)

### 问题

| # | 问题 | 严重度 |
|---|------|--------|
| 1 | **BS4 无正文提取能力** — 不区分正文/导航/广告/评论，产出 Markdown 噪声大 | P0 |
| 2 | **不处理 JS 渲染页面** — Hypebeast/Highsnobiety 等时尚站大量 JS 渲染内容 | P1 |
| 3 | **Image 提取不完整** — 仅 `<img src>`, 缺少 srcset/picture/data-src/bg-image/og:image | P1 |
| 4 | **无图片感知哈希去重** — 同一张秀场图片出现在多篇文章，浪费存储和向量空间 | P1 |
| 5 | **图片仅存 URL** — 外站 CDN 链接易失效（link rot） | P2 |
| 6 | **`article_chunk_servie.py` 文件名拼写错误** | P3 |

### 改进方案

1. **引入 `trafilatura`** 作为主正文提取器，`readability-lxml` 作为 fallback，BS4 仅用于 per-site 自定义 selector
   - 修改 `news_collection_service.py` 的 `parse_article_html()` 调用链
2. **Playwright 渲染** — 在 `sources.yaml` 中对需要 JS 的源标记 `requires_js: true`，按需启用
3. **扩展 image 提取** — 处理 `srcset`/`<picture>`/`data-src`/`data-lazy`/`og:image`/`<figcaption>`
4. **pHash 图片去重** — 新增 `image_hash` 字段到 `ArticleImage`，下载图片计算 pHash，相同 hash 复用
5. **MinIO 本地图片缓存** — 对 hero image 做本地持久化（hash-based naming）

---

## 三、RAG 管线：问题与改进

### 已有能力
- 设计文档完备 (`service/RAG/AGENTS.md`)
- Dense embedding: `qwen3-vl-embedding` 2560d multimodal
- Sparse embedding: `text-embedding-v4` (已实现函数，未接入管线)
- Qdrant schema 定义 (`qdrant_service.py`)
- Text chunking: `RecursiveCharacterTextSplitter` with tiktoken

### 问题

| # | 问题 | 严重度 |
|---|------|--------|
| 1 | **`qdrant_service.py` schema 与 AGENTS.md 规范不一致** — 代码缺少 `role`, `heading_path`, `source_lang`, `source_type` 等字段；需要继续把 shared collection payload 与检索约束完整对齐 | P0 |
| 2 | **Sparse embedding 未接入管线** — `generate_sparse_embedding()` 已实现但 daily pipeline 未调用 | P0 |
| 3 | **Chunking 策略不符合规范** — 规范要求"标题层级感知 + recursive chunker"，当前只有固定大小切分，无 heading 感知 | P0 |
| 4 | **无检索服务** — query planner / recall / rerank / merge 全部未实现 | P0 |
| 5 | **无 reranker** — 仅靠向量相似度，对时尚领域专有名词查询效果差 | P0 |
| 6 | **IVF_FLAT 索引不适合** — 数据量 < 100万时 HNSW 在 recall/latency 上远优于 IVF_FLAT | P1 |
| 7 | **无 RAG 评估** — 无 golden test set、无 Recall@K/NDCG/faithfulness 指标 | P1 |
| 8 | **`content` 拼接逻辑未实现** — 规范要求 `title + heading_path + chunk正文 + summary + tags + brands`，当前仅存 chunk 原文 | P1 |

### 改进方案

1. **重写 `qdrant_service.py`** — 对齐 AGENTS.md 的 shared collection schema，包含所有最小字段
   - 修复索引：dense 用 HNSW(COSINE)，sparse 用 Qdrant sparse vector index
   - 实现 upsert 逻辑（delete + insert by retrieval_unit_id）
2. **接入 sparse embedding** — 在 story_workflow_service 的 embedding 阶段同时生成 dense + sparse
3. **实现标题感知 chunking** — 解析 Markdown heading 层级，按语义段切分，超长段再用 recursive splitter
   - 重写 `article_chunk_service.py`（同时修正文件名拼写）
4. **实现 `content` 拼接** — 按规范拼接检索文本：`title_zh + heading_path + chunk + summary_zh + tags + brands + source_name`
5. **实现检索服务** — 按 AGENTS.md 5.1-5.6 节逐步实现：
   - `query_planner_service.py` — intent detection + filter extraction
   - `retrieval_service.py` — dual-lane recall + rerank + final scoring + merge
   - 集成 cross-encoder reranker（推荐 `bge-reranker-v2-m3`）
6. **实现 RAG answer agent** — 接收 retrieval evidence，回源读取原文，生成带引用的回答
7. **RAG 评估** — 构建 50-100 条时尚领域 golden queries，自动化跑 Recall@K + LLM-as-judge

---

## 四、管线健壮性：问题与改进

### 问题

| # | 问题 | 严重度 |
|---|------|--------|
| 1 | **无内置调度器** — 依赖外部 cron，丢失上下文 | P1 |
| 2 | **管线不可部分恢复** — 某阶段失败后必须从头重跑 | P1 |
| 3 | **sync/async 混用** — `daily_pipeline_service.py` 中 `asyncio.run()` 包裹 async service，阻塞事件循环 | P1 |
| 4 | **无阶段间数据校验** — 不验证 Markdown 非空、embedding 维度正确等 | P2 |
| 5 | **无监控/告警** — 管线静默失败无人知 | P2 |
| 6 | **`print()` 替代结构化日志** — `qdrant_service.py` 全用 print | P2 |

### 改进方案

1. **APScheduler** — 集成 `AsyncIOScheduler`，`Asia/Shanghai` 时区每日 8:00 触发
2. **阶段可恢复** — 基于已有的 `parse_status` / `enrichment_status` 字段，pipeline re-run 自动跳过已完成 article
3. **统一 async** — `DailyPipelineService` 改为全 async，移除 `asyncio.run()` 包裹
4. **校验 gate** — 每阶段后验证输出（Markdown 长度、embedding 维度、Qdrant 写入行数）
5. **结构化日志** — 替换 print 为 `structlog`，Pipeline run 级 correlation ID

---

## 五、缺失的 FastAPI 接口

当前无任何 HTTP 路由，需要新建：

| 路由 | 用途 |
|------|------|
| `POST /api/v1/search` | RAG 检索 + 生成回答 |
| `GET /api/v1/articles/{id}` | 文章详情 |
| `GET /api/v1/stories` | Story 列表 |
| `GET /api/v1/pipeline/status` | 管线运行状态 |
| `POST /api/v1/pipeline/trigger` | 手动触发管线 |

---

## 六、推荐执行顺序

### Phase 1：核心管线补全（1-2 周）
1. 重写 `qdrant_service.py` 对齐 AGENTS.md schema
2. 实现标题感知 chunking + content 拼接
3. 接入 sparse embedding 到管线
4. 引入 `trafilatura` 替换 BS4 正文提取
5. 添加内容级 SimHash 去重
6. 添加 per-domain 限速

### Phase 2：检索管线（2-3 周）
7. 实现 query planner
8. 实现 retrieval service（dense + sparse hybrid recall）
9. 集成 cross-encoder reranker
10. 实现 freshness decay scoring
11. 实现 RAG answer agent
12. 新建 FastAPI search 路由

### Phase 3：健壮性 & 评估（1-2 周）
13. 统一 async，移除 asyncio.run()
14. APScheduler 集成
15. 结构化日志 + 监控
16. RAG 评估 pipeline + golden test set
17. 扩展 image 提取 + pHash 去重

---

## 关键文件清单

| 文件 | 操作 |
|------|------|
| `backend/app/service/RAG/qdrant_service.py` | **重写** — 对齐规范 schema |
| `backend/app/service/article_chunk_servie.py` | **重写 + 重命名** — 标题感知 chunking |
| `backend/app/service/RAG/embedding_service.py` | 修改 — 接入 sparse 管线 |
| `backend/app/service/news_collection_service.py` | 修改 — trafilatura + image 扩展 |
| `backend/app/service/article_collection_service.py` | 修改 — SimHash 去重 |
| `backend/app/service/daily_pipeline_service.py` | 修改 — 全 async + embedding 阶段 |
| `backend/app/service/RAG/query_planner_service.py` | **新建** |
| `backend/app/service/RAG/retrieval_service.py` | **新建** |
| `backend/app/service/RAG/rag_agent_service.py` | **新建** |
| `backend/app/service/RAG/reranker_service.py` | **新建** |
| `backend/app/router/` | **新建** — FastAPI 路由 |

## 验证方式

1. **采集去重** — 运行 `collect-articles`，确认 SimHash 去重能过滤跨站重复
2. **解析质量** — 对比 BS4 vs trafilatura 输出，抽检 10 篇文章 Markdown 质量
3. **RAG 端到端** — 构建测试 query → 检索 → 生成回答，验证引用正确性
4. **Pipeline 恢复** — 在 enrichment 阶段人为中断，re-run 验证跳过已完成
5. **单元测试** — 所有新 service 配套 test 文件，`pytest` 全部通过
