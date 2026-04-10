# Backend

## 目标

当前后端只维护一条 digest runtime 主链路：

- 采集多语言时尚来源
- 落地 `article` / `article_image` 真相源
- 从已解析文章抽取 `article_event_frame`
- 以 business day 为单位打包 `strict_story`
- 生成唯一 public read model `digest`
- 基于 `article` / `article_image` 构建检索副本

旧 `story` 阅读模型已经退出当前运行时，不再属于任何生产链路。

## 核心不变量

- `article` 是事实真相源。
- `canonical_url` 归一化后是文章唯一去重键。
- 同一 `canonical_url` 二次抓到时直接视为重复，不做补写。
- `article` 主表只保存 metadata、摘要预览、Markdown 相对路径和主图引用。
- 正文解析后写入本地 Markdown，数据库只存相对路径。
- `article_image` 保存图片 URL、位置和来源文本真相，不保存二进制。
- `article_event_frame` 是最小可回放事件单元。
- `strict_story` 只服务内部 event packing，不是 public read model。
- `digest` 是唯一 public read model。
- `pipeline_run` / `source_run_state` 只承载运行态，不承载业务真相。
- Redis 只负责 broker、锁和 rate limiting，不保存核心业务真相。
- Qdrant 只是检索副本，回源与引用始终以 Postgres 为准。

## Runtime 链路

1. `NewsCollectionService`
   抓取来源文章，解析 `canonical_url`，只产出 article seed。

2. `ArticleCollectionService`
   对 article seed 执行去重入库，写入 `article` 并标记 `parse_status=pending`。

3. `ArticleParseService`
   解析详情页，落纯文本 Markdown 和 `article_image`，更新 `parse_*` 状态。

4. `EventFrameExtractionService`
   从已解析 Markdown 中抽取稀疏 `article_event_frame`，更新 `event_frame_*` 状态。

5. `StrictStoryPackingService`
   以 business day 为单位将 event frame 打包为 `strict_story`，必要时复用同日 key。

6. `DigestGenerationService`
   选择当日 `strict_story` 组合，生成 `digest` 正文和 public feed 元数据。

7. `ArticleRagService`
   基于全量 parse-complete 文章正文和图片来源文本构建 Qdrant 副本。

8. `DailyRunCoordinatorService` + Celery
   协调 source collection、article stages、strict story packing 和 digest generation。

9. `DeepResearchGraphService` + `DeepResearchService`
   按需编译带 Postgres checkpoint 的 LangGraph deep research runtime，并把 deep research 请求持久化到现有
   `chat_session` / `chat_message` 视图层。

## 关键实体

### `article`

- 原始来源 metadata
- `markdown_rel_path`
- `parse_*`
- `event_frame_*`

### `article_image`

- 来源图片 URL / normalized URL
- 位置与角色
- `alt_text` / `caption_raw` / `credit_raw` / `context_snippet`
- 可选视觉分析结果字段

### `article_event_frame`

- `event_type`
- `subject_json`
- `action_text`
- `object_text`
- `place_text`
- `collection_text`
- `season_text`
- `show_context_text`
- `evidence_json`
- `signature_json`

### `strict_story`

- `strict_story_key`
- `business_date`
- `synopsis_zh`
- `signature_json`
- `created_run_id`

### `digest`

- `digest_key`
- `business_date`
- `facet`
- `title_zh`
- `dek_zh`
- `body_markdown`
- `hero_image_url`
- `source_article_count`
- `source_names_json`

## 运行方式

- Celery `content` queue 负责 source collection、article parse、event-frame extraction
- Celery `aggregation` queue 负责 strict-story packing 和 digest generation
- `DailyRunCoordinatorService` 负责当前 business day 的重扫、重试、stale reclaim 和 batch gating
- 本地 review run 使用 `backend/app/scripts/dev_run_today_digest_pipeline.py`
  - `--published-today-only`：仅保留 `published_at` 命中当天 business day 的文章用于本地 review
  - `--llm-artifact-dir PATH`：仅本次 dev run 导出 `KARL_LLM_DEBUG_ARTIFACT_DIR`，用于 LLM 原始产物落盘

脚本入口见 [backend/app/scripts/README.md](/root/karl-fashion-feed/backend/app/scripts/README.md)。

## `sources.yaml`

当前支持两类来源：

- `type: rss`
- `type: web`

统一约定：

- 所有来源必须显式声明 `type`
- RSS 使用 `feed_url`
- Web 使用 `start_urls + discovery`
- `enabled: false` 的来源不会进入 runtime

## 当前边界

- 当前 public API 暴露 auth / chat / memory / rag / digest，以及
  `POST /api/v1/deep-research/messages/stream`
- deep research 不单独引入 research session 表；继续复用 `chat_session` /
  `chat_message` 作为用户可见持久化层，LangGraph thread continuity 走 Postgres checkpoint
- 旧 `story` / `story_article` 表不允许出现在 runtime schema bootstrap 中
- 旧 story-era prompt、schema、service 模块不再参与当前代码路径

## Scripts

脚本命令用法见 [backend/app/scripts/README.md](/root/karl-fashion-feed/backend/app/scripts/README.md)。
