# Current state — source and acceptance
固定字段由 compact handoff 读取；提交快照不是实时服务/进程状态。
```text
PROJECT = MentorFlow
GENERATION = MAIN3
BASELINE_BRANCH = main2
MAIN2_CANONICAL_SHA = 04faf2a9fbb50c5f9403dad5abf46354c9b53d82
MAIN2_CANONICAL_TREE = 61ddb1b2b80e4c1b0786c5b8ee65d3d801ff3031
SOURCE_BASELINE = CONTROL_CONVERGENCE_SOURCE
PRODUCT_ACCEPTANCE = PENDING_EXTERNAL_AUTHORITY_AND_HUMAN_UAT
INTEGRATION_BRANCH = integration/main3
INTEGRATION_REMOTE_SHA = a0b40f0c3c4972668e4c227b5287344c60c863b9
CHATGPT_READ_BRANCH = control/main3-current
KNOWLEDGE_COMMIT = 1b8f30862f24ac989a6fbdef2f0819afb44a7b06
KNOWLEDGE_TREE = fd285c9e42af0fbc4b2205ec479bc8416296ee98
NORMAL_DESKTOP_LOGIN_UX = NATIVE_IN_APP_USERNAME_PASSWORD
NORMAL_LOGIN_EXTERNAL_BROWSER_REQUIRED = NO
ALEMBIC_HEAD = 0040_admin_overview_aggregate
BACKEND_EXPECTED_HEAD = 0040_admin_overview_aggregate
GATE_A_SCHEMA_BLOCKER = RESOLVED_IN_SOURCE
GATE_A_REAL_DEPLOYMENT = NOT_RUN_AFTER_FIX
BATCH03_SOURCE = INTEGRATED
BATCH04_SOURCE = INTEGRATED
FULL_SERVER_SYNC = PARTIAL
PRODUCER_UX_WIRING = PASS
TEST_TARGET_STATUS = NO_NEW_USER_TEST_PUBLICATION
FROZEN_CANDIDATE = HISTORICAL_EVIDENCE_ONLY
REAL_A_B_REHYDRATION = PASS_NATIVE_POSTGRES_HTTP_SCOPE; FROZEN_PRODUCT_UAT_NOT_RUN
CROSS_DEVICE_DATA_CONTINUITY = REQUIRED
PORTABLE_PACK_V2 = REQUIRED_NOT_IMPLEMENTED
MIGRATION_EXECUTED = NO
CURRENT_PHASE = CONTROL_CLOSEOUT; A_B_AND_PRE02_SOURCE_QUALIFICATION; EXTERNAL_GATES_BLOCKED
```
main2 为冻结 canonical source baseline；integration/main3 是施工集成线。
正式 main3 = NOT_CREATED；PRODUCT ACCEPTANCE = PENDING。
当前 authority：`CONTROL_CONVERGENCE_CODE_INTEGRATION`；Account MVP 仅保留历史/Frozen smoke 证据。
GENERIC_SYNC_FOUNDATION_SOURCE = QUALIFIED
PROFILE_SETTINGS_EVIDENCE = LOCAL_FIXTURE_PARTIAL_CORRECTNESS_REVIEW_REQUIRED
LEARNING_RUN_HISTORY = PARTIAL
PLAN_TASK = REAL_STORE_AND_VERSIONED_WRITE_ENTRY_REVIEW_REQUIRED
POWERSYNC = CLIENT_POC_ONLY; SERVER_ADOPTION_GATE_NOT_PASSED
FULL_ACCOUNT_REHYDRATION = NOT_YET_COMPLETE

## 当前 control 收口（2026-09-15）
唯一 Web 入口为 `control/main3-current`；integration/main3、main2 不自动推进。
03 `80b619d` sealing delta 已收拢，保留 efb354c PRE02 修复；不再等待 03 回执。
A/B 为 native PostgreSQL + authenticated HTTP + independent Python Desktop clients，非 Frozen/UI UAT。
覆盖注册类型的 IDs/值、revision、隔离、B 重启、缺失本地资源；没有 A→B 文件复制。
PRE02 包括 password binding、0040 exact RLS、reset consumer、startup、email readiness 和 admin/member。
真实邮箱 consume/replay/expiry/reset/same-user continuation、TonySwift 精确映射及真实发送需独立 authority。
本轮真实 SMTP 发送=0，TonySwift 修改=0；旧用户数据迁移=NO。
[主上下文与服务器清单](../../GPT_WEB_MENTORFLOW_MASTER_CONTEXT.md) 区分 VPS staging 与本机已过期 TEST 租约。
DO_NOT_REDO：不重集成历史 delivery、不重建 Frozen、不改 CURRENT、不迁移、不切 public issuer。
NEXT_TASK：Owner 批准 external lifecycle/角色映射与 human UAT 后单独派发；当前 STOP。
以下为历史证据，不代表当前 artifact、服务可用或当前唯一 blocker。

