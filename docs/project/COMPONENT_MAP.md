# Component map

所有路径相对 repository；表中的测试是导航入口，不是本轮执行结果。
先看 [ARCHITECTURE](../../ARCHITECTURE.md) 和 [CURRENT_STATE](CURRENT_STATE.md)。

| 路径 / subsystem | 职责与主入口 | 关联测试 | 深层合同 |
| --- | --- | --- | --- |
| [desktop](../../desktop/) | `main.py` 原生 shell；`login_shell.py`；`gui_update_runtime.py` | [desktop/tests](../../desktop/tests/) 的 prelogin、E109 binding、account 系列 | [E109 code map](../E109_CODE_LEARNING_MAP.md) |
| [desktop/update_lifecycle.py](../../desktop/update_lifecycle.py) | `CoreUpdatePort`，UI 到 core 唯一 adapter | [integration binding](../../desktop/tests/test_e109_integration_binding.py) | [E109 integration](../E109_INTEGRATION_MAP.md) |
| [scripts/per_user_update_core.py](../../scripts/per_user_update_core.py) | startup classification、repair；install/launcher 提供稳定 runtime | [scripts](../../scripts/) 中 per_user/update 测试 | [核心接口](../PER_USER_CORE_INTERFACES_V1.md)、[trust](../PER_USER_INSTALL_TRUST.md) |
| [installer/inno](../../installer/inno/) | `MentorFlow.iss`、`per-user-core.iss`、完成页和目录交互 | [Inno tests](../../installer/inno/tests/) | [installer README](../../installer/inno/README.md) |
| [identity](../../backend/app/enterprise/identity/) | OIDC/JWKS、principal/membership、issuer migration | [issuer migration](../../backend/tests/test_e109_issuer_migration.py)、[desktop OIDC](../../desktop/tests/test_enterprise_oidc.py) | [E109 auth](../E109_AUTH_ISSUER_MIGRATION.md) |
| [account_core_mvp.py](../../backend/app/account_core_mvp.py) | Account Core API；desktop `account_identity` / `account_vault` 消费身份 | [workspace bootstrap](../../backend/tests/test_account_workspace_bootstrap.py)、[account vault](../../desktop/tests/test_account_vault.py) | [workspace contract](../account-core-workspace-bootstrap-v1.md) |
| [backend/app/main.py](../../backend/app/main.py) | FastAPI 全量学习 API/lifespan；不同于 Account Core MVP | [API startup](../../backend/tests/test_api_contract_startup.py)、[backend/tests](../../backend/tests/) | [product flow](../PRODUCT_FLOW.md) |
| [knowledge](../../backend/app/knowledge/)、[sources](../../backend/app/sources/) | 资料、解析/证据、知识地图；`provider_runtime` 与 grounding 在 app | [backend/tests](../../backend/tests/) knowledge/document/grounding 系列 | [知识地图合同](../knowledge-map-teacher-v1.md) |
| [teaching](../../backend/app/teaching/)、[planning](../../backend/app/planning/) | 教师状态、学习计划、执行与复习 | [backend/tests](../../backend/tests/) teaching/plan/review 系列 | [安全引用](../SAFETY_AND_GROUNDING.md) |
| [enterprise/jobs_v1](../../backend/app/enterprise/jobs_v1/) | runtime、Procrastinate adapter、production factory、product handlers | [backend/tests](../../backend/tests/) enterprise jobs/worker 系列 | [tenant persistence](../enterprise/tenant-persistence-integration.md) |
| [orm.py](../../backend/app/orm.py)、[Alembic](../../backend/alembic/) | SQLite/PostgreSQL 选择与规范化模型/迁移 | [account runtime](../../backend/tests/test_account_core_runtime_truth.py) | [migration strategy](../enterprise/migration-strategy.md) |
| [enterprise_tenancy](../../backend/app/enterprise_tenancy/) | scoped repository/UoW、RLS/partition/ownership | [backend/tests](../../backend/tests/) enterprise tenancy/RLS 系列 | [tenant isolation](../enterprise/tenant-isolation.md) |
| [contracts_v1](../../backend/app/enterprise/contracts_v1/) | 共享 Actor/Tenant/Scope/Audit 类型 | [contracts README](../../backend/app/enterprise/contracts_v1/README.md) | [identity-tenancy](../enterprise/identity-tenancy.md) |
| [governance](../../backend/app/governance/)、[provider](../../backend/app/provider/) | secret access、权限与 Provider 边界 | [secret navigation](../../backend/tests/test_enterprise_secret_navigation.py) | [secret governance](../enterprise/provider-secret-governance.md) |
| [observability](../../backend/app/observability/)、[ops](../../ops/observability/) | health/trace/metrics/audit/recovery，外部采集配置 | [backend/tests](../../backend/tests/) observability 系列 | [observability boundary](../enterprise/E1-06_OBSERVABILITY_RECOVERY.md) |
| [frontend](../../frontend/) | React/TS/Vite 状态、页面、复杂 Web 表面 | [frontend/src/test](../../frontend/src/test/) | [native desktop](../native-desktop.md)、[teacher](../teacher-2d-runtime.md) |
| [release](../../release/)、[scripts](../../scripts/) | build input、签名对象、发布白名单与 candidate QA | [final input checks](../../scripts/test_e108_final_feed_inputs.py) | [release contract](../windows-update-release-contract-v1.md) |
| [deploy](../../deploy/)、[enterprise_e1](../../enterprise_e1/) | 部署/门禁配置与历史 authority/lease 快照 | [auth offline policy](../../deploy/enterprise/auth-email-security/test_policy_contract.py) | [staging](../enterprise/staging-orchestrator.md) |
| [docs/governance](../governance/)、[scripts/context](../../scripts/context/) | 调度快照、compact handoff 与 lint | `check_repo_knowledge.py`；context 自检 | [handoff](../governance/HANDOFF_CONTRACT.md) |

