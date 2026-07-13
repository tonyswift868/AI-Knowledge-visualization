# MentorFlow 架构

## 运行层

FastAPI 使用 lifespan 初始化兼容 SQLite 与 SQLAlchemy 2.x ORM。`backend/app/main.py` 保留 API 编排；核心能力拆在 `core_security`、`ingestion_secure`、`url_fetcher_secure`、`retrieval_service`、`provider_service`、`provider_runtime`、`grounding_service`、`job_service`、`prompt_loader`。`provider_runtime` 只在进程内保存短 TTL ProviderSession，真实 Workspace 的生成入口统一要求 verified session。

## 数据层

`backend/app/orm.py` 定义 Workspace（含 execution_mode/provider identity）、Goal、Source/Chunk、Feasibility、Plan/Milestone/Module/Lesson/Block、Day/Task、Progress/Mastery/Mistake/Review、Session/Message/Citation、Conflict、Character、Job。ProviderSession 不入数据库。外键、cascade、索引和唯一 hash 约束用于核心关系；旧 JSON SQLite 表暂作兼容读写源，ORM 为新增规范化索引和状态事实源。

迁移入口：`backend/alembic`，执行 `scripts/migrate.ps1` 或 `alembic upgrade head`。

## 前端层

`frontend/src/api` 负责请求，`stores` 负责 Zustand 状态，`components` 负责 Citation/Grounding/Conflict，`styles` 负责组件样式，`test` 为真实 Vitest 测试；`App.tsx` 编排向导和页面，Vite 将 `/api` 代理到 8000。

## 数据流

```text
ProviderSession -> Verified Provider -> generation_meta
Import -> Parse -> Chunk/Hash -> BM25 Retrieval -> Grounding Critic
Goal -> Feasibility -> Draft Plan -> User Activation -> PlanTask
Tutor -> Query Retrieval -> Citation Validation -> Session/Message
Evaluation -> MasteryEvent/Mistake/Review -> Progress -> Replan Job
```
