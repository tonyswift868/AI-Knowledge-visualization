# MentorFlow — agent guidance

MentorFlow 是 Windows 桌面学习系统，组合资料证据、知识地图、计划与教师流程；账户、云端数据、安装与更新链各有独立安全边界。

## 按任务读取，不做固定预读

先核对任务给定的 SHA、当前 HEAD、工作区和 owned paths。只读取当前任务真正需要的资料：

- 服务边界、数据流、信任边界变化：`ARCHITECTURE.md`。
- 当前源码/验收状态：`docs/project/CURRENT_STATE.md`。
- MAIN3 集成、调度或依赖：`docs/exec-plans/active/MAIN3.md`、`docs/governance/ACTIVE_BOARD.json`、`docs/governance/DEPENDENCY_QUEUE.json`。
- Git、worker、promotion：`docs/governance/DEVELOPMENT_WORKFLOW.md`。
- 正常原生登录：`docs/ADR_NATIVE_CREDENTIAL_AUTH_V1.md`。
- E109 安装、更新、UI 接线：`docs/E109_INTEGRATION_MAP.md`、相关 accepted decision。
- 长期产品闭环：`docs/PRODUCT_FLOW.md`；冻结决定见 `docs/decisions/index.md`。
- 网页会话接手：`docs/handoff/README.md`、`docs/handoff/WEB_SESSION_LATEST.md`。

不要为了小修复预读整套仓库地图、历史 handoff 或无关报告。需要时再展开。未知状态写 `UNKNOWN`；分支名、时间戳、clean worktree 都不能单独证明 canonical 或验收通过。

## 自主执行边界

- 用户只要求回答、解释、审计、诊断或计划时：读取必要材料并报告，不擅自实现产品改动。
- 用户要求修复、实现、构建或更新时：在已授权 branch/worktree 和 owned paths 内直接完成可逆本地修改，并运行与改动匹配的非破坏验证；不要为这些步骤反复请求批准。
- 只有生产/共享环境写入、删除或覆盖用户数据、权限/密钥变更、发布、付费、不可逆操作、明显扩 scope，或无法安全推断且会改变产品结果的决定，才停下请求 authority。
- 任务可安全拆成互不重叠的只读调查、测试或独立实现时可使用子 Agent；不要为了“保持并发”强制委派。主 worker 保留最终写入和交付责任。
- 被其他 worker/外部门禁阻塞时给出精确 blocker 后结束；不 polling、不 wait threads、不长期占用会话。

## 完成标准与验证

默认把任务持续做到可交付，而不是第一版实现就停：实现请求的结果，运行最窄但有意义的验证，修复由本次改动引入的问题，再确认最终状态。

- 从受影响范围的 focused checks 开始；只有依赖面、失败证据或回归风险需要时才扩大测试。
- 已通过且后续改动不可能影响的检查不要机械重跑；不要把每个小改动升级成全仓库回归。
- 明确区分 source、fixture、packaged、live、final acceptance；不能把较低层 PASS 冒充更高层验收。
- 回执优先给 exact base/commit/tree、实际验证、未运行门禁和真实 blocker；不要重复背景或把 UNKNOWN 猜成结论。

## 核心产品不变量

- SOURCE_BASELINE 不等于 PRODUCT_ACCEPTANCE。
- 学习资料和账户数据在程序内容之外；升级、修复、卸载不得默认删除用户数据。真实迁移/覆盖必须有独立授权与可回滚证据。
- 正常登录：原生用户名/密码 → MentorFlow Backend → backend-only authenticated verifier → Keycloak → session → workspace bootstrap → main；不使用外部浏览器、password grant 或 Keycloak direct grant。
- 密码、token、SMTP/API secret、SSH/private signing key、真实用户隐私不得提交、记录或进入 handoff；只记录安全 resolver/reference。
- 更新核心拥有签名校验、mandatory floor、事务、健康检查和 rollback；UI 通过 accepted adapter 消费，不另造 authority。
- 共享 Actor/Tenant/RLS/identity authority 复用 accepted contract；客户端显示不是授权来源，失败时 fail closed。
- projection/workbench/handoff 可重建，不反向成为 truth authority。

## Git / worker

- `mainN` 冻结；A 独占 `integration/main(N+1)`；B 仅在明确需要 refs/worktrees/default/promotion 时启用。
- 01–04 只修改各自任务范围和 branch，不直接跨员工通信，不修改别人的 branch 或 integration。
- 集成只消费有 authority 的 exact commits/ancestry；禁止用 branch tip 猜交付。
- 历史 handoff、dated report 和 retained fixture 是证据，不是当前实时状态。

## Open-source-first，但不要重复搜索

当任务是在**新增或替换通用能力**（解析、迁移、同步、调度、认证组件、打包/更新基础设施等）时：先检查仓库已有实现和 accepted ADR；若尚无已接受方案，再评估成熟、许可兼容的开源方案，优先复用/适配而不是重造。

不要为普通 bug、小 UI 改动或已有 accepted decision 每次重新做开源调研。只有新证据足以改变既有决定时，才提出新的 ADR/supersede。

## Prompt / Skill 卫生（适配 GPT-6 Astra）

- 任务提示词优先写：**目标、已有 authority/输入、硬边界、完成条件、必须保留的证据**。不要把 standing rules 重复粘贴成几十步 itinerary；实现路径让模型在边界内自行判断。
- 只有真实测得的薄弱点才增加示例或额外步骤，不为旧模型习惯持续堆脚手架。
- 若仓库新增 Skill：description 只写清“做什么 + 何时触发”，避免“任何相关任务都用”；`SKILL.md` 做最小路由器，细节放 references/scripts，按需加载。不要为一次性任务新建 Skill。
- 安全边界、业务不变量、权限和 source-of-truth 规则保留；模型能力提升不是删除这些约束的理由。

## 知识与交接

只有任务确实改变当前 authority、依赖或长期合同，才更新知识/交接；不要把知识刷新当成每个任务的固定仪式。需要刷新时使用仓库生成器与检查器：

```text
python scripts/context/build_compact_handoff.py
python scripts/context/check_repo_knowledge.py
python scripts/context/build_compact_handoff.py --web --check
```

任务特定授权优先于通用建议；发现与 accepted decision 冲突时，不口头覆盖旧决定，记录新证据并走相应 authority/ADR。
