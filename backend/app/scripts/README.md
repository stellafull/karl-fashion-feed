# Scripts

## 入口

- `backend/main.py`
  日常校验和增量采集入口
- `backend/app/scripts/init_article_data.py`
  初始化或回填近一段时间的 `article`

## 常用命令

校验 `sources.yaml`：

```bash
python backend/main.py validate-sources
```

只执行 seed 采集：

```bash
python backend/main.py collect-articles
```

只解析 pending/failed article：

```bash
python backend/main.py parse-articles
```

执行增量采集入库：

```bash
python backend/main.py ingest-articles
```

执行完整日更 pipeline：

```bash
python backend/main.py run-daily-pipeline
```

只处理已入库 article，不重新采集：

```bash
python backend/main.py run-daily-pipeline --skip-ingest
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
python backend/main.py ingest-articles --source Vogue --source WWD
```

控制增量采集并发：

```bash
python backend/main.py ingest-articles --source-concurrency 4 --http-concurrency 16
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
