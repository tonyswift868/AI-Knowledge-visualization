# MAIN3 execution plan

MAIN3_BASE = main2

Authority: MF-MAIN3-A-WORKER-BATCH01-INTEGRATION-20260912-03。
integration/main3 是施工集成线，formal main3 = NOT_CREATED，main2 = FROZEN。
Batch01 exact 02→03→01→04 已集成；A 完成后 SILENT，01–04/B 均 SILENT；
所有 Phase0 isolation VERIFIED。Receipt-derived states 见 [board](../../governance/ACTIVE_BOARD.json)。

| 方向 | STATUS | OWNER / 下一步 |
| --- | --- | --- |
| Repository Knowledge / Web | COMPLETE_SOURCE_SCOPE；bridge CLOSED | A，更新 exact source snapshot 与分层状态 |
| Auth consumer source | ACCEPTED；LIVE_ACTIVATION_PENDING | 01 source 已接受；Gate A 另行授权 |
| Canonical identity recovery | SOURCE_REPAIR_ACCEPTED；LIVE_INSTALL_QA_PENDING | 02 source 已接受；Gate C 原生验证 |
| Installer/update UX | SOURCE_UX_CLOSEOUT_ACCEPTED；REAL_INSTALL_QA_PENDING | 03 source 已接受；Gate C 最终候选 |
| Account/email security | SOURCE_FIX_ACCEPTED；SECURITY_PARTIAL_PENDING | 04 source 可集成，Gate B 安全验收未完成 |
| Interview + Acceptance bundle | PENDING_MANDATORY | A assembly；最终用户验收后提供完整 bundle |
| Formal main3 promotion | BLOCKED / NOT_AUTHORIZED | B SILENT；仅后续明确 exact promotion authority |

## Gate A — Auth live activation

仅规划，NOT_RUN / NOT_DISPATCHED。需明确 service writer 和执行授权：
1. Build/deploy 兼容 backend；build/install 兼容 desktop。
2. 复核 authority、TLS、签名及旧账户 identity namespace。
3. 明确批准双方共享的 <=48h migration window；本轮不写日期。
4. 旧 issuer login/refresh/account continuity 实测通过。
5. 之后才 hostname cutover；实测 NEW issuer System Browser code+PKCE、动态 IPv4 callback、bootstrap。

## Gate B — Account security

仅规划，NOT_RUN / NOT_DISPATCHED；public issuer 与 SMTP 阶段依赖 Gate A：
native PostgreSQL RLS（独立租户及连接池 fail-closed）；真实/public issuer；
verification/reset token expiry、wrong/replayed/concurrent token、严格单次使用；
runtime anti-enumeration 与 rate limit；真实 SMTP verification/reset。
04 的 offline preflight 与 source 修复不能替代这些证据。register/forgot_password 均保持 false。

## Gate C — Update final candidate

仅规划，NOT_RUN / NOT_DISPATCHED。授权后构建一个 integrated installer/updater 候选，
绑定 exact source/tree、签名和 artifact hashes，验收 exact 1.5.0→1.5.1：
incremental path、UAC/apply/restart/health、canonical recovery、rollback，失败后旧版本仍可启动。
同时验证 parent semantics、progress/cancel、completion-X、raw-error masking、
shortcut/autostart 默认 OFF、pre/post-login repair 及 optional/mandatory update。
Inert/offscreen 与编译检查不能声称真实安装完成。

## 最终里程碑

三个 gate 完成后，合并一个最终候选的包级与用户验收证据；PRODUCT ACCEPTANCE 当前 PENDING。
Interview + Acceptance bundle 必交：业务链→代码→测试、架构/ADR rationale、exact source/tree/
candidate hashes，source/offline/live/package/user 证据分列，剩余缺口和明确验收结论。
[历史 engineering evidence](../../enterprise/INTERVIEW_ENGINEERING_EVIDENCE.md) 可复用 rationale，
不替代最终 bundle。然后才可申请 B 的正式 main3/default promotion；本轮禁止该操作。

状态与下一步由 [CURRENT_STATE](../../project/CURRENT_STATE.md)、
[queue](../../governance/DEPENDENCY_QUEUE.json) 和 [workflow](../../governance/DEVELOPMENT_WORKFLOW.md) 管理。
本轮唯一发布权限是通过门禁后的 origin/integration/main3 普通 push。
无 live deploy、SMTP、native QA、manual Actions 或 worker 派发授权。
