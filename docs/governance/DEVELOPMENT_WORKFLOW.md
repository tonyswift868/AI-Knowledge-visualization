# MAIN3 development workflow

Authority: `MF-MAIN3-A-REPOSITORY-KNOWLEDGE-BOOTSTRAP-20260912-01`，2026-09-12；
知识补充授权 `MF-MAIN3-A-KNOWLEDGE-INTEGRATION-WEB-HANDOFF-20260912-02`；
后续 Batch 授权及结果以 CURRENT_STATE / exact receipts 为准。
治理规则与实际 ref/worktree 状态分离；后者不得由旧会话或历史报告推断。

## Roles and refs

| Authority | 边界 |
| --- | --- |
| Architect / user | 产品路线、新任务、员工域、验收和 promotion 授权 |
| A | exact delivery review/integration、兼容性、共享边界与最终 assembly；独占 integration |
| 01–04 | 已派发任务内实现/修复/测试；禁止跨员工直接通信及修改其他 worker/integration |
| B | ON-DEMAND ONLY：branch/worktree/default/promotion/ref governance |

`main1`、`main2`、`mainN` 为冻结的 stable/canonical source baseline。
`integration/main(N+1)` 仅供 A 集成；`worker/main(N+1)/01..04` 供被分配的 worker 开发。
`mainN` 的源码冻结不自动证明它获得最终产品验收；请核对 CURRENT_STATE。
01–04 禁止 refs/tag/repository-config mutation，不创建/切换其他办公室或依赖他人脏树。
旧 E1 01–08 及旧 branch 命名是历史信息，不自动映射新槽位。

## Delivery outcome，不是固定食谱

派工只需把当前任务的**目标、权威输入、owned/forbidden 范围、不可破坏的不变量、完成条件和必须保留的证据**写清楚。除非安全/发布流程确实要求，不要把 worker 锁进几十个固定 PHASE；让模型在这些边界内自行选择最短实现路径。

一个 delivery 被认为可交付，至少应满足：

- 基于给定 exact source/branch，在授权范围内完成请求结果；
- 运行与改动风险匹配的最小有意义验证，修复由本次改动引入的问题；
- 回执冻结 ordered exact commit(s)、tree、scope、验证结果和仍未运行的更高层 gate；
- source / fixture / packaged / live / final acceptance 分层准确，不升级证据等级；
- 工作区 clean，且没有越权修改 shared refs、其他 worker 或用户真实数据。

A 在集成时审 exact ancestry 和 ownership，处理必要的 integration-only wiring，再运行受影响组合门禁。不得 merge branch tip；不得以 CI 绿灯、源码存在或单包 PASS 冒充真实环境/最终候选验收。

一个 delivery 等待不会阻止其它独立且具 authority 的交付；依赖未满足只挂起该项。无新证据时不要机械重复已经通过且不受后续改动影响的检查。
GitHub Actions / push / server mutation 必须有任务授权，参见 [Actions policy](../github-actions-compute-policy.md)。

## 自主推进与停止条件

- 已授权的可逆本地实现、构建、聚焦测试和诊断直接完成，不因为存在多个合理实现细节就反复请求批准。
- 只有生产/共享环境写入、真实用户数据删除/覆盖/迁移、权限或密钥变更、发布/promotion、付费/不可逆动作、明显扩 scope，或会改变产品语义且无法安全推断的决定，才停下请求 authority。
- 发现失败时先寻找**第一个行为分叉点**，优先做最小 differential diagnosis，而不是轮流替换外围组件。能证明 source-vs-packaged、old-vs-new、A-vs-B 差异时，先锁定差异再返工。
- 若新证据显示 blocker 属于其他 owner，给出精确 evidence + likely owner 后结束，不顺手跨域大改。

## Silent 和状态更新

没有可执行任务即 SILENT，禁止 polling、wait threads、定时状态聊天和长期等员工。
新正式 receipt 由用户或控制面提供后，才恢复可执行 review。B 不作为常驻后台 agent。
[ACTIVE_BOARD](ACTIVE_BOARD.json) 是事件驱动的提交快照，不是实时进程监控。
无正式证据的状态用 UNKNOWN；禁止凭旧会话填 RUNNING/READY。
更新 board/queue 的责任在 A/获授权的治理任务；worker 回执本身不是改派权限。