## Frozen V3 — historical real A/B stop before A product write

历史 V3 build base `9cd6279917bbdd7b0dc9db2f860bc9d34021beb2` 已包含指定 02/01 exact fixes；UNKNOWN_COMMITS=0，无重复应用。
新 EXE 从该 exact source/tree `d493b8046fe9296729a43b42137e53cce89ef703` 重建，sha256 `27daa94ca1ab75a9d659ecaa2ce52720545ef08fc050d6139340a257aeaf6d43`。
FROZEN_NATIVE_LOGIN=PASS；手工与实际 Workbench A 按钮 → native login/workspace/auth-me/snapshot 均 200；同 EXE 错密码、未知账号、bad CA 拒绝。
FIRST_DIVERGENCE：点击“进入学习空间”后，`desktop/account_backend.py:32-36` 前插 `_MEIPASS/backend`，子进程 `app.main` 导入失败。
`NoneType.write` 是无控制台 multiprocessing 报错时的次生错误；source 同函数 import PASS，Frozen 原路径稳定失败。
只在诊断进程移除该路径可进入 canonical `app.main`，随后发现 required knowledge-policy 资源缺失；没有修改/合格化产品 EXE。
LIKELY_OWNER=01（Desktop Frozen 本地服务与资源闭包）；本轮按新跨-owner blocker 停止，不跨域修复或另行派工。
V3 target=FAIL；旧 V2=SUPERSEDED。WORKBENCH_RESULT=实际 A observation + 当前失败候选，不能启动失败候选继续验收。
TRUE_EMPTY=PASS（真实 server-confirmed 支持范围）；disposable subject 的 server durable entities=0；未写测试 plan/progress/resource。
A 已完全关闭；B 独立空根已准备，B 启动/登录/恢复、stable IDs、物理资源缺失与真实 sync-failure control=NOT_RUN。
组合聚焦 481 PASS、0 FAIL、0 SKIP（479 + 原生 PG 补测 2）；独立产品门禁 1 FAIL，不能被测试绿灯覆盖。
[Blocker receipt](../governance/receipts/20260914-real-ab-closeout-blocker.json) 绑定 exact artifact、UI traceback、source/frozen differential、真实 HTTP/observation 和测试证据。
现有 Backend/Identity/PG 与 TLS/mTLS 密钥保留；租约仍健康，有效至北京时间 2026-09-14 10:30:19；AUTO_CLEANUP=FORBIDDEN。
FULL_SERVER_SYNC=PARTIAL；Portable Pack V2=NOT_IMPLEMENTED；REAL_USER_MIGRATION=NO；未访问 tonyswift555 历史 88 plans。
PUBLIC_ISSUER_CUTOVER/REGISTER/FORGOT_PASSWORD=NO；Gate A 真实部署、最终签名安装更新及用户验收仍未通过。
历史 [V2 login receipt](../governance/receipts/20260913-frozen-login-fixed-candidate-v2.json) 的登录范围保持有效，未证明本地学习服务/完整 A/B。

## Batch03 与跨设备长期 authority（保留）

[Batch03 receipt](../governance/receipts/20260913-main3-batch03-integration.json) 记录八个新 exact commits 与 04 evidence-only。
**CROSS_DEVICE_DATA_CONTINUITY = REQUIRED**；[三层／Portable Pack V2 authority](../decisions/CROSS_DEVICE_DATA_CONTINUITY_V2.md)
规定 server structured state、local large resources、rebuild/redownload，以及先恢复云状态再补资源。
V2 QUICK_EXPORT/FULL_EXPORT、可选模型／大 payload、账号绑定、完整性及保密仍 REQUIRED_NOT_IMPLEMENTED。
现有 V1 排除 model_binaries、明文 ZIP、新／空 vault 限制未被静默改写；导入不得覆盖来源，必须幂等、拒绝错误账号、无凭据。
智能分类／推荐及 full supported-history 仍为后续门禁；84-workspace 私有 chooser 仅 QA，不是最终 UX。
04 retained 数据存在，无完整事前 baseline，不声称所有旧记录完整。**MIGRATION_EXECUTED = NO**。

## Gate A schema 0036 专项解阻（历史）

