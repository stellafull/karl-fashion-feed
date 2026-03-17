# RAG Collection Design

## 文档状态

- 本文档是 KARL FASHION FEED 的 RAG collection 规范文档。
- 本文档描述目标最终态，不等同于当前代码完成度。
- 本文档优先于 [backend/README.md](/root/karl-fashion-feed/backend/README.md) 中所有关于 article / image / Milvus / RAG collection 的简写描述。
- 后续如果 collection 设计要变更，必须先改本文档，再改实现。

## 1. 核心不变量

- `article` 和 canonical Markdown 是文本事实真相源。
- `article_image` 是图片事实真相源与文章归属关系真相源。
- `story` 只服务阅读，不进入 RAG collection 真相层。
- Milvus 永远只是检索副本，可以重建，不承载业务真相。
- 只有 `should_publish=true` 的文章和其关联图片允许进入 shared collection。
- 引用和回溯必须回到 `article` / Markdown / `article_image`，不得直接把 Milvus 命中结果当作引用真相。
- Milvus 物理上只保留一个 shared collection，通过 `modality` 区分 `text` / `image`。
- 物理上是单 collection，但逻辑上仍保留 `text_only`、`image_only`、`fusion` 三种 query plan。
- `fusion` 不做 text/image 统一候选池混排，而是按 `modality` 分 lane recall、各自 rerank、最后 merge。
- 默认 freshness decay 只允许发生在 final score layer，不允许进入 ANN recall 或 rerank 输入。

## 2. 真相层与检索主键

### 2.1 真相层职责

- `article`
  - 保存来源、发布时间、中文 enrichment、分类标签、Markdown 路径。
  - 是文本内容、来源信息、读者可引用事实的根记录。
- canonical Markdown
  - 是 article 正文的 canonical 表示。
  - 只保存纯文本正文，不保存图片占位和视觉分析结果。
- `article_image`
  - 保存图片 URL、位置、caption、OCR、视觉分析结果。
  - `article_image.article_id` 是图片归属到文章的唯一真相关系。

### 2.2 检索主键规则

系统不再维护独立的桥接表。

Milvus 每条记录直接使用稳定命名规则生成检索主键：

- text 单元
  - `text:{article_id}:{chunk_index}`
- image 单元
  - `image:{article_image_id}`

要求：

- 同一逻辑单元重建时主键不漂移
- text 与 image 命名空间明确分离
- 回源不依赖额外桥接表
- text 主键能直接定位到 `article` + `chunk_index`
- image 主键能直接定位到 `article_image`

### 2.3 Shared Collection 最小字段

Milvus shared collection 最小字段固定如下：

| 字段 | 说明 |
| --- | --- |
| `retrieval_unit_id` | shared collection 唯一主键，用于回源、重建、引用。 |
| `modality` | 检索模态，固定枚举：`text` / `image`。 |
| `unit_kind` | 检索单元类型，固定枚举：`text_chunk` / `image_asset`。 |
| `article_id` | 所属 article 主键，所有记录必填。 |
| `article_image_id` | image 记录必填，text 记录为空。 |
| `chunk_index` | text 记录在 article 内的稳定顺序，image 记录为空。 |
| `position` | image 记录在 article 内的稳定顺序，text 记录为空。 |
| `role` | image 记录角色，如 `hero` / `inline` / `gallery`，text 记录为空。 |
| `heading_path` | text 记录标题路径扁平化表示，image 记录为空。 |
| `content` | 统一检索文本字段，仅服务 sparse embedding、rerank、调试与检索内部处理；不要求中文可读，不承诺直接给用户展示。 |
| `source_name` | 来源名称。 |
| `source_lang` | 来源语言。 |
| `source_type` | 数据来源类型，如用户上传、爬取、第三方接口。 |
| `category` | 主分类。 |
| `tags_json` | 标签列表。 |
| `brands_json` | 品牌列表。 |
| `ingested_at` | 唯一时间字段，直接复用数据库 `article.ingested_at`；既用于显式时间过滤，也用于默认 freshness decay。 |
| `dense_vector` | dense 向量。 |
| `sparse_vector` | sparse 向量，或 sparse 等价检索表示。 |

shared collection 的 nullability 规则固定如下：

- text 记录
  - `article_image_id = null`
  - `position = null`
  - `role = null`
- image 记录
  - `chunk_index = null`
  - `heading_path = null`

shared collection 不保留以下字段：

- `pk`
  - 主键就是 `retrieval_unit_id`。
- `published_at`
  - 时间统一复用 `ingested_at`。
