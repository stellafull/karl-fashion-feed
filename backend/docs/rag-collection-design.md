# RAG Collection Design

## 文档状态

- 本文档是 KARL FASHION FEED 的 RAG collection 规范文档。
- 本文档描述目标最终态，不等同于当前代码完成度。
- 本文档优先于 [backend/README.md](/root/karl-fashion-feed/backend/README.md) 中所有关于 `retrieval_unit_ref`、Milvus、RAG collection 的简写描述。
- 后续如果 collection 设计要变更，必须先改本文档，再改实现。

## 1. 核心不变量

- `article` 和 canonical Markdown 是文本事实真相源。
- `article_image` 是图片事实真相源与文章归属关系真相源。
- `story` 只服务阅读，不进入 RAG collection 真相层。
- `retrieval_unit_ref` 是检索桥接真相层，负责把文本或图片检索单元桥接到事实源和向量副本。
- Milvus 永远只是检索副本，可以重建，不承载业务真相。
- 只有 `should_publish=true` 的文章和其关联图片允许进入任何 collection。
- 引用和回溯必须回到 `article` / Markdown / `article_image` / `retrieval_unit_ref`，不得直接把 Milvus 命中结果当作引用真相。
- text collection 与 image collection 物理分离，不能混成一个 collection。
- 每次 query 必须先经过 `query planner`，由 planner 决定执行 `text_only`、`image_only` 或 `fusion`。
- text lane 与 image lane 都采用各自的 `hybrid recall + rerank`，但是否启用由 `query planner` 决定。

## 2. 真相层与桥接层

### 2.1 真相层职责

- `article`
  - 保存来源、发布时间、中文 enrichment、分类标签、Markdown 路径。
  - 是文本内容、来源信息、读者可引用事实的根记录。
- canonical Markdown
  - 是 article 正文的 canonical 表示。
  - 图片仅以 `[image:<image_id>]` 占位，不把视觉分析结果回写到 Markdown。
- `article_image`
  - 保存图片 URL、位置、caption、OCR、视觉分析结果。
  - `article_image.article_id` 是图片归属到文章的唯一真相关系。
- `retrieval_unit_ref`
  - 保存“某个可检索单元”如何映射回 article 或 article_image。
  - 保存该单元对应的索引版本、Milvus 副本位置、embedding 版本、content locator。

### 2.2 `retrieval_unit_ref` 逻辑字段

`retrieval_unit_ref` 统一覆盖 text 和 image 两类 retrieval unit，最小字段职责固定如下：

| 字段 | 说明 |
| --- | --- |
| `retrieval_unit_id` | 检索单元主键。建议使用基于 `index_version + modality + logical_locator` 的稳定生成方式，同一版本重跑不变。 |
| `modality` | 固定枚举：`text` / `image`。 |
| `unit_kind` | 固定枚举：`text_chunk` / `text_group` / `image_asset`。`text_group` 只用于递归拆块时的父节点，不直接参与召回。 |
| `article_id` | 必填。所有检索单元都必须归属于某一篇 article。 |
| `article_image_id` | 仅图片单元必填，文本单元为空。图片与文章的归属真相仍以 `article_image.article_id` 为准。 |
| `parent_unit_id` | 可空。仅当一个语义段被递归拆成多个子 chunk 时，用于指向 `text_group` 父节点；普通文章可以为空。 |
| `chunk_index` | 叶子检索单元在 article 内的稳定顺序。 |
| `heading_path_json` | 语义段所在标题路径，按数组保存；没有标题时为空数组。 |
| `content_locator_json` | 精确回溯定位信息。文本至少包含 `markdown_rel_path`、block 范围、字符范围或段落序号；图片至少包含 `image_id`、`position`、`role`。 |
| `canonical_text` | 该检索单元的规范化文本投影。文本单元为 chunk 文本；图片单元为 OCR/caption/视觉描述等拼接结果。 |
| `dense_embedding_ref` | dense 编码器与产物版本引用，不直接等于向量本体。 |
| `sparse_embedding_ref` | sparse 编码器与产物版本引用。 |
| `milvus_collection` | 当前副本所在的物理 collection 名称。 |
| `milvus_primary_key` | Milvus 行主键。默认与 `retrieval_unit_id` 一致。 |
| `index_version` | 当前 collection 设计版本。chunk 规则、字段结构、dense/sparse 模型族变化时必须升级。 |
| `created_run_id` | 生成该 retrieval unit 的 pipeline run。 |
| `is_searchable` | 是否参与召回。`text_group=false`，叶子文本块与图片块为 `true`。 |
| `metadata_json` | 其余检索期需要但不值得单列的元数据。 |

