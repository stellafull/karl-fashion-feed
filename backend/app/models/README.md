# Models

本目录维护当前 digest runtime 的 ORM 设计说明。

唯一有效的聚合链路是：

`article -> article_event_frame -> strict_story -> digest`

其中：

- `article` / `article_image` 是事实真相源
- `article_event_frame` 是最小可回放事件单元
- `strict_story` 只服务内部打包
- `digest` 是唯一 public read model

旧 `story` / `story_article` 不再属于当前 schema contract。

## 当前边界

- [article.py](/root/karl-fashion-feed/backend/app/models/article.py)
  article 真相源，以及 digest runtime schema bootstrap 入口
- [image.py](/root/karl-fashion-feed/backend/app/models/image.py)
  article_image 真相源，保留图片来源文本和可选视觉分析结果
- [runtime.py](/root/karl-fashion-feed/backend/app/models/runtime.py)
  pipeline_run / source_run_state 运行态
- [event_frame.py](/root/karl-fashion-feed/backend/app/models/event_frame.py)
  article_event_frame 结构化事件帧
- [strict_story.py](/root/karl-fashion-feed/backend/app/models/strict_story.py)
  strict_story 及其 frame/article bridge
- [digest.py](/root/karl-fashion-feed/backend/app/models/digest.py)
  digest 及其 strict_story/article bridge

## Image ORM

### 定位

`ArticleImage` 不再只是 article 的附属字段表，而是 image domain 的事实真相层。

它当前要支撑：

- article 与 image 的归属关系
- image asset 基础元数据
- source-provided text truth
- optional visual analysis 结果
- image retrieval / RAG collection 的时间与过滤条件
- 后续可能的 moderation / storage / embedding 状态

#### 1. 身份与归属

- `image_id`
  图片记录唯一主键。
- `article_id`
  图片归属到哪篇 article。

#### 2. 原始来源与去重

- `source_url`
  采集时拿到的原始图片 URL。
- `normalized_url`
  归一化后的图片 URL，用于减少同图不同 URL 形式造成的重复。

#### 3. 在文章中的位置

- `role`
  图片在 article 中的角色，如 `hero`、`inline`、`gallery`。
- `position`
  图片在 article 内的顺序位置。

#### 4. 原始文本上下文

- `alt_text`
  源页面 `img alt` 文本。
- `caption_raw`
  图片 caption 原文。
- `credit_raw`
  图片署名、来源说明、摄影师、供图方等原文。
- `context_snippet`
  图片附近抽取到的上下文文本片段，供 retrieval 和可选 visual analysis 使用。

#### 5. 抽取来源信息

- `source_kind`
  图片是从哪类页面结构或抽取来源拿到的。
  当前更偏爬虫语义，例如 `img`、`figure`、`picture`、`meta`、`json_ld`。
- `source_selector`
  图片在页面中的定位线索。
  当前更偏抽取实现语义，例如 CSS selector、DOM path、规则标识。

说明：

- 这两个字段当前仍然有效，但名字偏向爬虫实现。
- 如果后续 image domain 继续独立演进，可以考虑改成更稳定的业务语义名：
  - `origin_kind`
  - `origin_locator`
  - 或统一收敛到 `extraction_metadata_json`

#### 6. 资产状态与时间信号

- `fetch_status`
  图片资产当前抓取或处理状态。
- `last_fetched_at`
  最近一次抓取、刷新或确认该资产的时间。
- `mime_type`
  资源 MIME type，如 `image/jpeg`。
- `width`
  图片宽度。
- `height`
  图片高度。

说明：

- `last_fetched_at` 建议保留在 image 真相层，用于 image 资产相关的 filter、诊断和处理状态分析。
- 在当前 shared collection 设计里，默认全局检索时间锚点不是 `last_fetched_at`，而是 article 的 `ingested_at`。
- `width` / `height` 建议保留，属于 image asset 的稳定基础元数据，后续在展示、质量过滤、横竖图判断中都有用。

#### 7. Visual Analysis 状态

- `visual_status` / `visual_attempts`
  图片视觉分析的可选运行态。
  这不是 digest 主链路的前置条件。

#### 8. Visual Analysis 结果

- `observed_description`
  只能描述肉眼可见事实，不带额外推断。
- `ocr_text`
  图片中可见文字的 OCR 结果。
- `visible_entities_json`
  图像中明确可见的实体列表。
- `style_signals_json`
  图像中可提取的时尚风格信号列表。
- `contextual_interpretation`
  结合 article 上下文得到的解释，可包含有限语义推断。
- `analysis_metadata_json`
  附加分析信息容器，当前可承载 `context_used`、`confidence` 等非主字段结果。

## Digest Runtime Contract

### Article

`article` 是事实真相源，不承载 story-era publish/runtime contract。

必须保留：

- 原始来源字段和 `markdown_rel_path`
- `parse_*`
- `event_frame_*`

不再把 `should_publish`、`reject_reason`、`cluster_text`、`enrichment_*` 视为当前模型契约。

不再持久化 article 级 normalization 中间态，也不持久化 article 级中文标题、摘要、正文。
任何中文生成都应延后到各自下游 prompt，例如 event frame extraction 或最终 digest generation。

### Pipeline Runtime

`pipeline_run` 只保留 run 级批处理状态，并显式建模两段 batch stage：

- `strict_story_*`
- `digest_*`

`source_run_state` 负责每个 source 在一次 run 内的采集状态。

### Read Model Replacement

旧 `story` / `story_article` 不允许出现在当前 runtime schema bootstrap 中。
当前对外导出的聚合链路是：

- `article_event_frame`
- `strict_story`
- `digest`

## 设计原则

### 1. ORM 字段保留稳定事实

模型层优先保存长期稳定、可复用、可检索的事实字段。

例如：

- `width`
- `height`
- `last_fetched_at`
- `ocr_text`
- `observed_description`

这些字段虽然不是当前每一步都直接使用，但属于后续 image retrieval / ranking / display 的稳定基础。

### 2. 不把一次性 prompt 结构硬编码进 ORM

如果某个字段只是某次实验 prompt 的临时产物，不要直接提升为 ORM 主字段。

这类信息优先考虑：

- 放入 `analysis_metadata_json`
- 或先停留在 service/schema 层，确认长期稳定后再进入 ORM

### 3. 不把爬虫实现细节直接固化成长期领域语言

`source_kind` / `source_selector` 当前可以保留，但后续如果 image domain 要长期维护，应优先改成 image 领域自己的语言，而不是延续爬虫实现术语。

### 4. 为单 collection / 多 modality 检索保留扩展空间

当前检索设计可以先走单 collection，并通过 `modality=text|image` 过滤。

在这种前提下，image ORM 仍然应该保留足够的独立信息，以支持：

- image-only filter
- image 资产级时间过滤
- image 资产级附加时间特征
- article/image 回源引用
- 后续必要时拆独立 image collection

当前目标仍然是单 shared collection + `modality` 过滤；只有在 retrieval 架构再次 redesign 时，才考虑重新拆成独立 image collection。

## 迁移约束

- Postgres 是唯一业务真相。
- Redis 仅做 broker、锁和短期协调态，不承载核心真相。
- 新 schema bootstrap 走 replacement-only，不保留 story-era 双轨逻辑。
