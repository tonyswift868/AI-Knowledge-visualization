# GOV-001 — MAIN3 repository knowledge and generation governance

Status: ACCEPTED_BY_USER_DISPATCH；Date: 2026-09-12。
Authority: MF-MAIN3-A-REPOSITORY-KNOWLEDGE-BOOTSTRAP-20260912-01。
Applicable source baseline: 04faf2a9fbb50c5f9403dad5abf46354c9b53d82。
这冻结的是规则，不是分支已创建、源码已消费或产品已完成的回执。

## 已确定的决定

1. mainN 是冻结基线；A 独占下一代 integration，B 按需执行 ref/default/promotion。
2. Repository 是长期项目知识的 system of record；事实、计划、调度、证据指针分层。
3. AGENTS 是 ≤120 行地图；按 READ_FIRST 逐层读取，避免重复大型历史接手包。
4. 只凭正式 authority 消费 ordered exact commits，保留 parent/tree/scope/test evidence。
5. Worker 无任务即 SILENT；禁止跨员工通信、polling 和 wait threads。
6. Secrets/私钥/真实用户隐私在 repo 外，只引用 store/resolver contract。

## 结果与限制

Handoff ≤4096 UTF-8 bytes；当前快照 UNKNOWN 必须保留，不把文档变成实时调度器。
本轮只授权 A 的 documentation/context tooling 例外；后续 Worker 新任务仍由架构师决定。
详细规则：[workflow](../governance/DEVELOPMENT_WORKFLOW.md)、
[handoff](../governance/HANDOFF_CONTRACT.md)。