### 2.3 版本规则

- `index_version` 不是每日 run 的流水号，而是索引设计版本号。
- 下面任一变化都必须升级 `index_version` 并触发重建：
  - text chunking 规则变化
  - dense 或 sparse 编码器族变化
  - text/image collection 标量字段变化
  - rerank 前候选构造所依赖的 canonical_text 模板变化
- 每日增量写入只写当前 active `index_version`。
- 老版本 collection 可以保留到 alias 完成切换，再清理。

## 3. Text Collection 设计

### 3.1 索引对象

- text collection 只收录 `should_publish=true` 的 article 派生文本单元。
- `story`、未发布 article、纯图片说明行、Milvus 回写结果都不进入 text collection。
- `[image:<image_id>]` 占位不是独立文本检索单元，只作为上下文边界。

### 3.2 切块规则

text chunking 固定为“标题层级感知 + recursive chunker + overlap”：

1. 从 canonical Markdown 读取 article 正文。
2. 按 heading 构建逻辑语义段；每段保留 `heading_path_json`。
3. 没有稳定 heading 的网页文章，退化为按段落组装逻辑语义段，不强行制造 section/subsection 实体。
4. 每个逻辑语义段如果长度在阈值内，直接成为一个 `text_chunk`。
5. 逻辑语义段超长时，先创建一个 `text_group` 父节点，再在其内部使用 recursive chunker 继续拆分为多个 `text_chunk` 子节点。
6. recursive chunker 的分隔优先级固定为：空行、换行、句末标点、空格。
7. 所有叶子 `text_chunk` 都应用 overlap；overlap 是配置参数，不是 schema，不写死具体数值。
8. `chunk_index` 必须在 article 内稳定递增，重跑同一版本不得漂移。

### 3.3 Text 单元内容模板

- `canonical_text`
  - 由 `article.title_zh` 或 `title_raw`、`heading_path`、chunk 正文、`summary_zh`、标签、品牌、来源名组成。
  - 保持中文可读，便于 rerank 与引用展示。
- dense 编码输入
  - 以 `canonical_text` 为主，不再额外拼接不可解释的工程字段。
- sparse 编码输入
  - 以 `canonical_text` 为主，可补充来源名、分类、品牌、标签等稀疏可匹配词。

### 3.4 Text Collection 最小字段

Milvus text collection 最小字段固定如下：

| 字段 | 说明 |
| --- | --- |
| `pk` | 主键，默认等于 `retrieval_unit_id`。 |
| `retrieval_unit_id` | 检索桥接主键。 |
| `article_id` | article 归属。 |
| `chunk_index` | article 内顺序。 |
| `heading_path` | 扁平化标题路径，用于过滤和调试。 |
| `canonical_text` | 召回与 rerank 统一文本。 |
| `source_name` | 来源。 |
| `source_lang` | 源语言。 |
| `category` | 主分类。 |
| `tags_json` | 标签。 |
| `brands_json` | 品牌。 |
| `published_at` | 发布时间。 |
| `dense_vector` | dense 向量。 |
| `sparse_vector` | sparse 向量。 |
| `index_version` | 索引版本。 |
| `is_active` | 当前是否有效。 |

## 4. Image Collection 设计

### 4.1 索引对象

- image collection 物理上独立于 text collection。
- 只有满足以下条件的图片允许入 image collection：
  - 父 article 的 `should_publish=true`
  - `article_image` 已存在稳定 `image_id`
  - 图片可获取并可生成 dense 表示
  - 至少存在一类可检索文本信号：`alt_text`、`caption_raw`、`credit_raw`、`context_snippet`、`ocr_text`、`observed_description`、`contextual_interpretation` 之一
- 如果图片无法获取或 dense 表示失败，该图片不进入 image collection。

### 4.2 Image 单元内容模板

- 图片 dense 表示
  - 来自图片本体的多模态 dense encoder。
- 图片 sparse 表示
  - 基于图片文本投影生成，文本来源依次包括：
    - `caption_raw`
    - `alt_text`
    - `ocr_text`
    - `observed_description`
    - `contextual_interpretation`
    - `context_snippet`
    - 父 article 的标题、摘要、品牌、标签
