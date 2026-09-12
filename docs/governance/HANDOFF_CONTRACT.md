# Compact handoff contract v1

目标：可复制给新会话的 UTF-8 纯文本 ≤4096 bytes（按 bytes，不按字符计）。
长期 system of record 是已集成 repository；handoff 只增加当前会话差量。
先给 recipient 可访问的 repository/checkout，再给 exact current SHA。若知识库尚未合入
canonical，明确同时提供 baseline SHA 和 knowledge delivery HEAD，不能要求在旧树找新文件。

```text
PROJECT = MentorFlow
CANONICAL_BRANCH = main2
CANONICAL_SHA = <CURRENT_STATE exact baseline>
GENERATION = MAIN3
CURRENT_GIT_BRANCH = <branch or DETACHED>
CURRENT_GIT_HEAD = <exact checkout HEAD>
READ_FIRST = [AGENTS.md, ARCHITECTURE.md, docs/project/CURRENT_STATE.md,
 docs/exec-plans/active/MAIN3.md, docs/governance/ACTIVE_BOARD.json,
 docs/governance/DEPENDENCY_QUEUE.json]
SESSION_DELTA = <only this session's new result; no private data>
USER_ACTION_REQUIRED = <concise decision or NONE>
```

生成器还携带 baseline tree、product acceptance、active workers、UNKNOWN workers、
READY/BLOCKED/NOT_DISPATCHED task IDs。详细条件通过 queue 读取，不重复 receipt。
禁止重复完整历史/架构/员工规章；只保留 exact pointers 和 READ_FIRST。

## 本地生成

```text
python scripts/context/build_compact_handoff.py
python scripts/context/check_repo_knowledge.py
```

生成位置为 `artifacts/handoff/mentorflow-handoff.txt`，已忽略，不提交随 HEAD 改变的产物。
可用 `--session-delta "short public result"` 提供差量；仅从参数读取，不读取任意会话文件。
使用 `--check` 比较现有 handoff 与当前 Git/状态，不写文件；同样的差量需再次传入。
生成器不读取环境中的 credentials、不访问网络、不扫描用户目录、不调用其它 agent。
状态/链接/明显 secret 校验发生在写入前；超限直接失败，不截断、漏掉 blocker 或猜默认值。
文件路径越出 repo、symlink/reparse redirect、缺字段、重复 JSON key、状态不一致均拒绝。

## 小型 schema 的约定

ACTIVE_BOARD schema_version=1；workers 固定 A/01/02/03/04/B。
status = IDLE/RUNNING/READY/BLOCKED/SILENT，额外允许 UNKNOWN 作为明确的未知哨兵。
UNKNOWN 不被当作可派发、正在工作或交付完成。RUNNING/READY 必须有 dispatch_id/task_id。
dispatch_id/task_id/delivery_commit 可为 null，表示没有已绑定的正式值；字段本身不得省略。
delivery_commit 非空必须是完整 SHA；不写自引用的本提交 SHA，外部 immutable receipt 补齐。
baseline_branch/sha/tree 和 integration_branch 必须与 CURRENT_STATE 一致。

DEPENDENCY_QUEUE 的 state = READY/BLOCKED/NOT_DISPATCHED/DONE/UNKNOWN。
READY 只是可审查/可请求授权，不是自动派发；依赖必须是 queue 内任务并无环。
owner=UNASSIGNED 表示需架构师分派，不将历史 W05/W07 映射到新员工。
`user_action_required=null` 表示没有额外用户动作，非空仅存非敏感文字。
snapshot_kind 强调这是可提交快照；生成器不监控 worker 或验证远端 refs。

## 知识 lint 范围

检查四个地图的行数、READ_FIRST、board/queue schema、全 docs 的 Markdown 内部路径、
本轮知识文件与 session delta 的明显 secret 模式以及 handoff bytes/新鲜度。
Markdown fenced code 中的示例不作链接解析；锚点不影响路径存在性。
检查不会跟随链接读取 credentials，也不会请求外部网址。
轻量 SECRET_VALUE_COUNT=0 仅指受检文件未出现已列明显模式，不声称整个 Git 历史无秘密。

遇到冲突读 [CURRENT_STATE](../project/CURRENT_STATE.md)，修正 source/receipt 依据后再更新
状态文件；不能通过 handoff 文案覆盖正式 acceptance。完成后退出，保持 SILENT。
