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