- 图片 `canonical_text`
  - 用于 image rerank、fusion merge 和调试，必须可读，必须包含图片语义与文章上下文。

### 4.3 Image Collection 最小字段

Milvus image collection 最小字段固定如下：

| 字段 | 说明 |
| --- | --- |
| `pk` | 主键，默认等于 `retrieval_unit_id`。 |
| `retrieval_unit_id` | 检索桥接主键。 |
| `article_id` | 文章归属。 |
| `image_id` | 对应 `article_image.image_id`。 |
| `role` | hero / inline / gallery 等角色。 |
| `position` | 在文章中的顺序。 |
| `image_url` | 供调试和重建使用。 |
| `canonical_text` | 图片文本投影。 |
| `source_name` | 来源。 |
| `source_lang` | 源语言。 |
| `category` | 主分类。 |
| `tags_json` | 标签。 |
| `brands_json` | 品牌。 |
| `published_at` | 发布时间。 |
| `dense_vector` | 图片多模态 dense 向量。 |
| `sparse_vector` | 图片文本投影的 sparse 向量。 |
| `index_version` | 索引版本。 |
| `is_active` | 当前是否有效。 |

## 5. Milvus 副本与同步策略

### 5.1 Collection 命名

- text 与 image 使用独立版本化 collection：
  - `kff_text_v{index_version}`
  - `kff_image_v{index_version}`
- 对外只暴露稳定 alias：
  - `kff_text_active`
  - `kff_image_active`
- 查询永远走 alias，不直接写死物理 collection 名。

### 5.2 同步顺序

写入顺序固定如下：

1. article / canonical Markdown / article_image 落事实真相。
2. enrichment、图片分析完成后，生成 `retrieval_unit_ref`。
3. 生成 dense/sparse 表示并写入 Milvus 对应 collection。
4. 写回 `milvus_collection`、`milvus_primary_key`、embedding refs、`index_version`。
5. alias 切换前不得把新版本视为 active。

### 5.3 重建规则

- Milvus 副本损坏时，必须从数据库真相层和 Markdown 全量重建。
- 重建不得依赖旧 Milvus 行内容。
- 重建完成后先校验 unit 数量、可搜索数量、按 modality 的计数，再切 alias。
- 老版本清理不影响 `retrieval_unit_ref` 对引用的可解释性。

## 6. Query 与检索链路

### 6.1 Query Planner

一次 query 的标准链路固定如下：

1. query 归一化
2. `query planner` 识别用户 intent、输入模态、输出目标
3. 生成 `query_plan`
4. 按 `query_plan` 执行对应的 retrieval lanes
5. 回溯真相源
6. 把结构化 retrieval evidence 交给 RAG answer agent

`query planner` 是强制前置层，不能默认双路并发。planner 最小输出契约固定如下：

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
- `output_goal`
  - 用于区分 reference lookup、report、inspiration、similarity search 等任务目标。

### 6.2 Query Plan 类型

#### `text_only`

- 适用于纯文本信息查找、报道综述、趋势总结等明显以文本证据为主的请求。
- 只执行 text collection 的 hybrid recall。
- 只对 text hits 做 rerank。
- 返回 text evidence，不附带 image lane 结果。

#### `image_only`

- 适用于“给一张图，找类似面料 / 廓形 / 纹理 / look / 视觉元素”这类 image-dominant 请求。
- 只执行 image collection 的 hybrid recall。
- 只对 image hits 做 rerank。
- image rerank 完成后，必须为每个高分图片命中补充同 article 的 grounding text。
- grounding text 默认来自同一 article 内与该图片最邻近的 text chunk，优先依据 `article_image.position`、`context_snippet` 与 chunk 顺序回溯。
- grounding text 只用于解释图片所处的品牌、系列、面料或语境，不参与 image 排序。
- 如果同文邻近 chunk 不可用，允许降级为 article 标题与摘要级 grounding，但这是 fallback，不是默认路径。

#### `fusion`

- 适用于“时尚趋势报告”“给我一份带视觉例子的分析”“同时参考报道与图片”这类多模态输出请求。
- 同时生成 `text_query` 和 `image_query`。
- text lane 与 image lane 各自执行 hybrid recall、各自 rerank。
- `fusion` 不做 text/image 混合统一 rerank。
- 两路结果在 rerank 后按 `article_id` merge + dedupe，形成 article-level evidence package。
- 每个 article-level package 保留该 article 的 top text chunks 与 top image hits，交由 answer LLM 决定最终展示几张图和引用哪些文字。

