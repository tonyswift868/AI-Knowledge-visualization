# MentorFlow architecture map

范围：冻结 MAIN2 基线之上的 MAIN3 Batch02 集成源码阅读地图。
产品与部署的验收状态见 [CURRENT_STATE](docs/project/CURRENT_STATE.md)。
本文件说明已经存在的模块；不据此宣布某个服务器、安装包或学习全链路已经合格。

## 一次用户会话穿过哪些边界

1. Inno 外壳请求稳定 UserRuntime 安装/启动；签名核心决定能否运行 App。
2. Desktop 先接更新策略，再以原生用户名/密码调用 backend；mandatory 同时阻止登录与 main。
3. Backend 经私有 mTLS verifier 调用 Keycloak credential provider，签发 MentorFlow opaque session；workspace bootstrap 与本地服务就绪后才显示 main。
4. 导入材料经过能力判断、解析、证据提取和引用；知识地图候选经确认才激活。
5. 计划预览与激活分离，教师、评估、复习与进度反馈共享工作区证据。
6. 后台任务、数据库和 telemetry 分别保存执行状态、业务事实和脱敏运行信息。

PERSONAL_LOCAL 与 ENTERPRISE_SERVER 是显式模式。开发用完整 API 与 Account Core
MVP 是不同入口，不能因为某一路由存在就断言它已经接入当前账户服务或部署。

## Desktop 与渲染

[desktop/main.py](desktop/main.py) 是 PySide6/Qt 原生应用入口；登录壳、设置、
资料与课堂交互由 desktop 包编排。[frontend](frontend/) 保留 React/TypeScript/Vite
页面与 Web 渲染资源；复杂地图和教师表面不等于另一套安装器或账户 authority。
[教师渲染](docs/teacher-2d-runtime.md) 与 [原生客户端](docs/native-desktop.md) 是专项入口。

Desktop → `gui_update_runtime` → controller → `CoreUpdatePort` → 已交付更新核心。
唯一 adapter 边界在 [update_lifecycle.py](desktop/update_lifecycle.py)。
完整入口表在 [E109 code map](docs/E109_CODE_LEARNING_MAP.md)。

## Installer / Updater

[Inno](installer/inno/README.md) 负责目录/完成页/可选快捷方式交互；
`scripts/per_user_install.py` 与 `per_user_launcher.py` 提供稳定 UserRuntime 命令。
核心 [per_user_update_core.py](scripts/per_user_update_core.py) 承担启动分类、签名
发布对象、恢复事务和健康确认；UI 不自建版本/签名/恢复引擎。

签名 App、Updater、Maintenance/可变安装状态、程序外账户数据是不同 authority。
窗口出现不等于启动健康；损坏 CURRENT/STATE 时的 canonical identity recovery
仍有最终候选验收缺口。[最终 QA 清单](release/FINAL_E109_CANDIDATE_QA.json) 保留事实。
深层合同：[安装信任](docs/PER_USER_INSTALL_TRUST.md)、
[核心接口](docs/PER_USER_CORE_INTERFACES_V1.md)、[更新](docs/ONE_CLICK_UPDATE_CORE_V1.md)。

## Auth / Keycloak

正常登录 authority 是 [native credential ADR](docs/ADR_NATIVE_CREDENTIAL_AUTH_V1.md)。
[native_auth.py](desktop/native_auth.py) 将原生表单输入经 TLS 交给
[native backend](backend/app/enterprise/identity/native.py)，后者只通过 backend-only
authenticated [Keycloak SPI](deploy/enterprise/native-identity-verifier/README.md)
验证身份，再签发有界 opaque access/rotating refresh session 并重验 membership。
不使用 password grant、Keycloak direct grant，不持久化密码，不回退外部浏览器。
Keycloak 继续拥有密码、账号状态、required action、MFA 与验证/重置 token authority。
Atomic action handlers 使用原生 single-use/revocation authority；已消费 token 不可 re-arm。

`enterprise_oidc.py` 的 code + S256 PKCE、state、nonce、IPv4 callback 及
`in_app_oidc.py` 保留显式 legacy 消费者；它们与 Batch01/03d979 的正常浏览器 UX
已被 Batch02 原生登录 authority supersede。历史协议检查不代表默认登录验收。
当前 backend session/限流有单进程边界；分布式 session/revocation、实际集群 token
authority 与生产拓扑必须另行验收。本地 fixture PASS 不授权 live activation。

当前新 auth host 已准备在 [auth-authority.json](release/auth-authority.json)，状态为
PREPARED_NOT_ACTIVE。[迁移合同](docs/E109_AUTH_ISSUER_MIGRATION.md) 约束两个 exact
issuer、最多 48 小时显式窗口、受控 JWKS、旧 identity namespace 与消费者部署顺序。
此配置的存在不证明 hostname 已切换或真实登录通过。

[account-capabilities.json](release/account-capabilities.json) 是构建能力输入。
register / forgot_password 均为 false；capability 只控制入口，不能代替服务端授权。
[邮件安全](docs/AUTH_EMAIL_SECURITY_FLOW.md) 的 Mailpit UAT 不等于真实 SMTP 合格。

## Backend / API / Workspace

