# Models

本目录维护 ORM 实体的长期设计说明。

当前代码处于一次模型边界迁移过程中：

- `Article` 仍定义在 [article.py](/root/karl-fashion-feed/backend/app/models/article.py)
- `ArticleImage` 现在只定义在 [image.py](/root/karl-fashion-feed/backend/app/models/image.py)
- `article.py` 仅保留 `Article` 和 article storage schema bootstrap

后续维护以本文件描述的目标边界为准，而不是以当前临时实现位置为准。

## 目标边界

- [article.py](/root/karl-fashion-feed/backend/app/models/article.py)
  只承载 article 主实体
- [image.py](/root/karl-fashion-feed/backend/app/models/image.py)
  承载 image 相关实体，作为独立 image domain 的 ORM 入口
- [story.py](/root/karl-fashion-feed/backend/app/models/story.py)
  承载 story / story_article / pipeline_run
- [retrieval.py](/root/karl-fashion-feed/backend/app/models/retrieval.py)
  承载 retrieval bridge 层

## Image ORM

### 定位

`ArticleImage` 不再只是 article 的附属字段表，而是 image domain 的事实真相层。

它后续要支撑：

- article 与 image 的归属关系
- image asset 基础元数据
- visual analysis 结果
- image retrieval / RAG collection 的时间与过滤条件
- 后续可能的 moderation / storage / embedding 状态

### 当前建议模型

`ArticleImage` 当前保留如下字段，后续如需改名或拆 metadata，也应围绕这组语义演进。

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
  图片附近抽取到的上下文文本片段，供 visual analysis 和 retrieval 使用。

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

- `visual_status`
  visual LLM 处理状态，如 `pending`、`done`、`failed`。

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

## 后续迁移建议

当前推荐迁移顺序：

1. 所有 service / tests 统一通过 [__init__.py](/root/karl-fashion-feed/backend/app/models/__init__.py) 或 [image.py](/root/karl-fashion-feed/backend/app/models/image.py) 引用 `ArticleImage`
2. 再把 schema bootstrap 从 [article.py](/root/karl-fashion-feed/backend/app/models/article.py) 逐步抽到更中性的 storage/schema 模块

在完成上述步骤前，代码可以临时兼容，但后续维护应持续朝这个方向收敛。