## 学习业务链到代码 / 测试

以下是阅读导航，测试存在不等于本轮运行或产品验收通过；长期要求在
[PRODUCT_FLOW](../PRODUCT_FLOW.md)，选择理由在 [ADR index](../decisions/index.md)。

| 业务阶段 | 代码入口 | 回归证据入口 |
| --- | --- | --- |
| Materials → Evidence | [ingestion](../../backend/app/sources/document/ingestion/)、[pipeline](../../backend/app/knowledge/pipeline_v2/) | [pipeline tests](../../backend/tests/test_knowledge_pipeline_v2.py) |
| Inventory / Anchor-Support → Map | [inventory](../../backend/app/knowledge/inventory/)、[scope](../../backend/app/knowledge_map_scope.py) | [inventory](../../backend/tests/test_knowledge_inventory.py)、[anchor](../../backend/tests/test_knowledge_map_scope_anchor.py) |
| Plan → Teacher | [plan creation](../../backend/app/planning/product_runtime/creation.py)、[teaching](../../backend/app/teaching/) | [plan runtime](../../backend/tests/test_plan_product_runtime_v1.py) |
| Assessment → Student Model | [assessment](../../backend/app/teaching/assessment_v2/)、[student model](../../backend/app/teaching/student_model.py) | [assessment](../../backend/tests/test_assessment_session_v2.py)、[student model](../../backend/tests/test_student_model.py) |
| Active Recall / Spaced Review | [review](../../backend/app/teaching/review_v1/)、[review memory](../../backend/app/teaching/review_memory/) | [review sessions](../../backend/tests/test_review_session_v1.py)、[schedule](../../backend/tests/test_review_schedule_integration_v2.py) |
| Progress / Replan | [progress](../../backend/app/teaching/progress_read/)、[replan](../../backend/app/planning/scheduling/replan.py) | [progress](../../backend/tests/test_progress_read.py)、[replan](../../backend/tests/test_constraint_scheduler_replan.py) |
| Free Chat 边界 | [free chat](../../backend/app/tools/free_chat/) | [state isolation](../../backend/tests/test_free_chat_service.py) |

历史 `reports`/`artifacts` 只按专项合同中的 exact pointer 读取；不要全量装入上下文。
`data`、用户资料库、credentials、模型缓存与实际运行日志不是知识库输入。

## 当前 structured rehydration 导航

| 层级 | 正式路径 / 状态 | 生产者 → 消费者 | store / 证据 |
| --- | --- | --- | --- |
| 正式产品 | `enterprise_tenancy/learning_run_history_sync.py`、`profile_settings_sync.py` | ORM/domain rows → adapter/dispatcher（当前仍 partial） | LearningRunORM、LearningRunMessageORM；adapter tests |
| 正式产品 | `enterprise_tenancy/rehydration_dispatcher.py`、`profile_settings_store.py` | authenticated snapshot → Device-B stores | local profile/settings store；focused tests |
| 兼容路径 | `active_teaching.py`、`active_plan_tasks` | legacy runtime projection | 不拥有 canonical business truth；PlanTask seam 未完成 |
| 正式基础设施 | `structured_sync.py`、`sync_snapshot.py`、`sync_registry.py` | scoped producer → server projection | `structured_sync_entities` 是 projection，不是业务 authority |
| 独立 fixture | `backend/tests/`、`scripts/` qualification helpers | synthetic/test inputs → tests | 不等同真实产品验收 |
| PoC | PowerSync Node/.NET/VPS material | evaluated future SyncEnginePort | deferred；不是当前 runtime dependency |

## Batch02 integration seams

正常登录：[native form/client](../../desktop/native_auth.py) →
[backend verifier/session](../../backend/app/enterprise/identity/native.py) →
[Keycloak SPI/action tokens](../../deploy/enterprise/native-identity-verifier/README.md)。
[native tests](../../desktop/tests/test_native_login.py)、[backend tests](../../backend/tests/test_native_auth.py)
与 [真实本地 fixture](../../scripts/native_identity_fixture.py) 对应
[native auth ADR](../ADR_NATIVE_CREDENTIAL_AUTH_V1.md) 的身份、session 和 bootstrap 边界。

[Batch02 组合测试](../../desktop/tests/test_main3_batch02_integration.py) 把真实签名 floor、
core、adapter、reason UI 和 native login gate 接在一起，覆盖既知 floor 的断网/取消拒绝、
latest 目标、optional 可继续及修复后原生表单恢复。更新检查与应用使用 inert signed bytes。
[native edge tests](../../backend/tests/test_e109_security_native_stage2.py) 运行本地 nginx 编码/限流检查；
[RLS harness](../../scripts/e109_native_lab/postgres_checks.py) 的独立原生 PostgreSQL 证据保留原执行者范围。
exact 11-commit 映射、本轮检查与保留的独立证据见
[Batch02 receipt](../governance/receipts/20260913-main3-batch02-integration.json)。

## Batch01 integration seams（历史）

[组合回归](../../desktop/tests/test_main3_batch01_integration.py) 连接 01 的 System Browser
与 04 capability boundary，以及 02 signed recovery、03 adapter 和 login update gate。
其浏览器正常 UX 已被 Batch02 native authority supersede；协议检查保留 legacy 范围。
其 synthetic OIDC、inert signed files 与 offscreen Qt 不代表 live/native 验收。
具体执行计数、exact commit/tree 映射和未运行门禁见
[batch01 receipt](../governance/receipts/20260912-main3-batch01-integration.json)。