- `last_fetched_at`
  - 保留在 `article_image` 真相层，不进入 shared collection。
- `index_version`
  - single live collection 通过直接重建处理 schema 变化，不在 schema 中持久化版本。
- `is_active`
  - single live collection 不需要该字段。

## 3. Shared Collection 中的检索单元

### 3.1 索引对象

- shared collection 只收录 `should_publish=true` 的可检索叶子单元。
- `story`、未发布 article、Milvus 回写结果都不进入 shared collection。
- 图片不进入 canonical Markdown；image lane 只从 `article_image` 派生。

### 3.2 Text 单元规则

- text 单元来自 canonical Markdown 切块。
- 切块策略固定为“标题层级感知 + recursive chunker + overlap”：
  1. 从 canonical Markdown 读取 article 正文。
  2. 按 heading 构建逻辑语义段。
  3. 逻辑语义段超长时，继续拆成多个 `text_chunk`。
  4. 只有最终 `text_chunk` 进入 shared collection。
- `chunk_index` 必须在 article 内稳定递增，重跑同一逻辑单元不得漂移。
- text 单元的 `content` 生成规则固定为：
  - `title_zh/title_raw + heading_path + chunk 正文 + summary_zh + tags + brands + source_name`
- text 单元命中时，返回的是 text evidence，可直接使用 `content`。

### 3.3 Image 单元规则

- image 单元来自 `article_image` 派生的 `image_asset`。
- 只有满足以下条件的图片允许进入 shared collection：
  - 父 article 的 `should_publish=true`
  - `article_image` 已存在稳定 `image_id`
  - 图片存在至少一类文本投影信号：`alt_text`、`caption_raw`、`credit_raw`、`context_snippet`、`ocr_text`、`observed_description`、`contextual_interpretation` 之一
- image 单元的 `content` 生成规则固定为：
  - `caption_raw + alt_text + ocr_text + observed_description + contextual_interpretation + context_snippet + 父 article 标题/摘要/标签/品牌`
- image 单元的 `content` 只服务 sparse embedding、rerank 与内部检索，不作为最终返回本体。
- image 单元命中时，返回的是 image evidence，本体是图片和其 locator，而不是 image2text 文本。

## 4. Milvus 副本与重建策略

### 4.1 Collection 命名

- Milvus 只保留一个在线 shared collection：
  - `kff_retrieval`
- 查询永远直接走该 collection，不做 alias 切换。

### 4.2 同步顺序

写入顺序固定如下：

1. article / canonical Markdown / article_image 落事实真相。
2. enrichment、图片分析完成后，直接生成检索主键与检索文本。
3. 生成 dense/sparse 表示并写入 Milvus shared collection。

### 4.3 重建规则

- Milvus 副本损坏时，必须从数据库真相层和 Markdown 全量重建。
- 重建不得依赖旧 Milvus 行内容。
- schema、文本模板、embedding 模型发生变化时，直接重建当前唯一在线 collection。
- 重建完成后至少校验：
  - unit 总数
  - `modality=text` 的记录数
  - `modality=image` 的记录数
  - `retrieval_unit_id` 去重一致性

## 5. Query 与检索链路

### 5.1 Query Planner

一次 query 的标准链路固定如下：

1. query 归一化
2. `query planner` 识别用户 intent、输入模态、输出目标
3. 生成 `query_plan`
4. 按 `query_plan` 在 shared collection 上执行对应逻辑 lanes
5. 回溯真相源
6. 把结构化 retrieval evidence 交给 RAG answer agent

`query planner` 的最小输出契约固定如下：

- `plan_type`
  - `text_only`
  - `image_only`
  - `fusion`
- `text_query`
  - 可空。仅当 plan 需要 text lane 时生成。
- `image_query`
  - 可空。仅当 plan 需要 image lane 时生成。
- `filters`
  - 来源、分类、时间范围、品牌等 metadata 过滤条件。
- `filters.time_range`
  - 可空。显式时间约束统一作用于 `ingested_at`。
- `apply_default_freshness`
  - bool。无显式时间约束时为 `true`；有显式时间约束时为 `false`。
- `output_goal`
  - 用于区分 reference lookup、report、inspiration、similarity search 等任务目标。

### 5.2 Query Plan 类型

#### `text_only`

- 只在 shared collection 上查询 `modality='text'`。
- 只对 text candidates 做 rerank。
- 返回 text evidence，不附带 image results。

#### `image_only`