[backend/app/main.py](backend/app/main.py) 挂载学习、教学等 API 并建立 runtime；
[account_core_mvp.py](backend/app/account_core_mvp.py) 提供较小的 Account Core 应用。
FastAPI 处理 HTTP；业务实现拆在 knowledge、teaching、planning、enterprise 包中。

[账户 workspace bootstrap](docs/account-core-workspace-bootstrap-v1.md) 先鉴权，后发现
可见空间，必要时显式初始化。缓存 workspace ID 只作选择提示；每次访问重验 membership。
`desktop/account_identity.py` 与 `account_vault.py` 负责账户身份和本机隔离资料库。
旧单 issuer 文档的账户身份解释须结合 E109 的 identity namespace 迁移合同阅读。

## Evidence / knowledge pipeline

材料能力以 ConversionRegistry 与 API 响应为准；解析、分块/来源指纹、检索与
grounding 支撑可追溯引用。ProviderSession 的 verified 状态是生成前提。
候选修订、材料处置、last-good 激活与计划预览是显式步骤，不用虚构课程兜底。
详见 [知识地图与教师](docs/knowledge-map-teacher-v1.md) 和
[安全与引用](docs/SAFETY_AND_GROUNDING.md)。

仓库保留 video_collection 路由及 [ADR-002](docs/adr/002-video-collection-v1.md)：
目录/字幕/摘要能力与上传原始视频媒体不同；不能将历史 ADR 当成当前所有入口支持视频的证据。
教师、Live2D、语音、练习及 review 通过独立模块消费学习状态；具体能力先查 registry。

## Jobs / background processing

本地 [job_service.py](backend/app/job_service.py) 与专项学习任务存储保留各自状态。
Enterprise [jobs_v1](backend/app/enterprise/jobs_v1/) 提供 durable runtime、repository、
retry、Procrastinate adapter 及 product handlers。production factory 的 Preview 支持
document ingestion、knowledge pipeline、plan creation；EXPORT / ACCOUNT_DELETION
仍显式 fail closed。后台通道不能扩大原始 actor/tenant 权限。

## Persistence

[orm.py](backend/app/orm.py) 选择 PERSONAL_LOCAL SQLite 或显式
`postgresql+psycopg` 的 ENTERPRISE_SERVER；旧本地 JSON/SQLite 兼容数据仍存在。
[Alembic](backend/alembic/) 管理迁移；不要从文档中的历史 head 推断当前 head。
[tenant repository/UoW](backend/app/enterprise_tenancy/) 把数据库事务绑定 TenantContext。
深层资料：[tenant persistence](docs/enterprise/tenant-persistence-integration.md)、
[RLS](docs/enterprise/rls-policy.md)、[migration strategy](docs/enterprise/migration-strategy.md)。

## Release / update distribution

[build_e108_user_candidate.py](scripts/build_e108_user_candidate.py) 保留历史文件名，
当前 E109 最终版本目标见 QA JSON；它和 Inno 构建器消费冻结版本/能力/签名输入。
发布对象签名、不可变路径、上传白名单与安装事务独立于 branch promotion。
已验证的历史 mandatory floor 单调不下降；current 低于 floor 时，目标直接为当前
latest（例如 1.0 → 历史 mandatory 1.2 → latest optional 1.4，必须更新至 1.4）。
断网、稍后、取消和 reason 文案不能解除既知强制门；optional 允许继续使用。
reason 仅作中文解释，状态由 core 决定；UI 不重算版本和签名策略。
[发布合同](docs/windows-update-release-contract-v1.md) 与
[不可变路径](docs/update-immutable-path-contract.md) 是查证入口。
`deploy/`、`.github/` 的配置存在不代表容器、Actions 或 staging 已运行；执行需独立授权。

## Observability / security

[observability](backend/app/observability/) 提供 request/trace 关联、allowlisted metrics、
health、audit 和 recovery；[ops](ops/observability/README.md) 存放外部观测栈配置。
采集内容不得带 prompt、材料、用户答案或凭据。[运行边界](docs/enterprise/E1-06_OBSERVABILITY_RECOVERY.md)
中的租约/运行结果属于历史快照；实际状态 UNKNOWN 时保持 UNKNOWN。

[enterprise-context.v1](backend/app/enterprise/contracts_v1/README.md) 是共享 Actor、Tenant、
ResourceScope 和 AuditEnvelope 定义；各子系统不得复制一套共享类型。
Provider 使用 credential reference / secret resolver；原始 secret 不进 repo、handoff 或日志。
签名信任、身份验证、租户授权、路径/归档安全、用户数据隔离是分别需要验证的门。

## 权威与查证

- 当前源码：CURRENT_STATE 冻结的 exact SHA/tree；不是工作目录名或远端默认分支猜测。
- 当前产品门禁：CURRENT_STATE、native auth ADR、更新合同及带 source/candidate 的正式回执；E109 QA JSON 保留历史候选范围。
- 当前协作规则：[workflow](docs/governance/DEVELOPMENT_WORKFLOW.md)；历史 E1 员工编号不自动映射 MAIN3。
- 全目录导航与测试：[COMPONENT_MAP](docs/project/COMPONENT_MAP.md)。
- 旧 `docs/ARCHITECTURE.md` 和 dated reports 保留，适用范围/冲突见 CURRENT_STATE。
