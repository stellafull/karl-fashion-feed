# Backend

## 目标

为 KARL FASHION FEED 提供一条稳定的后端生产链路：

- 多语言来源采集
- 中文翻译与摘要
- 过滤广告和无关内容
- 生成面向读者的不可变 `story`
- 为后续 RAG 和引用提供可追溯的文章真相源

## 核心不变量

- `article` 是事实真相源。
- `canonical_url` 归一化后是文章唯一键。
- 同一 `canonical_url` 再次出现时按重复处理，不做补写。
- `story` 是不可变读模型，`story_key` 使用 UUID。
- 每天北京时间 8 点只处理新增 `article`，生成新的 `story`。
- 旧 `story` 不更新、不合并、不重写。
- `article` / `retrieval_unit_ref` 是检索与引用真相源。
- Milvus 只是检索副本。
- Redis 只负责缓存、锁、短期会话态。

## 每日 Pipeline

1. `article_collection_service`
   抓取来源文章，清洗字段，归一化 `canonical_url`，完成去重入库。

2. `article_summarization_service`
   对每篇 `article` 执行一次 LLM enrichment，产出：
   `should_publish`、`reject_reason`、中文标题、中文摘要、标签、品牌、分类建议、全文翻译

3. `embedding_service`
   仅对 `should_publish=true` 的文章生成 embedding。
   embedding 输入建议使用中文标题、中文摘要、标签、品牌等结构化拼接文本。

4. `article_cluster_service`
   基于 embedding 做语义聚类，输出 story 候选簇。

5. `article_cluster_service` + LLM 复核
   对候选簇做复核，必要时拆分，再生成最终 `story` 标题、摘要、要点、标签和分类。

6. 持久化
   写入 `story`、`story_article`、`retrieval_unit_ref`，记录本次 `pipeline_run`。

## `sources.yaml` 格式

当前只支持两类来源：

- `type: rss`
  必填字段：`name`、`feed_url`、`lang`、`category`
- `type: web`
  必填字段：`name`、`start_urls`、`allowed_domains`、`discovery`

统一约定：

- 所有来源都必须显式声明 `type`
- RSS 用 `feed_url`
- 网页源用 `start_urls + discovery`
- `max_articles` 控制单源单次最多抓取文章数
- `enabled: false` 的来源会被跳过

网页源的 `detail` 选择器是可选的。
未配置时，采集器会使用通用正文提取策略。

## 推荐数据模型

### `article`

- `article_id`
- `canonical_url`
- `source`
- `title_raw`
- `content_raw`
- `lang`
- `published_at`
- `ingested_at`
- `image_url`
- `should_publish`
- `reject_reason`
- `title_zh`
- `summary_zh`
- `tags_json`
- `brands_json`
- `category_candidates_json`
- `cluster_text`

### `story`

- `story_key`
- `created_run_id`
- `title_zh`
- `summary_zh`
- `key_points_json`
- `tags_json`
- `category`
- `hero_image_url`
- `source_article_count`
- `created_at`

### `story_article`

- `story_key`
- `article_id`
- `rank`

### `retrieval_unit_ref`

- `retrieval_unit_id`
- `article_id`
- `chunk_index`
- `chunk_text`
- `embedding_ref`

### `pipeline_run`

- `run_id`
- `run_type`
- `status`
- `started_at`
- `finished_at`
- `watermark_ingested_at`
- `error_message`

## 服务职责边界

- `article_collection_service`：采集、清洗、去重、入库
- `article_summarization_service`：翻译、总结、过滤、标签抽取、分类建议
- `embedding_service`：embedding 生成与向量副本同步
- `article_cluster_service`：语义聚类与 story 候选拆分
- `scheduler_service`：编排每日任务和失败恢复
- `rag_agent`：消费 retrieval 结果，不反向改写 `article` / `story`

## 当前实现优先级

1. 打通 `article` 采集与入库
2. 打通 LLM enrichment
3. 打通 immutable `story` 生成
4. 打通 retrieval chunk 与 Milvus 副本
5. 最后接入 chat / RAG

## Scripts

采集脚本和命令用法见 [backend/app/scripts/README.md](/root/karl-fashion-feed/backend/app/scripts/README.md)。
