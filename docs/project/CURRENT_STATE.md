# Current state — source and acceptance

以下固定格式字段由 compact handoff 读取。来源是本轮 dispatch 与 MAIN2 冻结回执；
状态是可提交快照，不能代替新会话重新核对正式 delivery。

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
```

main2 是冻结的 canonical source baseline；integration/main3 是施工集成线。
正式 main3 = NOT_CREATED；PRODUCT ACCEPTANCE = PENDING。
本轮 authority：`MF-MAIN3-A-WORKER-BATCH01-INTEGRATION-20260912-03`。
起点 `404bc1a7794582dd6bde3a5db48d15ded0041a25`，tree
`139b26d0231e234fd63c04145ba38abfb2cac847`。知识原交付保持原 SHA/tree。

## 控制面与 exact delivery

A 接单时 RUNNING，完成本轮收口后 SILENT；01–04 和 B 均 SILENT。
所有 Phase0 worktree isolation = VERIFIED（本轮用户 authority）；A 已重新实测
独立 managed worktree、HEAD/index、clean 起点与 integration/main3 绑定。
B06 setup DONE，branch picker 证据不替代 worktree 验证。未联系、轮询或等待 01–04。

| 槽位 | 已接受的 source delivery | 尚未完成 |
| --- | --- | --- |
| 01 | SOURCE_DELIVERY_ACCEPTED_FOR_INTEGRATION | LIVE_ACTIVATION_PENDING；backend 未部署，desktop 未构建/安装，迁移窗口未启动 |
| 02 | SOURCE_REPAIR_ACCEPTED | LIVE_INSTALL_QA_PENDING；不声称最终 1.5.0→1.5.1 PASS |
| 03 | SOURCE_UX_CLOSEOUT_ACCEPTED | REAL_INSTALL_QA_PENDING；不声称最终产品 PASS |
| 04 | SOURCE_FIX_ACCEPTED | SECURITY_ACCEPTANCE_PARTIAL_PENDING；原生令牌、严格单次使用、public issuer、RLS、限流、反枚举、真实 SMTP 未过门 |

Exact source 与 cherry-pick 映射见 [board](../governance/ACTIVE_BOARD.json)，
审计、组合回归及证据范围见 [batch01 receipt](../governance/receipts/20260912-main3-batch01-integration.json)。
应用顺序 02→03→01→04；文本冲突 0。01/04 auth/capability、02/03 repair/UI 的
语义耦合均登记并新增组合测试。旧 prelogin 测试显式传入 capability，同时验证缺省拒绝。

## 当前 source 与产品边界

| 事项 | 本轮证据 | 仍需验收 |
| --- | --- | --- |
| canonical identity recovery | 四类 CURRENT/STATE 缺失或损坏的签名恢复、篡改拒绝、rollback/下次启动与 UI gate 组合回归 | 最终签名候选原生恢复、资料保持、旧版本可启动 |
| Windows System Browser | E109 NEW 配置走系统浏览器；PKCE/state/nonce、动态 IPv4 callback、issuer/API 分离 source PASS | 最终构建与真实登录；legacy/PREPARED 路径仍按原合同 |
| issuer migration | bounded acceptance、strict oidc-ready 与 identity continuity source PASS | 兼容消费者部署、TLS、获批 <=48h 窗口、旧登录/refresh，再 cutover 与新登录 |
| account/security | capability boundary、默认 LEARNER、bootstrap、跨租户及已有 token/policy source tests | native PostgreSQL RLS、public issuer、verification/reset token runtime、strict single-use、反枚举、限流、真实邮件 |
| installer/update UX | progress/cancel/finish-X/默认 OFF/error masking/parent semantics 与 core/adapter 回归 | 最终 exact 1.5.0→1.5.1、增量、UAC、apply/restart/health、人工 QA |
| Interview + Acceptance | PENDING_MANDATORY_MILESTONE | rationale、业务→代码→测试、exact source/candidate、分层证据和用户结论 |

[auth-authority](../../release/auth-authority.json) 仍 PREPARED_NOT_ACTIVE / migration=null；
[account-capabilities](../../release/account-capabilities.json) 仍 register=false / forgot_password=false。
两个文件相对 frozen main2 字节未变。未写 live dates，未发送真实邮件，未启用入口。
[历史 final candidate QA](../../release/FINAL_E109_CANDIDATE_QA.json) 是旧候选证据，
不替代本轮 source receipt 或后续最终候选验收。

## 下一波 gate（只定义，不执行）

- Gate A：AUTH LIVE ACTIVATION，兼容 backend/desktop、TLS、批准共享窗口、旧登录连续性后才 cutover。
- Gate B：ACCOUNT SECURITY，native RLS/public issuer/令牌 runtime 与单次使用/反枚举/限流/真实 SMTP。
- Gate C：UPDATE FINAL CANDIDATE，精确版本、增量、UAC、重启健康、恢复和失败时旧版本可启动。

详见 [MAIN3 plan](../exec-plans/active/MAIN3.md) 和 [queue](../governance/DEPENDENCY_QUEUE.json)。
NOT_DISPATCHED 表示 source prerequisites 已满足且可规划；不代表 live 执行授权。
外部服务今天状态未探测；旧 dated UAT 不作为今天真值。

## 长期 authority 与历史

`13_DURABLE_REQUIREMENTS_BRIDGE_UNTIL_REPO_KNOWLEDGE_LANDS.md` =
SUPERSEDED / BRIDGE CLOSED。历史文件不删除；当前 repo-native authority 的 12/12
索引见 [handoff contract](../handoff/README.md)，新会话不再依赖临时 bridge。
知识 COMPLETE 与 batch01 source PASS 均不表示产品验收完成。

原知识 [intake receipt](../governance/receipts/20260912-repository-knowledge-intake.json)
保留 Phase0 与 exact 195fb241 source 证据。
MAIN2 freeze：`MF-GOV-MAIN2-CANONICAL-FREEZE-20260912-01`；04faf 为唯一 canonical baseline。
56bcc 属 MAIN1_HISTORY；a198 含排除的 21337c1 ancestry，QUARANTINED_NOT_CANONICAL。
旧 GPT_WEB/READ_FIRST/HANDOFF 与旧员工编号仅作历史，登记见 handoff legacy registry。
最终 publication commit 由 immutable receipt 和 remote ref 回读绑定，避免文档自引用 SHA。
