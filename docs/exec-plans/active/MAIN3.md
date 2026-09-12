# MAIN3 execution plan

MAIN3_BASE = main2

Authority: MF-MAIN3-A-REPOSITORY-KNOWLEDGE-BOOTSTRAP-20260912-01。
本计划是方向和依赖，不是给新 01–04 派发任务。
源码基线与当前未通过的产品门见 [CURRENT_STATE](../../project/CURRENT_STATE.md)。

| 方向 | STATUS | OWNER | DEPENDENCY | ACCEPTANCE |
| --- | --- | --- | --- | --- |
| Repository knowledge system | READY_FOR_INTEGRATION | A，本轮临时文档授权 | MAIN2 exact tree；后续 exact intake | 地图/结构化状态、lint PASS、≤4 KiB handoff、纯文档/tooling commit |
| Auth issuer / email account closure | PENDING_LIVE_ACCEPTANCE | 新槽位 UNKNOWN，架构师分配 | 兼容消费者部署、旧登录、same signing authority、迁移窗口、真实 SMTP | 新 issuer 登录、账户连续性、真实 verify/reset；之后才启用 capabilities |
| Update/install final QA | PENDING_E109_FINAL_QA | 新槽位 UNKNOWN；A 最终集成 | identity repair 的正式交付、最终唯一候选 | mandatory update / bad target recovery / identity recovery / same-version repair；数据保留 |
| Final user candidate | BLOCKED_ON_ACCEPTANCE_INPUTS | A assembly；QA owner UNKNOWN；user acceptance | 前两项合格输入及新打包授权 | 一个精确签名候选、原生安装/账户/更新操作与人工验收 |
| Governance cleanup / promotion | PENDING_AUTHORITY | B ON-DEMAND，A 提供候选 | QA/user acceptance + exact promotion authority | B 创建 main3/default 操作；旧 main 冻结；board/queue 更新 |

这些方向可由架构师拆成独立 bounded tasks；本文件不授予部署、邮件、push、Actions 或打包权限。
无可执行工作时 SILENT；不轮询其他 worker 或长期等待。

## 判断完成

- source、offline checks、live environment、packaged QA、user acceptance 分列证据。
- 已知恢复缺陷和真实邮件未完成前，不因文档和配置齐备宣称产品 PASS。
- Promotion 由 [workflow](../../governance/DEVELOPMENT_WORKFLOW.md) 管理。
- 本代完成后归档计划到 [completed](../completed/)，保留小型结果与 exact pointers。

事件驱动依赖见 [queue](../../governance/DEPENDENCY_QUEUE.json)，实际分派快照见
[board](../../governance/ACTIVE_BOARD.json)。
