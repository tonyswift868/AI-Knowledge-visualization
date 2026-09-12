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
```

main2 为冻结 canonical source baseline；integration/main3 是施工集成线。
正式 main3 = NOT_CREATED；PRODUCT ACCEPTANCE = PENDING。
本轮 authority：`MF-MAIN3-A-BATCH02-INTEGRATION-20260913-01`。
Batch02 起点 `15f6d0dda0968be1b39295577a139a9017615e82`，tree
`53ed1867beb9afcb3944045cc837b52563d0f3b5`。知识原交付保持原 SHA/tree。

## 控制面与 exact integration

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

## 验收证据分层

| 层次 | 当前证据 | 限制 |
| --- | --- | --- |
| A combined regression | native auth/session/bootstrap/main；signed floor/latest/incremental/full fallback；optional/mandatory/UI；edge/capability | synthetic/inert/offscreen 与本地运行范围；具体计数见 receipt |
| A native fixture | 集成源码构建 JAR；Keycloak 26.7.3 + PostgreSQL 17.6；真实 mTLS verifier、原生表单→session→bootstrap→main | 一次性本机身份、数据和端口；没有生产账号/服务变更 |
| A action tokens | verification/reset 2/5/10 并发各仅 1 次成功；失败者 HTTP400，无 HTTP500；重放、resend、cross-purpose、重启后消费状态 | 单实例本地原生 fixture，未证明实际生产集群拓扑 |
| 04 independent confirmation | exact 69e68 fix，独立 JAR SHA256 43657662bc85497734e99a9aeb86fd2db3a3307ec12b438c9ffe0834c170269a；23/23 原生检查 PASS | A 比较集成 JAR 全部 class/resource 相同；容器时间戳可改变 JAR hash |
| 04 native RLS | PostgreSQL 17.6 原生 120 denial probes 与 controls；独立 retest 直接非特权 application login smoke PASS | E109 原报告整体 PARTIAL 保留；其中 token 缺陷由新独立 receipt supersede。A 未重新跑 RLS，不把 retained PASS 写成 A 执行 |
| 当前生产/最终候选 | REAL_SMTP_SEND=0；VERIFY/RESET NOT_RUN；REAL_INSTALL_QA NOT_RUN；最终 1.5.0→1.5.1 NOT_RUN | LIVE_PRODUCTION_LOGIN NOT_RUN；PRODUCTION_KEYCLOAK_MUTATION=0；用户最终验收 PENDING |

本轮默认后端连接下两项 native-DSN 测试跳过；并不否定 04 的独立原生 RLS 证据。
两次 A fixture 均已停止进程、删除私钥；递归清理目录被自动审批拒绝，临时数据目录留在 E:，该限制见 receipt。
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
