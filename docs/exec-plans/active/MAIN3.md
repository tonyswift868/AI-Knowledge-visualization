# MAIN3 execution plan

MAIN3_BASE = main2

Authority: MF-MAIN3-A-KNOWLEDGE-INTEGRATION-WEB-HANDOFF-20260912-02。
B06 DONE；用户已确认 branch picker 六个相关 ref。01–04 新 dispatch 已发布，
详细分工和 Phase 0 receipt 未随本任务提供；本计划不重复派发或猜测槽位映射。
源码基线与当前未通过的产品门见 [CURRENT_STATE](../../project/CURRENT_STATE.md)。

| 方向 | STATUS | OWNER | DEPENDENCY | ACCEPTANCE |
| --- | --- | --- | --- | --- |
| Repository knowledge + web handoff | COMPLETE_SOURCE_SCOPE | A | exact 195fb241 delivery 已集成；12 项长期 authority | lint/links/JSON/bad-input/secret scope PASS；两种 handoff 各 ≤4096 bytes；远端 exact receipt |
| Windows System Browser login | REQUIREMENT_GAP | 新槽位映射 UNKNOWN | 后续正式 source receipt 和实现授权 | System Browser code+PKCE、动态 IPv4 callback、三个 authority 分离、bootstrap 后进入主界面 |
| Auth issuer / email account closure | PENDING_LIVE_ACCEPTANCE | 新槽位 UNKNOWN，架构师分配 | 兼容消费者部署、旧登录、same signing authority、迁移窗口、真实 SMTP | 新 issuer 登录、账户连续性、真实 verify/reset；之后才启用 capabilities |
| Update/install final QA | PENDING_E109_FINAL_QA | 新槽位 UNKNOWN；A 最终集成 | identity repair 的正式交付、最终唯一候选 | mandatory update / bad target recovery / identity recovery / same-version repair；数据保留 |
| Final user candidate | BLOCKED_ON_ACCEPTANCE_INPUTS | A assembly；QA owner UNKNOWN；user acceptance | auth/login/update 合格输入及新打包授权 | 一个精确签名候选、原生安装/账户/更新操作与人工验收 |
| Interview + Acceptance bundle | PENDING_MANDATORY | A assembly；各域提供正式证据 | 最终候选与 QA/user acceptance | exact SHA/tree/候选哈希；业务链→代码→测试；ADR/rationale；source/offline/live/package/user 分栏；剩余缺口与正式验收结论 |
| Governance cleanup / promotion | PENDING_AUTHORITY | B ON-DEMAND，当前 SILENT | QA/user acceptance + Interview/Acceptance bundle + exact promotion authority | B 创建 main3/default 操作；旧 main 冻结；board/queue 更新 |

产品方向须依正式 dispatch 执行；本轮仅授权知识维护和受审后的 origin/integration/main3
普通 push，不授予部署、邮件、Actions 或打包权限。A 在本轮检查/发布回执后 SILENT。
无可执行工作时 SILENT；不轮询其他 worker 或长期等待。

## 判断完成

- source、offline checks、live environment、packaged QA、user acceptance 分列证据。
- 已知恢复缺陷和真实邮件未完成前，不因文档和配置齐备宣称产品 PASS。
- Interview + Acceptance bundle 是必交大里程碑，不可因源码/单测 PASS 省略。
  [历史 interview evidence](../../enterprise/INTERVIEW_ENGINEERING_EVIDENCE.md) 可复用 rationale，
  其 PARTIAL 和旧员工状态不替代最终 bundle 的新证据。
- Promotion 由 [workflow](../../governance/DEVELOPMENT_WORKFLOW.md) 管理。
- 本代完成后归档计划到 [completed](../completed/)，保留小型结果与 exact pointers。

事件驱动依赖见 [queue](../../governance/DEPENDENCY_QUEUE.json)，实际分派快照见
[board](../../governance/ACTIVE_BOARD.json)。
