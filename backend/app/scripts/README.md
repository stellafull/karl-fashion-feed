# Scripts

## 入口

- `backend/app/scripts/validate_sources.py`
  source 配置校验入口
- `backend/app/scripts/collect_articles.py`
  只采集 article seed
- `backend/app/scripts/parse_articles.py`
  只解析 pending/failed article
- `backend/app/scripts/ingest_articles.py`
  增量采集 + parse
- `backend/app/scripts/run_daily_pipeline.py`
  日更 story pipeline 入口
- `backend/app/scripts/run_scheduler.py`
  每天北京时间 8 点触发一次 `DailyPipelineService`
- `backend/app/scripts/init_article_data.py`
  初始化或回填近一段时间的 `article`
- `backend/app/scripts/dev_ingest_parse_today.py`
  dev 专用增量脚本，只 parse 本次新插入 article，不跑 enrichment / cluster
- `backend/app/scripts/dev_ingest_story_rag_today.py`
  dev 专用全链路脚本，跑 enrichment、image analysis、story draft、RAG 导入
- `backend/app/scripts/rebuild_rag_collection.py`
  从 Postgres + Markdown 真相源全量重建 `kff_retrieval`

## 常用命令

校验 `sources.yaml`：

```bash
python backend/app/scripts/validate_sources.py
```

只执行 seed 采集：

```bash
python backend/app/scripts/collect_articles.py
```

只解析 pending/failed article：

```bash
python backend/app/scripts/parse_articles.py
```

执行增量采集入库：

```bash
python backend/app/scripts/ingest_articles.py
```

dev 模式只做本次增量采集 + parse，不扫历史 pending backlog：

```bash
python backend/app/scripts/dev_ingest_parse_today.py
```

只跑指定来源：

```bash
python backend/app/scripts/dev_ingest_parse_today.py --source Vogue --source WWD
```

执行完整日更 pipeline：

```bash
python backend/app/scripts/run_daily_pipeline.py
```

执行每天北京时间 8 点触发的 scheduler：

```bash
python backend/app/scripts/run_scheduler.py
```

只处理已入库 article，不重新采集：

```bash
python backend/app/scripts/run_daily_pipeline.py --skip-ingest
```

执行一次初始化 story 聚合，按 `published_at` 的日期分组：

```bash
python backend/app/scripts/bootstrap_story_pipeline.py
```

如果 article 已经全量入库，只做 bootstrap story 聚合：

```bash
python backend/app/scripts/bootstrap_story_pipeline.py --skip-ingest
```

只跑某一天的 bootstrap story 聚合：

```bash
python backend/app/scripts/bootstrap_story_pipeline.py --skip-ingest --story-date 2026-03-16
```

只跑指定来源：

```bash
python backend/app/scripts/ingest_articles.py --source Vogue --source WWD
```

控制增量采集并发：

```bash
python backend/app/scripts/ingest_articles.py --source-concurrency 4 --http-concurrency 16
```

执行 dev 全链路联调：

```bash
python backend/app/scripts/dev_ingest_story_rag_today.py
```

只跑指定来源的 dev 全链路联调：

```bash
python backend/app/scripts/dev_ingest_story_rag_today.py --source Vogue --source WWD
```

执行 dev 检索联调脚本（验证 `RagTools` 4 条路径）：

```bash
python backend/app/scripts/dev_query_retrieval.py
```

重建 shared retrieval collection：

```bash
python backend/app/scripts/rebuild_rag_collection.py
```

## 初始化回填

执行近 30 天初始化：

```bash
python -u backend/app/scripts/init_article_data.py --days-back 30
```

控制初始化范围：

```bash
python -u backend/app/scripts/init_article_data.py \
  --days-back 30 \
  --limit-sources 5 \
  --max-articles-per-source 50 \
  --max-pages-per-source 2 \
  --request-timeout-seconds 5 \
  --source-concurrency 4 \
  --http-concurrency 16
```

只跑指定来源：

```bash
python -u backend/app/scripts/init_article_data.py \
  --days-back 30 \
  --source Vogue \
  --source WWD
```

## 参数说明

- `--days-back`
  回填最近多少天
- `--limit-sources`
  限制本次处理多少个 source
- `--max-articles-per-source`
  覆盖单源最大抓取文章数
- `--max-pages-per-source`
  覆盖网页源最大翻页数
- `--request-timeout-seconds`
  单次 HTTP 请求超时
- `--source-concurrency`
  source 级 worker 数
- `--http-concurrency`
  全局 HTTP 并发上限
