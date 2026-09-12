# MAIN3 development workflow

Authority: `MF-MAIN3-A-REPOSITORY-KNOWLEDGE-BOOTSTRAP-20260912-01`，2026-09-12。
该 dispatch 冻结新 generation 模型，并临时允许 A 做本轮 documentation/context tooling。
本文件描述治理规则，不宣称对应 branch/worktree/default 已经创建或切换。

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

## 一次交付

1. 明确 dispatch/task、exact source SHA/tree、owned/forbidden paths、依赖和验收门。
2. Worker 在授权隔离路径实现，运行相关检查，保留 NOT_RUN/PENDING。
3. 冻结 ordered exact commits、parent/tree、receipt hash、scope、测试和已知风险。
4. A audit → exact apply → registration → focused/combined gates → clean gate。
5. 成功记录 MERGED；失败给出原 delivery 的 WHY / FILES / FIX_SCOPE / TESTS / DO_NOT_TOUCH。
6. 修复只限原范围；新的功能或员工改派交架构师决定。

不得 merge branch tip；不得以 CI 绿灯、源码存在或单包 PASS 冒充真实环境/最终候选验收。
一个 delivery 等待不会阻止其它独立且具 authority 的交付；依赖未满足只挂起该项。
安全/数据/签名/tenant 边界测试按风险选取，不因小改动默认重跑全产品。
GitHub Actions / push / server mutation 必须有任务授权，参见
[Actions policy](../github-actions-compute-policy.md)。

## Silent 和状态更新

没有可执行任务即 SILENT，禁止 polling、wait threads、定时状态聊天和长期等员工。
新正式 receipt 由用户或控制面提供后，才恢复可执行 review。B 不作为常驻后台 agent。
[ACTIVE_BOARD](ACTIVE_BOARD.json) 是事件驱动的提交快照，不是实时进程监控。
无正式证据的状态用 UNKNOWN；禁止凭旧会话填 RUNNING/READY。
更新 board/queue 的责任在 A/获授权的治理任务；worker 回执本身不是改派权限。

## Promotion

Worker deliveries → A integration → QA/user acceptance → exact promotion candidate SHA/tree
→ B 按批准创建 main(N+1) → GitHub default switch → 旧 main 保持冻结。
Promotion 的 branch/ref/default 操作由 B 执行，A 的 source delivery 不等于 promotion。
本轮只产生 detached documentation commit；不操作 primary repo/main2/remote/default。

## 长期知识维护

长期事实进入结构化 docs；当前计划进入 exec-plans；调度进入 board/queue；
历史证据只给 exact SHA/receipt pointer；临时对话、截图、大日志与原始回执不进知识库。
规范文本优先复用原合同，只有根地图和 CURRENT_STATE 给出范围/冲突标记。
完成的计划移入 [completed](../exec-plans/completed/) 时保留结果、范围及证据指针。
修改接口/行为的 delivery 同时更新受影响索引；不要复制整篇 architecture。

## Secrets 与回执

只登记 private secret store 或 resolver contract，不提交 secret 值或个人隐私。
Provider/SMTP、SSH、token、用户密码、私有证书和签名私钥均在 repo 外。
扫描发现疑似值只输出文件/行号，避免在工具错误和报告中再泄漏值。
Knowledge lint 是轻量明显模式检查，不是整个历史的认证安全审计。

回执至少给出 base、exact commit/tree、scope、检查及限制；机器快照不得复制 raw receipts。
未嵌入当前提交自身 SHA 的 board `delivery_commit=null` 是显式未绑定：由外部 delivery
receipt 给出 exact SHA，Integration 后的下一次状态提交才填写，避免自引用哈希循环。
如何交接见 [HANDOFF_CONTRACT](HANDOFF_CONTRACT.md)。