## Promotion

Worker deliveries → A integration → QA/user acceptance → Interview + Acceptance bundle
→ exact promotion candidate SHA/tree
→ B 按批准创建 main(N+1) → GitHub default switch → 旧 main 保持冻结。
Promotion 的 branch/ref/default 操作由 B 执行，A 的 source delivery 不等于 promotion。
发布前只读审 workflow；任何自动 hosted compute 入口风险必须阻止发布。普通 source integration 授权不自动包含 main2/main1/default/tags/worker refs 或 Actions 执行。

## Open-source-first：只在决策点调查一次

当任务是在**新增或替换通用能力**时（解析、迁移、同步、调度、认证组件、打包/更新基础设施等），先检查仓库现有实现和 accepted ADR；若尚无已接受方案，再做一次有边界的成熟开源方案评估，比较适配性、维护状态、许可、迁移成本和可验证性，优先复用/适配而不是重造。

不要把“open-source-first”变成每次 bug fix 的固定联网步骤：普通 bug、小 UI 调整、已接受架构内的实现缺陷不需要重复搜索生态。若已有 accepted decision，只有新证据足以改变结论时才重开评估并用新的 ADR supersede；不要口头推翻旧决定。

产品特有状态机继续遵守 [产品流程](../PRODUCT_FLOW.md)，业务改动须能从 [component map](../project/COMPONENT_MAP.md) 追到代码和测试。

## 用户 QA 与临时环境生命周期

用户 QA 开始前先说明将发生什么、用户是否需要操作和成功标准。不要无预告或重复弹出安装器、UAC、浏览器。正常登录使用原生用户名/密码，不启动外部浏览器；显式 legacy 协议测试不改变该产品 authority。
不改主机 DPI，不让用户反复填写已有配置；按现有授权从受控 resolver 读取配置。

若某任务建立的临时环境仍被后续独立 QA 消费，必须在回执中显式记录 consumer/lease；此时不得因“本任务完成”自动清理服务、证书或数据库。清理必须等 consumer 完成或收到独立 cleanup 授权。没有后续 consumer 时仍应及时停止进程并清理临时 secrets。

CI 遵守 [现有 compute policy](../github-actions-compute-policy.md)：本地/自有 VPS 优先，不新增 push / pull_request / schedule 的 GitHub-hosted 自动消耗。
Staging 分域 writer 和恢复责任见 [staging contract](../enterprise/staging-orchestrator.md)。

## 长期知识维护

长期事实进入结构化 docs；当前计划进入 exec-plans；调度进入 board/queue；历史证据只给 exact SHA/receipt pointer；临时对话、截图、大日志与原始回执不进知识库。
规范文本优先复用原合同，只有根地图和 CURRENT_STATE 给出范围/冲突标记。
完成的计划移入 [completed](../exec-plans/completed/) 时保留结果、范围及证据指针。
修改接口/行为的 delivery 同时更新受影响索引；不要复制整篇 architecture。

只有 authority、依赖或长期合同真的变化时才刷新知识；不要把知识刷新当每个 worker 小任务的固定尾声。

## Secrets 与回执

只登记 private secret store / resolver 在哪里、谁拥有、如何在授权下读取；不登记值。
使用 [provider secret governance](../enterprise/provider-secret-governance.md) 的现有合同，不能为了新会话接手复制本机配置、原始聊天或个人隐私。
Provider/SMTP、SSH、token、用户密码、私有证书和签名私钥均在 repo 外。
扫描发现疑似值只输出文件/行号，避免在工具错误和报告中再泄漏值。
Knowledge lint 是轻量明显模式检查，不是整个历史的认证安全审计。

回执优先短而精确：base、exact commit/tree、scope、实际验证、未运行门禁、真实 blocker。机器快照不得复制 raw receipts。
未嵌入当前提交自身 SHA 的 board `delivery_commit=null` 是显式未绑定：由外部 delivery receipt 给出 exact SHA，Integration 后的下一次状态提交才填写，避免自引用哈希循环。
如何交接见 [HANDOFF_CONTRACT](HANDOFF_CONTRACT.md)。
