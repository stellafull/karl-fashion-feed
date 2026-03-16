# 运维运行手册

## 1. 文档目的

本文档描述重构后 Fashion Feed 的目标运行方式，面向内部运维和工程同学。

## 2. 运行服务

规划中的核心服务：

- 前端静态部署
- FastAPI 服务
- Celery Worker
- Redis
- PostgreSQL
- Milvus

目录约定：

- `backend/app/`：FastAPI 服务主目录
- `backend/scripts/`：迁移期采集脚本目录
- `backend/test/`：后端统一测试目录
- `backend/server/`：遗留 Node 托管层

## 3. 配置项分类

必需配置包括：

- Feishu 认证凭证
- 允许访问的 tenant keys
- PostgreSQL 连接信息
- Redis 连接信息
- Milvus 连接信息
- 模型供应商配置
- embedding 供应商配置
- source 配置路径

## 4. 定时任务

### 每日重聚类

- 时间：`08:00`
- 任务名：`daily_recluster`
- 范围：最近 72 小时 active window
- 预期输出：
  - 完整 `pipeline_run`
  - 更新后的 story snapshots
  - 可选新的 `published_run`

### 日间增量更新

- 时间：`10:00` 到 `18:00` 每 2 小时
- 任务名：`incremental_update`
- 预期输出：
  - 新增 documents
  - 更新后的 retrieval units
  - 刷新的 feed 发布结果

## 5. 发布流程

1. 创建 `pipeline_run`
2. 执行采集、清洗、聚类、发布步骤
3. 校验输出数量与结构完整性
4. 切换 `published_run`
5. 记录发布信息

禁止直接覆盖当前线上结果。

## 6. 回滚流程

如果某次发布结果异常：

1. 找到上一个成功的 `published_run`
2. 将系统指回该 run
3. 验证首页与 topic 接口
4. 排查失败 run 后再重新发布

## 7. 日常巡检项

重点关注：

- 登录失败数和 tenant 拒绝数
- source 抓取失败
- document 解析失败
- Milvus 写入失败
- embedding 延迟
- story 数量异常
- citation 生成失败

## 8. 标准命令

当前约定的基础命令：

- 启动前端开发环境：`pnpm --dir frontend dev`
- 运行前端类型检查：`pnpm --dir frontend check`
- 构建前端静态资源：`pnpm --dir frontend build`
- 启动 API：`uvicorn backend.app.main:app --reload`
- 运行后端测试：`python -m unittest discover -s backend/test`
- 手动执行采集脚本：`python backend/scripts/fetch_feeds.py`

待 Celery 与发布任务代码落地后，再补充以下命令：

- 启动 Celery worker
- 手动执行日重聚类
- 手动执行增量更新
- 重建 embedding
- 回滚或重发某个 run

## 9. 典型故障场景

### 登录异常

检查：

- Feishu 凭证
- tenant allowlist
- callback URL
- 登录审计表

### 首页为空或异常

检查：

- `published_run`
- 最近成功的 `pipeline_run`
- story snapshot 写入数量
- API 返回结构

### AI 回答质量异常

检查：

- retrieval 结果是否相关
- citation 是否正确落库
- 模型服务是否健康
- session scope 是否正确传入

### Memory 行为异常

检查：

- `user_memory_record` 写入链路
- Milvus memory upsert
- 检索参数是否正确

## 10. 文档维护要求

只要运行服务、定时任务或发布流程发生变化，必须同时更新：

- 本文档
- `docs/architecture.md`
- `docs/data-model.md`
