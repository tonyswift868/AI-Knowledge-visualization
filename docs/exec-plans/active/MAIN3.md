# MAIN3 execution plan

MAIN3_BASE = main2

Current authority: `MENTORFLOW_ACCOUNT_MVP_FINAL_INTEGRATION_AND_USER_TEST_BUILD`。
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

## 当前 Account MVP 收口

01 453e940、02 fba57de、04 3feb4b6 exact delta 已集成；03 已有 UI 复用，A 完成官方生命周期和 Workbench 最小接线。
最终 product source 405a3568；最新开发 Frozen 实测 admin/learner main、多账号、WinVault、session-first 与 saved-credential 自动登录 PASS。
官方客户端凭据 grant、测试公开邮件链接、官方 verify/UPDATE_PASSWORD 与 after-commit observer 已验证；用户 QQ 邮箱验收 PENDING。
[用户 A-F 清单](../../governance/ACCOUNT_MVP_USER_TEST.md) 和 [收口证据](../../governance/receipts/20260914-account-mvp-closeout.json) 是下一轮入口。
Workbench 最新开发版 CURRENT；最新已验收候选 NONE；旧 V3 FAIL 保留。环境至北京时间 2026-09-15 04:20:39，不自动清理。
只普通发布 integration/main3；A/B 恢复、Portable Pack、迁移和生产 promotion 不在本轮范围。

## Batch03 与跨设备连续性（历史执行）

八个新 exact commits 已按 02→03→01 集成，04 为 evidence only；不重放 Batch02。
新增密码规则／canonical reset completion、未激活 lifecycle/recovery UI、中文只读 Workbench。
冲突保持原生正常登录，重置成功只返回登录；source/native 回归与 live/product 验收分开。
[Batch03 receipt](../../governance/receipts/20260913-main3-batch03-integration.json) 记录 exact source 映射和组合验证。

[Cross-device authority](../../decisions/CROSS_DEVICE_DATA_CONTINUITY_V2.md) 为本代必需合同：
SERVER_SYNC_TIER 持久保存结构化用户状态；LOCAL_PORTABLE_TIER 携带大文件；
REBUILD_OR_REDOWNLOAD_TIER 可重建／可信重下载。旧设备离线不影响新设备云状态恢复。
登录后立即可见支持的计划／进度／历史；缺失资源标记 MISSING_ON_THIS_DEVICE，
用户导入经过账号／workspace／hash 校验的大包，stable resource ids 重连云状态。

- DATA_TARGET_STABILITY：BLOCKED；Temp 根不是最终目标，issuer namespace／subject 映射未稳定。
- SERVER_SYNC_REHYDRATION：待派发；逐实体存储／同步／恢复矩阵及旧设备离线测试。
- PORTABLE_PACK_V2：待派发；QUICK/FULL、可选模型、加密完整性与账号绑定、非覆盖幂等恢复。
- DATA_CLASSIFICATION_RECOMMENDATION：待派发；自动分类／推荐，只将真实歧义交用户。
- OLD_DATA_CONTINUITY：BLOCKED；以上门禁和双设备验收通过后，仍需独立真实迁移授权。

04 的 46 个 retained artifact hash 已核验；只接收归一化事实。84-space 私有选择器仅为 QA，
03 wizard 仍是无 IO／执行器的预览壳。MIGRATION_EXECUTED=NO；当前任务不要求用户作迁移选择。
历史 Batch03 时候选为 WAITING_FOR_ARTIFACT；当前 V2 CURRENT 与实测登录状态以下方最新候选段为准。
保留 register/forgot_password=false、main2 冻结和 formal main3 未创建。

## Batch04 — historical source closeout / then PARTIAL

Authority: `MF-MAIN3-A-BATCH04-INTEGRATION-20260913-01`；从 `1ef95a71...` 审计五项、集成四份 delta。
04 baseline merge 的 tree 与起点一致，不能重复消费。01→04→03→02 已组合；
[Batch04 receipt](../../governance/receipts/20260913-main3-batch04-integration.json) 绑定完整 parents/tree/paths 与测试证据。