### 6.3 Hybrid Recall

- text lane
  - dense recall 与 sparse recall 并行执行。
  - metadata filter 在召回阶段生效。
- image lane
  - dense recall 基于图片多模态向量。
  - sparse recall 基于图片文本投影。
  - metadata filter 同样在召回阶段生效。
- 两个 lane 是否执行，由 `query planner` 决定。
- text 与 image 的召回 budget 可以不同，但属于配置，不写死在架构层。

### 6.4 Rerank、Merge 与 Dedupe

- `text_only`
  - 只对 text candidates 做 rerank。
- `image_only`
  - 只对 image candidates 做 rerank。
  - rerank 后再补 grounding text，不把 grounding text 混进排序池。
- `fusion`
  - text 与 image 各自独立 rerank。
  - rerank 后按 `article_id` 合并去重。
  - 同一 article 的 text 与 image 证据被聚合为一个 article-level evidence package。
  - package 内保留多条 text chunk 和多张 image hit，不在 retrieval 层硬编码最终出图数量。
- text candidate 的 rerank 输入使用 text `canonical_text`。
- image candidate 的 rerank 输入使用 image `canonical_text`。
- rerank 模型厂商、top-k、阈值是配置项，不是 collection schema。
- 如果未来替换成多模态 reranker，不得改变本文定义的 truth layer、planner 边界和 merge 语义。

`fusion` 的 article-level merge 契约固定如下：

- 去重主键是 `article_id`。
- 同一 article 下：
  - 保留排序最高的 text chunks 作为文本证据
  - 保留排序最高的 image hits 作为视觉证据
  - 记录各自分数与 merge metadata
- retrieval 层不尝试把多个 article 再聚成 story/topic 级 bundle。

### 6.5 Retrieval 输出契约

传给 RAG agent 的 retrieval result 最小字段固定如下：

- `query_plan`
- `text_results`
- `image_results`
- `packages`
- `citation_locators`

其中各计划的结构要求如下：

- `text_only`
  - 返回 text results 列表。
- `image_only`
  - 返回 image results 列表。
  - 每个 image result 必须附带 grounding text references。
- `fusion`
  - 返回按 `article_id` 合并后的 packages 列表。
  - 每个 package 至少包含：
    - `article_id`
    - `text_unit_ids`
    - `image_unit_ids`
    - `text_rank_score`
    - `image_rank_score`
    - `merge_metadata`

单条证据最小字段仍需支持：

- `retrieval_unit_id`
- `modality`
- `article_id`
- `article_image_id`
- `score`
- `index_version`
- `citation_locator`
- `canonical_text`

RAG agent 必须再回源读取 article Markdown 或 `article_image`，不能直接把 Milvus 命中行当最终引用内容。

## 7. 当前代码现状与差距

截至 2026-03-16，仓库当前已落地的部分：

- article 采集、Markdown 落地、图片占位与 `article_image` 存储
- `ArticleEnrichmentService`
- `EmbeddingService` 的 dense embedding
- `ArticleClusterService`
- `StoryGenerationService`
- `DailyPipelineService`

当前尚未落地、但本文已固定为目标规范的部分：

- `retrieval_unit_ref` ORM 与持久化服务
- text collection 与 image collection 的 Milvus 副本同步
- sparse embedding 生成链路
- text/image hybrid recall
- `query planner`
- intent-driven rerank / merge / dedupe
- 图片 dense embedding 与 image collection 写入
- retrieval query service 与 RAG chat consumption

因此，本文是“目标 collection 规范”，不是“当前代码说明书”。

## 8. 非目标与可调参数

本文不冻结以下内容：

- 具体 dense / sparse / rerank 模型厂商与模型名
- top-k、score threshold、overlap 数值
- query 重写 prompt
- 最终 answer agent 的提示词和回答模板

本文只冻结下面这些边界：

- truth source 在哪里
- `retrieval_unit_ref` 承担什么职责
- text 与 image 是否分库
- text chunking 的结构策略
- planner 驱动的 retrieval 路由与 lane 级 hybrid recall / rerank
- 回源引用必须如何做