原办公室从 `102f734890d76bc7c079a75a14958299c2882269` 继续，只消费 04 exact
`0d064974a145e758cdc8e2c8700eca7a13576157`，集成为 `34fc9c95e9e51da832ef1bc6aab587ee6a3a041a`。
0036 是从 retained staging release / running image 恢复的逐字权威迁移；没有导入旧 release ancestry 或新增 import 产品功能。
当时 Alembic single head 与 backend expected head 均为 `0036_account_import_ledger`。
A 本轮重新跑本地 PostgreSQL 18/18：fresh/0035/0033→0036、already-0036 只读接受、
unknown future/十类 schema drift 拒绝、worker/backend preflight、ledger RLS/idempotency/rollback、
default LEARNER 与 workspace bootstrap 连续性；加受影响后端与登录/更新 smoke，计数见
[专项 receipt](../governance/receipts/20260913-gatea-schema-0036-unblock.json)。本轮 fixture 已清理。
**Gate A schema blocker = RESOLVED_IN_SOURCE；REAL DEPLOYMENT = NOT_RUN AFTER FIX。**
此前部署遇到 schema source 缺失是用户提供的背景；本轮没有重新部署或操作 staging DB，不能写 Gate A PASS。
REAL SMTP 继续由独立 04 任务推进，本轮不轮询、执行或验收；旧 SMTP 计数只描述 Batch02。
该 schema 专项当时仅消费 0036；其它员工状态为交付快照，不代表独立任务实时状态。

## Batch02 控制面与 exact integration（历史）

A 完成本任务后 SILENT；01–04 与 B 均 SILENT。未联系、轮询或等待员工任务。
A 原办公室复用，Phase0 实测 HEAD/index/clean 起点与 integration/main3 绑定。
01–04 当前 worktree/ref 绑定只读核对，先前 isolation 证据保留；不冒充重新执行其 Phase0。
B06 setup DONE 和 branch-picker 历史证据不替代本轮本地检查。

| 槽位 | Batch02 final exact source | 接受范围 |
| --- | --- | --- |
| 01 | 69e68c86e21403bb8c5a365483bda62b074a9ea2 | native login/verifier PASS；token atomicity 独立确认，A 本地原生复测 PASS |
| 02 | 521ba9fb0248c2dc973cbba6cc44e6d8f0f6baeb | monotonic signed mandatory floor / current latest / public reason PASS |
| 03 | 3eba9c3e0198d6b485226737e59d594cb773a12c | optional/mandatory gates / 中文 reason UX PASS |
| 04 | f9634e24d759eb268c56d45b301046dc503ef714 | edge encoding fix PASS；native PostgreSQL RLS PASS 原执行者证据保留 |

全部 11 个连续 exact commits（含 report-only 和被 supersede 的中间提交）按
02→03→04→01 消费，没有未知 commit。3 个文本冲突文件依 authority 合并。
组合中修复重复 snapshot keyword，以及强制更新完成后原生输入框未重新启用。
更新三项旧测试前提，新增 15 项 native/core/UI 组合检查。exact source→integrated
parent/tree/path 映射和检查结果见 [Batch02 receipt](../governance/receipts/20260913-main3-batch02-integration.json)。
历史 [Batch01 receipt](../governance/receipts/20260912-main3-batch01-integration.json) 保留其当时范围。

## 正常登录与更新 authority

Launch → 原生用户名/密码 → MentorFlow backend → backend-only authenticated
verifier → Keycloak credential provider → MentorFlow opaque session → workspace
bootstrap → main。禁止 password grant、Keycloak direct grant 和密码持久化。
[Native auth ADR](../ADR_NATIVE_CREDENTIAL_AUTH_V1.md) 是正常登录长期 authority。
Batch01 及 `03d979...` 的 System Browser 正常 UX 已 superseded；
[issuer migration](../E109_AUTH_ISSUER_MIGRATION.md) 仅保留 legacy 协议/基础设施范围。
原生 verifier 未显式配置时拒绝认证，不回退浏览器。

OPTIONAL 可继续、稍后、更新并重启或退出时更新。MANDATORY 同时阻止 login/main，
只允许更新至当前 latest 或退出；断网和取消不能清除已知签名 floor。
current 1.0、历史 mandatory 1.2、latest optional 1.4 → MANDATORY，TARGET=1.4。
reason 只解释原因，不决定状态；未知 reason 使用通用中文。
详情复用 [更新合同](../windows-update-release-contract-v1.md)。

## Batch02 验收证据分层（历史）

