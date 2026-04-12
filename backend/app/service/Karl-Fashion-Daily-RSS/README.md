# world-fashion-daily Workspace Package

这是仓库内的一个独立 workspace package，不是 KARL Fashion Feed 当前主应用的入口。

它当前主要用于：

- 作为 `world-fashion-daily` workspace dependency 被主后端引用
- 承载一套独立的 deep-agents / LangGraph 相关实验与封装
- 维护它自己的 `tests/`、`pyproject.toml` 和包内运行配置

请不要把这个目录理解为：

- 当前产品的 RSS GitHub workflow 主链路
- 当前前后端应用的登录或阅读入口
- 当前 digest runtime 的唯一运行面

当前主应用请看：

- 项目总览: [README_PROJECT.md](/home/czy/karl-fashion-feed/README_PROJECT.md)
- 后端说明: [backend/README.md](/home/czy/karl-fashion-feed/backend/README.md)
- 架构文档: [docs/architecture.md](/home/czy/karl-fashion-feed/docs/architecture.md)

如果你要运行主产品，请使用 `backend/app/` 下的 FastAPI、Celery 和 runtime scripts，而不是把这里当作主入口。
