# MAIN3 execution plan

MAIN3_BASE = main2

Current authority: `MF-MAIN3-A-GATEA-0036-UNBLOCK-20260913-02`。
integration/main3 是施工集成线；formal main3 = NOT_CREATED；main2 = FROZEN。
Batch02 完整 11-commit exact 历史按 02→03→04→01 已集成；其员工状态保留历史交付范围。
本轮 A 原办公室 Phase0 实测 VERIFIED，收口后 SILENT；不推断其它独立任务实时状态，无新 worktree 或员工派发。
source 映射及分层状态见 [board](../../governance/ACTIVE_BOARD.json) 与
[Batch02 receipt](../../governance/receipts/20260913-main3-batch02-integration.json)。

| 方向 | Batch02 已接受范围（历史） | 下一步门禁 |
| --- | --- | --- |
| Repository Knowledge / Web | COMPLETE_SOURCE_SCOPE；bridge CLOSED；Web 为派生导航 | 下一份正式 receipt 到达后更新状态 |
| Native login / verifier | PASS / integrated；本地 mTLS→Keycloak→session→bootstrap→main | Gate A 生产拓扑与签名 desktop 候选 |
| Action tokens | exact 69e68 独立确认；A 原生并发/重放/resend 复测 PASS | Gate B 实际部署共享 authority 与真实邮件 |
| Signed mandatory floor | PASS / integrated；历史 floor 不降，直接 target current latest | Gate C 最终候选增量/完整路径与恢复 |
| Update presentation | PASS / integrated；optional 可继续，mandatory 阻止 login/main，中文 reason | Gate C 原生安装与人工 UX QA |
| Account / edge / RLS | A 本地 nginx encoding/anti-enumeration PASS；04 native RLS PASS retained | Gate B 生产拓扑/public issuer/SMTP；A 未重跑 RLS |
| Interview + Acceptance | PENDING_MANDATORY | 最终用户验收后提供完整 bundle |
| Formal main3 promotion | BLOCKED / NOT_AUTHORIZED | B SILENT；后续 exact promotion authority |

## 0036 schema blocker — source resolved

只集成 `0d064974a145e758cdc8e2c8700eca7a13576157`，不重放 Batch02 04 commits。
0036 authoritative migration 与 expected head 已恢复；A 本地 PostgreSQL 18/18 加聚焦回归通过。
本轮 ledger RLS 是新增聚焦执行证据；上表及 Batch02 广域 RLS 仍保留原执行者范围。
Gate A schema blocker = RESOLVED_IN_SOURCE；REAL DEPLOYMENT = NOT_RUN AFTER FIX。
[专项 receipt](../../governance/receipts/20260913-gatea-schema-0036-unblock.json) 绑定 exact source 和检查。
原生身份与更新长期 authority 保持；real SMTP 由独立 04 任务推进，无 Batch03 或其它 source intake。

## Gate A — Native auth production activation

修复后 NOT_RUN / NOT_DISPATCHED by this task；source blocker 已解除，Gate A 本身尚未验收：

1. Qualify exact Keycloak SPI、required actions/MFA、private backend-only mTLS 与可信代理/TLS。
2. 明确实际 backend session/rate-limit 单进程或共享状态拓扑、revocation latency 与长期主窗口 renewal。
3. Build/deploy 兼容 backend/SPI；build/install 同一 source 的 native desktop 候选。
4. 正常 UX：原生用户名/密码 → verifier → Keycloak → session → workspace bootstrap → main。
   验证错误/未知账号一致拒绝、restore/logout、旧 issuer+subject membership 连续性；禁止浏览器回退、password/direct grant、密码存储。
5. 若需 issuer/hostname cutover，按 legacy 基础设施合同另行批准有界共享窗口并先验旧账户连续性；本轮不写日期。

[Native auth ADR](../../ADR_NATIVE_CREDENTIAL_AUTH_V1.md) 拥有正常 UX；
Batch01/03d979 System Browser 正常体验已 superseded，旧协议保留审计范围。

## Gate B — Account security and real email

最终生产账户 gate 尚未验收；真实 SMTP 按独立 04 任务推进，本 schema 任务不覆盖其状态。
保留 04 原生 PostgreSQL RLS 120 probes/control 与独立 nonprivileged-login smoke PASS；
保留 exact 69e68 action-token 独立确认和 A 本地原生 2/5/10 并发、resend、replay、cross-purpose PASS。
Batch02 广域 RLS 不改写为 A 执行；本轮只新增 0036 ledger 聚焦原生证据。

后续补实际集群 single-use/revocation authority、生产连接池/tenant isolation、public issuer
及真实 SMTP verify/reset 端到端。Batch02 的 SMTP send=0 / NOT_RUN 保留历史范围；
独立 04 任务当前 SMTP 结果须以其正式回执更新，本 schema 任务未执行。
register/forgot_password 均为 false，native account-action 产品入口须另行接受才能启用。

## Gate C — Update final candidate

NOT_RUN / NOT_DISPATCHED。授权后构建一个 exact integrated signed installer/updater 候选，
绑定 source/tree/artifact hashes，验收 1.5.0→1.5.1、incremental/latest 或 full/latest、
UAC/apply/restart/health、canonical recovery/rollback、失败时旧版本可启动与用户资料保持。

OPTIONAL 可稍后、继续登录/使用、更新重启或退出时更新。MANDATORY 同时封锁 login/main，
不可 skip/cancel/network bypass，直接最新版本或退出。current 1.0、历史 mandatory 1.2、
latest optional 1.4 → MANDATORY target 1.4；reason 只解释，不拥有策略。
同时验收 parent semantics、progress、completion-X、脱敏错误和 shortcut/autostart 默认 OFF。
REAL_INSTALL_QA=NOT_RUN；FINAL 1.5.0→1.5.1=NOT_RUN。inert/offscreen 不替代实际安装。

## 最终里程碑与执行边界

三个 gate 完成后合并同一最终候选的包级和用户验收。PRODUCT ACCEPTANCE 当前 PENDING。
Interview + Acceptance 必交：业务链→代码→测试、架构/ADR rationale、exact source/tree/
candidate hashes；source、synthetic、本地 native、独立 retained、生产、package/user 证据分列。
[历史 engineering evidence](../../enterprise/INTERVIEW_ENGINEERING_EVIDENCE.md) 可复用 rationale。
之后才可申请 B 的正式 main3/default promotion，本轮不执行。

状态由 [CURRENT_STATE](../../project/CURRENT_STATE.md)、[queue](../../governance/DEPENDENCY_QUEUE.json)
和 [workflow](../../governance/DEVELOPMENT_WORKFLOW.md) 管理。
本轮发布权限仅为聚焦和知识门禁通过后的 origin/integration/main3 普通 push；执行范围为隔离 PostgreSQL schema fixture。
LIVE_PRODUCTION_LOGIN=NOT_RUN；PRODUCTION_KEYCLOAK_MUTATION=0；无真实 SMTP/安装、manual Actions、tags、force push、main2 mutation 或 formal main3。
