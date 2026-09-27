# MF-U10 逐文件运行依赖审计

基线：0d8ad81。对照：MF-U04 只读取证；未取出的未改动依赖仅用 37f9507 作历史参考。LF 规范化 SHA256 见 runtime-audit.json。prepare/build/deploy 均在 MF-U04 镜像核验这些参考，发现差异即停止，须补取实际文件后复审，不能自动覆盖。

运行模块集合来自组装后端真实启动及请求的导入跟踪，另含迁移、启动配置和已删除的原件模块；未改动的依赖不重复覆盖。

| 文件 | 线上版本来源 | 差异/功能归属 | 本包覆盖 | 线上缺失依赖 |
|---|---|---|---|---|
| `backend/alembic/versions/0042_structured_sync_changes.py` | 未上线/不存在 | 新增全局变化序列、触发器、索引及安全降级 | 是 | 无新增 |
| `backend/app/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/account_core_mvp.py` | 取证文件 | 装配目录接口；移除原件存储装配 | 是 | app.enterprise.catalog_api（本包提供）；app.enterprise_tenancy.account_object_storage（已去除） |
| `backend/app/account_identity_contract.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/core_errors.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/budget.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/adapters.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/benchmark.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/contracts.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/control_center.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/control_center_delivery.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/delivery.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/demo.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/experiments.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/identity_policy.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/integrity_benchmark.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/invalidation.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/metrics.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/opportunity.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/optimization.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/persistence.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/product_metrics.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/profiles.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/cache_observatory/runtime_ports.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/calculator.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/contracts.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/durable_execution/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/durable_execution/audit.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/durable_execution/contracts.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/durable_execution/provider_runtime_v2.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/durable_execution/runtime.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/durable_execution/serialization.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/durable_execution/store.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/estimation.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/execution_v2/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/execution_v2/contracts.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/execution_v2/guard.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/execution_v2/ports.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/execution_v2/session.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/execution_v2/trace.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/ledger.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/cost/runtime_budget.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/credential_store.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/document_import_integration.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/account_workspaces.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/catalog_api.py` | 未上线/不存在 | 新增只读目录 API | 是 | 无新增 |
| `backend/app/enterprise/contracts_v1/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/contracts_v1/manifest.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/contracts_v1/models.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/account_lifecycle.py` | 取证文件 | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/admin_overview.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/application_roles.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/config.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/context.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/continuity.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/directory.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/issuer_migration.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/keycloak.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/lifecycle_api.py` | 取证文件 | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/models.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/native.py` | 取证文件 | 单设备登录及 session 状态接口 | 是 | 无新增 |
| `backend/app/enterprise/identity/oidc.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/reset_completion.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/runtime.py` | 取证文件 | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/identity/service_account.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/jobs_v1/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/jobs_v1/learning_runs.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/jobs_v1/models.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/jobs_v1/ports.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/jobs_v1/procrastinate_adapter.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/jobs_v1/redaction.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/jobs_v1/repository.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/jobs_v1/retry.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/jobs_v1/schema.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/jobs_v1/service.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/product_bridge.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise/structured_sync_api.py` | 取证文件 | 增量 /changes；移除原件对象路由 | 是 | refactor.server.account_object_storage（已去除） |
| `backend/app/enterprise_tenancy/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/account_object_storage.py` | 未上线/不存在 | 移除未上线原件存储模块 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/adapters.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/asset_manifests.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/cache_boundary.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/context.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/document_import.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/factory.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/import_ledger_schema.py` | 取证文件 | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/import_seam.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/inventory.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/isolation_corpus.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/job_context.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/models.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/object_reference.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/plan_task_mutation.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/ports.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/rehydration_dispatcher.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/rehydration_manifest.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/repository.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/schema.py` | 取证文件 | 数据库准入版本更新为 0042 | 是 | 无新增 |
| `backend/app/enterprise_tenancy/structured_sync.py` | 取证文件 | refactor-sync 信封、course_record、增量读取 | 是 | 无新增 |
| `backend/app/enterprise_tenancy/sync_registry.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/sync_schema.py` | 取证文件 | 精确验证 0042 列、序列、触发器和索引；保留原隔离校验 | 是 | 无新增 |
| `backend/app/enterprise_tenancy/sync_snapshot.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/teaching.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/unit_of_work.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/workspace_bootstrap.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/enterprise_tenancy/workspaces.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/knowledge_policy.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/model_routing/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/model_routing/capabilities.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/model_routing/identity.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/model_routing/orchestrator.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/model_routing/registry.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/model_routing/requirements.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/model_routing/router.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/observability/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/observability/api.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/observability/audit.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/observability/context.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/observability/health.py` | 取证文件 | 还原线上版本：原件存储不是 Account Core 必需依赖 | 否 | 无新增 |
| `backend/app/observability/instrumentation.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/observability/metrics.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/observability/privacy.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/observability/recovery.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/observability/runtime.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/observability/slo.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/orm.py` | 取证文件 | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/effort/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/effort/contracts.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/effort/estimator.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/product_runtime/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/product_runtime/contracts.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/product_runtime/creation.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/product_runtime/feasibility.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/product_runtime/publication.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/review_effort/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/review_effort/calibration.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/review_effort/contracts.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/review_effort/estimator.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/review_effort/schedule_bridge.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/runtime_v2/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/runtime_v2/contracts.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/runtime_v2/progress.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/runtime_v2/runtime.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/runtime_v2/shift.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/scheduling/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/scheduling/availability.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/scheduling/benchmark.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/scheduling/contracts.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/scheduling/replan.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/scheduling/solver.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/scheduling/validation.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/planning/scheduling/validator.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/product_sync_contract.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/product_sync_extensions.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/provider/runtime_v2/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/provider/runtime_v2/adapters.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/provider/runtime_v2/coordinator.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/provider/runtime_v2/models.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/provider/runtime_v2/registry.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/provider_runtime.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/provider_service.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/provider_session_repository.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/provider_url_policy.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/providers/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/providers/pricing.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/providers/pricing_deepseek.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/providers/telemetry.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/runtime_resources.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/schemas.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/contracts.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/evidence.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/application.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/benchmark.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/bridge.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/checkpoint.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/contracts.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/corpus.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/execution.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/exercises.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/import_api.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/pool.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/provenance.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/quality.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/review.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/ingestion/runtime.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/preflight.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/sources/document/source.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/teaching/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/teaching/contracts.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/teaching/evaluator_v2/__init__.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/teaching/evaluator_v2/engine.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/teaching/evaluator_v2/models.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/teaching/evaluator_v2/ports.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/teaching/pedagogy.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/app/upload_limits.py` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `backend/config/knowledge_policy_v1.json` | 37f9507 参考（镜像内核验） | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `refactor/server/__init__.py` | 取证文件 | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `refactor/server/account_object_storage.py` | 未上线/不存在 | 移除未上线原件存储模块 | 否 | 无新增 |
| `refactor/server/closure.py` | 取证文件 | 还原线上版本，去除未上线对象存储清理 | 否 | 无新增 |
| `refactor/server/closure_api.py` | 取证文件 | 还原线上版本，去除未上线对象存储装配 | 否 | 无新增 |
| `refactor/server/closure_provider.py` | 取证文件 | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `refactor/server/closure_store.py` | 取证文件 | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `refactor/server/password_sessions.py` | 取证文件 | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
| `refactor/server/sql/closure_v1.sql` | 取证文件 | 沿用既有线上逻辑，无功能差异 | 否 | 无新增 |
