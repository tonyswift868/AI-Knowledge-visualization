# Current state — source and acceptance

固定字段由 compact handoff 读取；提交快照不是实时服务/进程状态。

```text
PROJECT = MentorFlow
GENERATION = MAIN3
BASELINE_BRANCH = main2
MAIN2_CANONICAL_SHA = 04faf2a9fbb50c5f9403dad5abf46354c9b53d82
MAIN2_CANONICAL_TREE = 61ddb1b2b80e4c1b0786c5b8ee65d3d801ff3031
SOURCE_BASELINE = CANONICAL_SOURCE_BASELINE
PRODUCT_ACCEPTANCE = PENDING_E109_FINAL_QA
INTEGRATION_BRANCH = integration/main3
KNOWLEDGE_COMMIT = 195fb241f30cbd9e6ea376bd6d22b76843e5d53a
KNOWLEDGE_TREE = 46a93d98bef697831ae9d9496334d902bc6ce181
NORMAL_DESKTOP_LOGIN_UX = NATIVE_IN_APP_USERNAME_PASSWORD
NORMAL_LOGIN_EXTERNAL_BROWSER_REQUIRED = NO
ALEMBIC_HEAD = 0036_account_import_ledger
BACKEND_EXPECTED_HEAD = 0036_account_import_ledger
GATE_A_SCHEMA_BLOCKER = RESOLVED_IN_SOURCE
GATE_A_REAL_DEPLOYMENT = NOT_RUN_AFTER_FIX
BATCH03_SOURCE = INTEGRATED
CROSS_DEVICE_DATA_CONTINUITY = REQUIRED
PORTABLE_PACK_V2 = REQUIRED_NOT_IMPLEMENTED
MIGRATION_EXECUTED = NO
CURRENT_PHASE = 第三批源码已集成，跨设备合同已建立
```

main2 为冻结 canonical source baseline；integration/main3 是施工集成线。
正式 main3 = NOT_CREATED；PRODUCT ACCEPTANCE = PENDING。
当前 authority：`MAIN3_BATCH03_AND_CROSS_DEVICE_CONTINUITY_AUTHORITY`。
以下保留 Batch02／schema 解阻的历史执行范围；当前 Batch03 见下一节。
Batch02 起点 `15f6d0dda0968be1b39295577a139a9017615e82`，tree
`53ed1867beb9afcb3944045cc837b52563d0f3b5`。知识原交付保持原 SHA/tree。

## Batch03 源码与跨设备连续性

起点 `d5169081bfcc3510ba2e4a644872294cd70ce3d7`；01 两项、02 三项、03 三项 exact commits
按 02→03→01 集成；各自直接延续 Batch02 已消费点，无重复／未知提交。04 本阶段零源码，
仅验证 retained continuity evidence 的 46 个 artifact hash，归一化公开事实，未导入私有清单。
密码重置以服务端 after-commit 事实为准，完成后清空输入并返回原生登录；正常登录与 mandatory gate 保留。
账户生命周期和数据恢复仅为未激活的 UI／预览壳；Workbench 从 repo exact snapshot 读取，当前候选仍待构建。
组合回归、三处冲突处理及 Workbench 日期校验修复见 [Batch03 receipt](../governance/receipts/20260913-main3-batch03-integration.json)。

**CROSS_DEVICE_DATA_CONTINUITY = REQUIRED**；[跨设备／Portable Pack V2 authority](../decisions/CROSS_DEVICE_DATA_CONTINUITY_V2.md)
规定服务端结构化状态、本地大文件和重建／重下载三层，以及先恢复云状态、再导入缺失资源的顺序。
V2 定义 QUICK_EXPORT / FULL_EXPORT、可选模型／大 payload、账号绑定、认证完整性与保密；
现有 V1 排除 model_binaries、明文 ZIP、新／空 vault 限制均未被静默改写。
全量 server sync、V2 执行器、智能分类推荐及新设备产品接线仍待实现／验收。
04 证据：历史数据存在，尚无数据丢失证明；没有完整事前 baseline，不声称所有记录完整。
目标仍为 Temp，localhost→public issuer identity continuity 尚未稳定；MIGRATION_EXECUTED=NO。
私有 84-workspace chooser 仅为 QA，不是最终 UX；产品先分类／推荐，仅真实歧义交给用户。
Workbench 的旧数据连续性显示 BLOCKED；不把 audit PASS 投影为迁移／跨设备 PASS。
register/forgot_password 保持 false。Batch03 未进行真实部署、SMTP、旧用户迁移或安装验收；
此前 Gate A NOT_RUN_AFTER_FIX 保留为本任务未重验的历史门禁，不推断其他任务实时部署状态。
A 收口 SILENT；无需用户操作、无新员工派发。正式 main3 未创建，main2 冻结。

## Gate A schema 0036 专项解阻（历史）

原办公室从 `102f734890d76bc7c079a75a14958299c2882269` 继续，只消费 04 exact
`0d064974a145e758cdc8e2c8700eca7a13576157`，集成为 `34fc9c95e9e51da832ef1bc6aab587ee6a3a041a`。
0036 是从 retained staging release / running image 恢复的逐字权威迁移；没有导入旧 release ancestry 或新增 import 产品功能。
Alembic single head 与 backend expected head 均为 `0036_account_import_ledger`。
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

Batch02 默认后端连接下两项 native-DSN 测试跳过；并不否定 04 的独立原生 RLS 证据。
Batch02 两次 A fixture 均已停止进程、删除私钥；递归清理目录被自动审批拒绝，临时数据目录留在 E:，该限制见 receipt。
当前 session/限流有单进程内存状态边界；实际分布式 session/revocation、SPI 版本和
共享 token single-use authority、长期主窗口 session renewal 均需部署拓扑验收。

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
旧 GPT_WEB/READ_FIRST/HANDOFF 及员工编号仅作历史，不覆盖当前 authority。
最终 publication SHA/tree 由 immutable receipt 和 remote 回读绑定，避免文档自引用 SHA。
