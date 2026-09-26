# 新设备云端恢复与本机资源缺口：UI/state 交付

DISPATCH_ID = MF-MAIN3-03-NEW-DEVICE-REHYDRATION-UX-20260913-07

TASK_ID = MENTORFLOW_NEW_DEVICE_CLOUD_REHYDRATION_AND_RESOURCE_GAP_UX_V1

STATUS = PASS_NEW_DEVICE_REHYDRATION_UX

## 工作区与依据

工作区：`E:/Codex/Data/worktrees/bb70/MentorFlow`；分支：`worker/main3/03`。
起始 HEAD：`6c6af25d15ce10254e71ca4388452ee51c8c36ec`；起始状态干净。
private index 位于 `.git/worktrees/MentorFlow3/index`，延续既有 managed worktree，未创建新工作树。

只读检查指定 authoritative integration `1ef95a71c5e7422cb3fe1435ccd703193629b591` 的
`docs/decisions/CROSS_DEVICE_DATA_CONTINUITY_V2.md`。该合同明确：全 SERVER_SYNC_TIER 协议与
Portable Pack V2 尚未完成；V1 不能替代 V2。未 checkout、合并或修改 integration/main3。

## 已接入与待接入边界

- **已接入现有产品 UI**：账号设置可见“导入 MentorFlow 迁移包”，默认禁用；能力和 readiness 即使报告 true，V2 执行器未实现仍禁止启用，无任何 V1 handler。生产登录 bootstrap 等待文案为“正在恢复你的学习空间…”。
- **新增可调用的原生 UI/state contract**：`RehydrationPage` 与 `RehydrationState`，供未来完整同步结果适配；同步完成、离线、失败、空账号和资源缺失有独立展示。
- **尚未接线**：现有 `main.py` bootstrap 未被转换成全量 `SYNC_COMPLETE`，新状态页未替换生产主窗口。现有部分 metadata/bootstrap、新设备旧恢复中心流程仍在原合同下运行。全量同步 producer、缓存可信性判定、下载与 V2 执行器、最终主窗口资源行接线由后续任务交付。
- 本次 PASS 仅覆盖明确授权的 UI/state 范围，**不宣称真实新设备登录后全量自动恢复已经端到端完成**。

## 行为

| 场景 | 用户展示和状态 |
| --- | --- |
| AUTH_SUCCESS / SYNCING | “正在恢复你的学习空间…”、不定进度；不能发出进入空间请求 |
| SYNC_COMPLETE | 仅接受同账号、当前同步尝试、服务端已确认的完整表示层投影；显示恢复摘要 |
| 真空账号 | 仅明确的 empty_account 且计划/资源/进度/复习/历史均为空，显示“这个账号还没有学习计划。” |
| SYNC_OFFLINE | “暂时无法连接到云端学习数据。”，无数据丢失或无计划结论 |
| SYNC_FAILED | “学习空间暂时未能恢复，请重试。”，不展示成功或空账号 |
| 可信缓存 | 离线/失败时仅同账号、trusted_cache=true 且有 last_sync 的投影可展示，明确标记缓存和时间 |
| PDF 缺失 | “这份资料还没有下载到这台设备。”；计划和进度仍显示，资源仍嵌在所属计划下 |
| 模型缺失 | “模型尚未安装在这台设备。”；不改变同步完成状态，不隐藏进度 |
| 已验证重下载 | 显示重新下载；点击只发出稳定资源 ID 的用户意图并说明尚未下载，无 URL/下载执行 |
| 仅迁移包 | 展示“从迁移包恢复”但当前禁用；模型不支持此恢复方式时不提供可执行按钮 |
| 两者都不可用 | 模型明确提示“需要从旧设备迁移包恢复。”；不删除或隐藏模型元数据 |
| 稍后 | 只记录内存 UI 选择；保留计划、进度、资源 ID 和原绑定 |
| 技术详情 | 默认收起；纯文本只读展示同步修订、资源稳定 ID 和 MISSING_ON_THIS_DEVICE |

## 适配合同

这是表示层合同 `mentorflow.rehydration-ui.v1`，不是新 server API，也不取代 canonical sync backend。

调用顺序：

1. 已有认证方调用 `page.authenticated(account_key)`，清空上一账号状态并显示等待。
2. 调用 `page.start_sync()`；返回 attempt generation，并发出 `sync_requested`。
3. 已有/未来同步适配器调用 `page.receive(generation, state, projection)`。
4. 只有已确认完成或可信缓存可点击进入，发出 `workspace_requested`；认证成功本身不会发出该信号。

投影白名单：

- `account_key`、`revision`：非空范围与修订；技术数据不放主界面。
- `plans[{key,name,progress}]`：计划名和人话进度；数量由该完整投影的 plans 得出。
- `resources[{key,plan_key,name,kind,missing,verified_redownload,package_supported}]`：kind 为 file/model；空 plan_key 表示资料/模型库位置，非空必须引用当前计划。稳定资源 ID 唯一，UI 不重新生成或删除 binding。
- `progress_count/review_count/history_count`：非负整数，不接受布尔值；适配器负责提供所支持范围的正确数量。
- `empty_account/server_confirmed/trusted_cache/last_sync`：由可信 producer 提供的事实，不由 UI 猜测。缺省/非布尔 true 的授权标志不解锁。

每次重试 generation 递增；迟到、重复、跨账号结果不能覆盖当前状态。无效完成结果进入受控失败，不能通过空数组冒充空账号。无执行器、文件读写、缓存持久化、服务端请求或 V1 导入依赖。

## 验证

**50 passed in 1.74s**：

- `desktop/tests/test_rehydration_ux.py`：35 项，包括成功、空账号、离线/失败、可信缓存矩阵、PDF/模型缺失、重下载/迁移包/不可用矩阵、计划与进度保留、默认折叠详情、V2 gate、账号设置入口、跨账号/迟到/重复拒绝、无效结果、无 IO 依赖、生产等待文案。
- 既有 `desktop/tests/test_e109_capability_config.py`：14 项，注册和忘记密码仍关闭。
- 既有账号设置 Qt 登录动作回归：1 项，使用临时测试 Vault 和假凭据接口，不操作用户 Vault 或真实账号。
- Python 3.13 / PySide6 offscreen；QSettings 隔离，pytest 临时数据位于 `E:/Codex/Temp/MF-MAIN3-03-REHYDRATION-07`。
- 生成离屏同步中、完成但资料/模型缺失、离线画面；人工检查后两张。仅 harness 加载系统中文字体，无系统设置/DPI 修改。检查发现折叠按钮文字不可见，已设为文字+箭头，测试通过。

BACKEND_CHANGED = NO

REAL_DATA_MIGRATION = NO

REGISTER_CAPABILITY = FALSE

FORGOT_PASSWORD_CAPABILITY = FALSE

V1_NOT_MASQUERADING_AS_V2 = YES

WORKER_STATUS = SILENT