| 层次 | 当前证据 | 限制 |
| --- | --- | --- |
| A combined regression | native auth/session/bootstrap/main；signed floor/latest/incremental/full fallback；optional/mandatory/UI；edge/capability | synthetic/inert/offscreen 与本地运行范围；具体计数见 receipt |
| A native fixture | 集成源码构建 JAR；Keycloak 26.7.3 + PostgreSQL 17.6；真实 mTLS verifier、原生表单→session→bootstrap→main | 一次性本机身份、数据和端口；没有生产账号/服务变更 |
| A action tokens | verification/reset 2/5/10 并发各仅 1 次成功；失败者 HTTP400，无 HTTP500；重放、resend、cross-purpose、重启后消费状态 | 单实例本地原生 fixture，未证明实际生产集群拓扑 |
| 04 independent confirmation | exact 69e68 fix，独立 JAR SHA256 43657662bc85497734e99a9aeb86fd2db3a3307ec12b438c9ffe0834c170269a；23/23 原生检查 PASS | A 比较集成 JAR 全部 class/resource 相同；容器时间戳可改变 JAR hash |
| 04 native RLS | PostgreSQL 17.6 原生 120 denial probes 与 controls；独立 retest 直接非特权 application login smoke PASS | E109 原报告整体 PARTIAL 保留；其中 token 缺陷由新独立 receipt supersede。A 未重新跑 RLS，不把 retained PASS 写成 A 执行 |
| 当前生产/最终候选 | REAL_SMTP_SEND=0；VERIFY/RESET NOT_RUN；REAL_INSTALL_QA NOT_RUN；最终 1.5.0→1.5.1 NOT_RUN | LIVE_PRODUCTION_LOGIN NOT_RUN；PRODUCTION_KEYCLOAK_MUTATION=0；用户最终验收 PENDING |


[auth-authority](../../release/auth-authority.json) 保持 PREPARED_NOT_ACTIVE / migration=null；
[account-capabilities](../../release/account-capabilities.json) 保持 register=false / forgot_password=false。
两文件相对 frozen main2 字节未变。本轮没有迁移日期、真实邮件或 capability 激活。
[历史 final QA](../../release/FINAL_E109_CANDIDATE_QA.json) 不替代最终集成候选验收。

## 下一波 gate（只定义，不执行）

- Gate A：批准后的 native backend/SPI/desktop 兼容部署、TLS/mTLS、实际 session 拓扑、旧 identity continuity；issuer cutover 另行授权。
- Gate B：保留已完成 native RLS/token 证据，补实际生产拓扑、public issuer、真实 SMTP verify/reset 与 native account-action capability 验收。
- Gate C：一个 exact 签名候选的 1.5.0→1.5.1、增量/完整 latest、UAC、重启健康、恢复/rollback 与资料保持。

详见 [MAIN3 plan](../exec-plans/active/MAIN3.md) 和 [queue](../governance/DEPENDENCY_QUEUE.json)。
NOT_DISPATCHED 不代表执行授权；没有新增员工派发或自动等待。

## 长期知识与历史

临时 `13_DURABLE_REQUIREMENTS_BRIDGE_UNTIL_REPO_KNOWLEDGE_LANDS.md` =
SUPERSEDED / BRIDGE CLOSED；12/12 repo-native authority 见 [handoff contract](../handoff/README.md)。
知识 COMPLETE / Batch02 source 与 native-fixture PASS 均不表示产品最终验收。
[知识 intake receipt](../governance/receipts/20260912-repository-knowledge-intake.json) 保留原始证据。
MAIN2 freeze：`MF-GOV-MAIN2-CANONICAL-FREEZE-20260912-01`；04faf 为唯一 canonical baseline。
56bcc 属 MAIN1_HISTORY；a198 含排除的 21337c1 ancestry，QUARANTINED_NOT_CANONICAL。
旧 READ_FIRST/HANDOFF 历史不覆盖本轮重写的 GPT_WEB 当前主上下文。
最终 publication SHA/tree 由 immutable receipt 和 remote 回读绑定，避免文档自引用 SHA。

## Generic sync foundation (2026-09-14)

`04` delivery `dd86307c387ed8012a6edbe130b6ccd7eb510d68` integrated through its exact parent chain (`f097cd4`, `c423bc9`, `6a15de5`, `dd86307`). `SYNC_ITEM_REGISTRY` and migration `0039_generic_sync_kinds` are now the canonical integration baseline. Unsupported kinds remain fail-closed; structured sync entities/projections remain non-authoritative. Full account rehydration is NOT_YET_COMPLETE; domain adapters remain pending.

