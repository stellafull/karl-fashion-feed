# Fashion Feed — 时尚资讯聚合

一个基于 RSS 聚合 + AI 翻译摘要的时尚资讯聚合平台，灵感来自 [Perplexity Discover](https://www.perplexity.ai/discover)。

## 架构概述

```
┌─────────────────────────────────────────────────────┐
│                  GitHub Actions                      │
│              (每2小时自动触发)                         │
│                                                      │
│  ┌─────────┐    ┌──────────┐    ┌───────────────┐   │
│  │ RSS源    │───▶│ 去重/聚合 │───▶│ LLM翻译/分类  │   │
│  │ 16个源   │    │          │    │ OpenRouter API│   │
│  └─────────┘    └──────────┘    └───────┬───────┘   │
│                                          │           │
│                                 ┌────────▼────────┐  │
│                                 │ feed-data.json   │  │
│                                 │ (提交到仓库)      │  │
│                                 └────────┬────────┘  │
└──────────────────────────────────────────┼──────────┘
                                           │
                              ┌─────────────▼──────────────┐
                              │     前端 (React + Vite)      │
                              │  从 feed-data.json 读取数据   │
                              │  类 Perplexity Discover UI    │
                              └──────────────────────────────┘
```

## 功能特性

- **多源聚合**: 聚合 Vogue、WWD、BOF、GQ、Highsnobiety 等 16 个全球时尚媒体 RSS 源
- **AI 翻译**: 通过 OpenRouter API (Gemini Flash) 将英文/日文资讯自动翻译为中文
- **智能分类**: AI 自动将文章分为高端时装、潮流街头、行业动态、男装风尚、先锋文化五大类
- **新闻聚合**: 识别并合并报道同一事件的多篇文章，类似 Perplexity 的新闻聚合
- **内容过滤**: 自动识别并过滤政治敏感内容
- **定时更新**: GitHub Actions 每2小时自动拉取最新资讯
- **优雅前端**: Editorial Noir 设计风格，响应式布局

## RSS 信息源

| 来源 | 语言 | 分类 |
|------|------|------|
| Vogue | EN | 高端时装 |
| Vogue Fashion | EN | 高端时装 |
| WWD | EN | 行业动态 |
| Hypebeast | EN | 潮流街头 |
| Highsnobiety | EN | 潮流街头 |
| GQ | EN | 男装风尚 |
| Elle | EN | 高端时装 |
| Fashionista | EN | 行业动态 |
| BOF | EN | 行业动态 |
| Dazed | EN | 先锋文化 |
| i-D | EN | 先锋文化 |
| Harper's Bazaar | EN | 高端时装 |
| Fashion Dive | EN | 行业动态 |
| WWD Japan | JA | 行业动态 |
| Vogue Japan | JA | 高端时装 |

## 本地开发

### 前端

```bash
cd fashion-feed
pnpm install
pnpm dev
```

### 运行 RSS 聚合脚本

```bash
# 安装 Python 依赖
pip install requests feedparser beautifulsoup4

# 设置 API Key
export OPENROUTER_API_KEY="your-key-here"

# 运行
python scripts/fetch_feeds.py
```

## GitHub Actions 部署

1. Fork 本仓库
2. 在仓库 Settings → Secrets and variables → Actions 中添加:
   - **Secret**: `OPENROUTER_API_KEY` — 你的 OpenRouter API Key
   - **Variable** (可选): `LLM_MODEL` — 默认为 `google/gemini-2.0-flash-001`
3. Actions 会每2小时自动运行，也可手动触发

## 设计参考

- 前端设计灵感: [Perplexity Discover](https://www.perplexity.ai/discover)
- 后端架构参考: [RSSbrew](https://github.com/yinan-c/RSSbrew), [rss-gpt](https://github.com/thamore/rss-gpt)