- 01 source/fixture PASS：稳定 account ID、signed per-subject continuity、durable root；live mapping NOT_APPROVED，已有 Temp candidate 不可真实迁移。
- 04 V1 PLAN/PROGRESS/RESOURCE_METADATA 本地 native PG PASS；head/expected=0037，full sync/history PARTIAL。
- 03 source UX PASS；04 缺 03 的可信空账号／history、全局 revision、file/model 与多计划资源语义，不能猜测 adapter。
- 02 harness 隔离／hash／受控启动 fixture PASS；exact target 登记合同 BLOCKED，真实 A/B 云恢复 NOT_RUN。
- 17 dedicated migration skip 已补 native chain；bootstrap/pool RLS 补跑；剩余 Windows symlink privilege 明确计入 skip。
- [候选／接线缺口](../../dev_workbench/MAIN3_BATCH04_INTEGRATION_GAPS.md) 记录冻结构建、Backend 部署与登记合同边界。

下一阶段需明确 producer public contract 与 authenticated transport，完成 login/bootstrap→sync→UX→main 投影，
使缺资源不等于 sync failed/empty account；定义 authoritative scope revision、历史范围、缓存可信度、资源分类与多计划投影。
随后提供同版 Desktop+Backend exact target，解决 Workbench build SHA 与登记 HEAD 的循环约束，执行真实 A/B acceptance。
这不是派发，也不授权真实迁移／issuer cutover／账号 capability 激活。PORTABLE_PACK_V2 与真实旧数据迁移继续锁定。

## Frozen V3 — new Desktop local-service blocker

两个指定 delivery 已在当前远端 base；无重放，UNKNOWN_COMMITS=0。新 exact-source V3 已完成构建、原生认证负例及实际 Workbench A 登录。
点击“进入学习空间”后，在 `desktop/account_backend.py:36` 首先发生 `ModuleNotFoundError: app.main`；`NoneType.write` 是子进程无 stderr 的次生错误。
source 同函数导入成功；Frozen 前插 `_MEIPASS/backend` 选择 shadow app。只移除该路径的诊断可进入 canonical app.main，但仍有 knowledge-policy 资源闭包缺口；不是产品修复或 PASS。
LIKELY_OWNER=01；V3=FAIL；真实 A/B=BLOCKED_BEFORE_A_WRITE。A 已关闭，B 空目录已准备但未登录恢复；真实 sync-failure control 未运行。
TRUE_EMPTY 为真实 server-confirmed 支持范围 PASS；481 聚焦检查 PASS 不替代失败的 packaged main-startup gate。
后续需经过授权的 Desktop Frozen 本地服务/资源修复、新 exact 候选，再继续真实 A durable write → A offline → B stable-ID restore/missing-resource/control。
本轮不跨 owner 改源码或新派工；[阻塞证据](../../governance/receipts/20260914-real-ab-closeout-blocker.json) 可供下一次 exact delivery 使用。
验收 runtime 与密钥保留，AUTO_CLEANUP=FORBIDDEN；FULL_SERVER_SYNC=PARTIAL；Portable Pack V2=NOT_IMPLEMENTED；真实迁移/public issuer/账号 capability 未执行。

## 0036 schema blocker — source resolved（历史）

只集成 `0d064974a145e758cdc8e2c8700eca7a13576157`，不重放 Batch02 04 commits。
0036 authoritative migration 与 expected head 已恢复；A 本地 PostgreSQL 18/18 加聚焦回归通过。
本轮 ledger RLS 是新增聚焦执行证据；上表及 Batch02 广域 RLS 仍保留原执行者范围。
Gate A schema blocker = RESOLVED_IN_SOURCE；REAL DEPLOYMENT = NOT_RUN AFTER FIX。
[专项 receipt](../../governance/receipts/20260913-gatea-schema-0036-unblock.json) 绑定 exact source 和检查。
该历史专项仅恢复 0036；Batch04 已进至 0037。real SMTP 属独立 04 任务，本轮不接收其邮件或部署验收。

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
本轮发布权限为组合回归和知识门禁通过后的 origin/integration/main3 普通 push；原生测试仅隔离 fixture。
LIVE_PRODUCTION_LOGIN=NOT_RUN；PRODUCTION_KEYCLOAK_MUTATION=0；无真实 SMTP/安装、manual Actions、tags、force push、main2 mutation 或 formal main3。