- 只在 shared collection 上查询 `modality='image'`。
- 只对 image candidates 做 rerank。
- image rerank 完成后，再补同 article 的 grounding text。
- grounding text 只用于解释图片语境，不参与 image 排序。

#### `fusion`

- 同时生成 `text_query` 和 `image_query`。
- 在 shared collection 上执行两次逻辑查询：
  - 一次 `modality='text'`
  - 一次 `modality='image'`
- 两个 lane 各自 recall、各自 rerank、各自 final scoring。
- 最后按 `article_id` merge + dedupe，形成 article-level evidence package。
- `fusion` 不做 text/image 混合统一 rerank。

### 5.3 Recall

- text lane
  - dense recall 与 sparse recall 并行执行。
  - metadata filter 在召回阶段生效。
- image lane
  - dense recall 基于图片向量。
  - sparse recall 基于图片文本投影。
  - metadata filter 同样在召回阶段生效。
- 显式时间约束统一在 recall 阶段对 `ingested_at` 做 hard filter。
- 默认 freshness 不进入 recall。

### 5.4 Rerank 与 Final Scoring

- rerank 只负责 relevance，不注入默认 freshness。
- text 与 image 各自独立 rerank。
- 默认 freshness 只在 final score layer 叠加。

默认流程固定为：

1. recall
2. rerank 得到 `rerank_score`
3. 归一化为 `normalized_relevance_score`
4. 若 `apply_default_freshness=true`，根据 `ingested_at` 计算 `freshness_score`
5. 生成 `final_score`

公式示例：

- `freshness_score = exp(-age_days / half_life_days)`
- `final_score = alpha * normalized_relevance_score + beta * freshness_score`

说明：

- `alpha`、`beta`、`half_life_days` 是配置项，不是 schema。
- query 显式给时间范围时：
  - recall 阶段先做 `ingested_at` hard filter
  - `apply_default_freshness=false`
  - `final_score = normalized_relevance_score`

### 5.5 Merge 与 Dedupe

- `text_only`
  - 只返回 text results。
- `image_only`
  - 只返回 image results。
  - 每个 image result 必须附带 grounding text references。
- `fusion`
  - 去重主键是 `article_id`
  - 保留排序最高的 text chunks 作为文本证据
  - 保留排序最高的 image hits 作为视觉证据
  - 同一 article 的 text 与 image 证据聚合为一个 article-level evidence package

retrieval 层不尝试把多个 article 再聚成 story/topic 级 bundle。

### 5.6 Retrieval 输出契约

传给 RAG agent 的 retrieval result 最小字段固定如下：

- `query_plan`
- `text_results`
- `image_results`
- `packages`
- `citation_locators`

单条证据最小字段固定如下：

- `retrieval_unit_id`
- `modality`
- `article_id`
- `article_image_id`
- `score`
- `citation_locator`
- `content`

说明：

- text 命中时，`content` 可作为 text evidence 返回。
- image 命中时，`content` 只作为内部检索文本投影保留，不作为最终图片结果本体返回。

RAG agent 必须再回源读取 article Markdown 或 `article_image`，不能直接把 Milvus 命中行当最终引用内容。

## 6. 当前代码现状与差距

截至 2026-03-17，仓库当前已落地的部分：

- article 采集、Markdown 落地、图片占位与 `article_image` 存储
- `ArticleEnrichmentService`
- dense embedding
- `ArticleClusterService`
- `StoryGenerationService`
- `DailyPipelineService`

当前尚未落地、但本文已固定为目标规范的部分：

- shared collection 的 Milvus 副本同步
- sparse embedding 生成链路
- planner 驱动的 shared collection query service
- lane 级 rerank、final scoring、merge / dedupe
- image retrieval grounding logic

当前 [milvus_service.py](/root/karl-fashion-feed/backend/app/service/RAG/milvus_service.py) 中的 schema 只是过渡实现，不代表本文定义的 shared collection 目标规范。

## 7. 非目标与可调参数

本文不冻结以下内容：

- 具体 dense / sparse / rerank 模型厂商与模型名
- top-k、score threshold、召回 budget
- query 重写 prompt
- `alpha`、`beta`、`half_life_days` 等时间衰减参数
- 最终 answer agent 的提示词和回答模板

本文只冻结下面这些边界：

- truth source 在哪里
- 稳定检索主键如何命名
- shared collection 的最小字段与语义
- planner 驱动的 retrieval 路由
- `content` 的机器检索字段语义
- `ingested_at` 作为唯一时间字段的规则
- freshness 发生在 final score layer
- 回源引用必须如何做
