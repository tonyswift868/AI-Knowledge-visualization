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

main2 是冻结的 canonical source baseline；MAIN3 是 active development generation。
本轮 authority：`MF-MAIN3-A-KNOWLEDGE-INTEGRATION-WEB-HANDOFF-20260912-02`。
知识 exact commit 已在隔离 worktree fast-forward 集成，原 commit/tree 未重造。
本轮只改知识文档、控制面和 context tools，不改产品代码，不变更 main2。

## MAIN3 控制面快照

- B06 repository setup DONE；六个相关 ref 的 branch picker 已由用户截图确认。
  证据来自本轮用户 dispatch 的明确陈述；本轮未重新读取截图，也不由此推导 checkout 隔离。
- B=SILENT。A 接到本 dispatch 时任务进行中；知识集成与 handoff 检查完成后 SILENT。
- active slots = A/01/02/03/04；01–04 本轮新 dispatch 已发布，详细 task/dispatch ID
  未包含于本任务包，运行/完成状态 UNKNOWN，不臆造分工，不直接通信、不轮询。
- actual worktree isolation 必须逐员工用 Phase 0 receipt 收证。A 已实测隔离：独立
  worktree git-dir/HEAD/index，starting HEAD 等于 baseline，安全 attach integration/main3。
  01–04 的 isolation receipt 尚未提供；branch picker 不能代替这些证据。
- 本轮只读远端 preflight 观察 GitHub default=main2；外部服务今日状态 UNKNOWN。
  远端发布 exact HEAD 由本轮最终 immutable
  receipt / 远端 ref 回读证明，不在可提交文件中自引用其最终提交 SHA。
- 长期要求 authority 已覆盖 12/12，入口见 [web contract](../handoff/README.md)。
  Repository knowledge COMPLETE 仅表示知识交付；product acceptance 仍 PENDING。

## 已存在并已接入的主要源码

- Windows Qt desktop、账户壳与账户资料隔离、证据/知识地图/计划/教师模块。
- 07 core → 03 单一 UI adapter → 02 Inno → 05 capability config 的 E109 集成。
- A 的 issuer migration source/config binding，旧账户 identity namespace 保留。
- Enterprise identity、tenant persistence、durable jobs、观测与发布配置；各 runtime 门禁单独验收。

来源：[E109 integration](../E109_INTEGRATION_MAP.md)、
[code map](../E109_CODE_LEARNING_MAP.md)、[auth migration](../E109_AUTH_ISSUER_MIGRATION.md)。
“已接入源码”不表示所有操作已在最终包或真实服务成功执行。

## 收口事项 / release blockers

现有正式材料没有统一 P0/P1 数字分级：`P0/P1_CLASSIFICATION = UNKNOWN`。
下列事项具有明确的验收阻塞证据，保留原含义，不自行提升或降低严重度。

| 事项 | 已知状态 | 缺少的证据 |
| --- | --- | --- |
| canonical identity recovery | PENDING_QA，已知 CURRENT/STATE 损坏分类缺陷 | 最终签名候选安全恢复与资料不丢失 |
| 强制更新 / 坏版本恢复 / 同版本修复 | PENDING_QA | 最终唯一候选的包级验证 |
| 新 issuer | PREPARED_NOT_ACTIVE | 兼容 backend/desktop 部署回读、旧登录、cutover、新登录 |
| Windows System Browser 登录 | REQUIREMENT_GAP | 当前源码仍有内嵌授权；系统浏览器 PKCE、动态 IPv4 callback 与 bootstrap 的最终候选证据 |
| 真实邮件与账号入口 | register=false，forgot_password=false | 真实 verify/reset 邮件与新 issuer 登录验收 |
| 最终用户候选 | NOT_BUILT_WAITING_REAL_SMTP | 最终安装器、原生操作、用户人工 QA |
| Interview + Acceptance bundle | PENDING_MANDATORY_MILESTONE | 业务/架构 rationale、exact 源码和候选哈希、分层测试及用户验收/未过门清单 |

门禁数据：[FINAL_E109_CANDIDATE_QA](../../release/FINAL_E109_CANDIDATE_QA.json)、
[auth-authority](../../release/auth-authority.json)、
[account-capabilities](../../release/account-capabilities.json)。
服务器今天是否改变：UNKNOWN；本轮未访问服务器，旧 dated UAT 不作今天的真值。
上述缺口不可推断已被 01–04 某个 dispatch 解决；只接纳后续正式 immutable receipt。

## 人工验收仍缺什么

真实新 issuer 登录与原账户数据连续性；真实收信/验证/重置；最终安装、启动、
强制更新、损坏恢复、同版本修复、取消/卸载/快捷方式；用户资料保持；最终使用体验。
已有 1.4.x 阶段包验证不替代 E109 1.5.0/1.5.1 最终候选。

## Authority pointers（不复制原回执）

本轮 [knowledge intake evidence](../governance/receipts/20260912-repository-knowledge-intake.json)
记录 Phase 0 与 exact 原交付复验；不包含本文件自身的最终提交 SHA。

MAIN2 freeze dispatch：`MF-GOV-MAIN2-CANONICAL-FREEZE-20260912-01`。
回执：`MAIN2_CANONICAL_FREEZE_RESULT.v1.json`，外部 Coordination archive，SHA-256
`d59f685a08d07422196262553272598396cd31af8745536f8849cf5290f55f8f`。
E109 registry：`E109_INTEGRATION_REGISTRY.json`，SHA-256
`2a1b0e022376b7479d838ae2a653f63b90728f7b48ec48281db870212bc52726`。
这些是历史审计指针，不要求网页端访问本机绝对路径；exact 源码中的专项合同可直接阅读。

56bcc 历史远端归 MAIN1_HISTORY；a198 包含排除的 21337c1 import ancestry，
QUARANTINED_NOT_CANONICAL；04faf 是保留的 MAIN2 source。不得从隔离分支 tip 导入旧 WIP。

## 文档冲突与阅读范围

- README 原先宣称登录已删除，与 E109 源码冲突。本轮只更正入口文案；历史主上下文仍是旧快照。
- [旧架构](../ARCHITECTURE.md) 只覆盖早期 local/web 路径；补读根架构和 Enterprise 合同。
- [Enterprise 架构证据](../enterprise/architecture.md) 的 NOT_RUN 与租约是其交付快照；
  不能用它否定后续正式运行回执，也不能凭配置文件存在宣称部署 PASS。
- [ADR-002](../adr/002-video-collection-v1.md) 描述目录/字幕能力；上传视频媒体支持不由该 ADR 推导。
- [旧 worktree layout](../project-management/WORKTREE_LAYOUT.md) 和 E1 01–08 规则只作历史证据；
  MAIN3 员工槽位/refs 规则由 [新 workflow](../governance/DEVELOPMENT_WORKFLOW.md) 约束。
- 旧 W05/W07 不自动成为新 01–04；新分工由架构师明确派发。
- 旧内嵌登录 receipt 是源码事实；长期系统浏览器要求以
  [auth contract](../E109_AUTH_ISSUER_MIGRATION.md) 为准，本轮不改登录实现。
- root GPT_WEB / READ_FIRST / HANDOFF 历史路径登记见
  [LEGACY registry](../handoff/README.md#legacy--superseded)，不作为新会话默认入口。

未来每次 accepted source、QA、部署或 promotion 变化时，更新相关状态与证据指针；
保留未验证项，生成新 handoff，不复制聊天记录。
