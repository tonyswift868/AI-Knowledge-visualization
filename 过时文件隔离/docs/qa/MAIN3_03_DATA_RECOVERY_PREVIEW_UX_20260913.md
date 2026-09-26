# 03 — 旧数据恢复预览 UI/source 交付

DISPATCH_ID = MF-MAIN3-03-DATA-RECOVERY-WIZARD-20260913-06

TASK_ID = MENTORFLOW_LEGACY_DATA_RECOVERY_PREVIEW_UX_V1

STATUS = PASS_DATA_RECOVERY_PREVIEW_UX

## 范围与工作区

- 既有 managed worktree：`E:/Codex/Data/worktrees/bb70/MentorFlow`。
- 起始 HEAD：`e65c7980ee49e475dff60ced9e9d3edf505eed41`；与调度要求一致，起始工作树干净。
- 分支：`worker/main3/03`。HEAD/index 位于 `.git/worktrees/MentorFlow3/`，独立于共享 Local。
- 未创建工作树、未切换或写入 integration/main3，未触及 04 的迁移实现。
- 交付为可由调用者创建的原生 Qt 向导、内存状态机和表示层合同。尚未接入正式菜单或真实预览生产者。
- 本分支未找到可直接消费的 04 正式 migration preview/readiness 接口；本模块定义的是未来适配用的 **UI 表示层合同**，不宣称它已经是 04 的 canonical API。

## 用户合同

| 要求 | 实现 |
| --- | --- |
| 中文、摘要优先 | 初始页先展示发现提示和六类数量；未知数显示“待确认”，不伪装为 0 |
| 数据仍存在 | 有确认的数据时显示“这些数据仍然存在，目前尚未导入当前账号。” |
| 不误报丢失 | `data_loss_confirmed=false` 才显示“尚未发现数据丢失。”；其余情况提示待核实 |
| 来源 | 主要历史数据、历史开发副本、更早的本机副本；差异用合并方式待确认提示 |
| 学习空间 | 名称、计划数、最近活动、资料数；搜索、逐个选择、全部选择/选择搜索结果、取消选择 |
| 默认选择 | 所有来源和空间均不默认选中；归属未证明的选项即使手工选择，也不能通过准备门 |
| 冲突 | 五类人话摘要；有对应证明才显示已找到内容候选；换行差异单独提示 |
| 安全保证 | 五条固定说明，无可取消复选框 |
| 准备条件 | 四个准备标志全部为严格布尔 true，且当前选择与生产者确认范围精确一致 |
| 技术详情 | 默认折叠、只读纯文本；路径、空间 ID、身份/账号/清单指纹、导入登记状态仅在这里显示 |
| 本轮按钮 | 未就绪禁用；就绪后点击只确认内存预览，明确显示尚未导入，无执行器或导入信号 |
| 关闭 | 原生窗口关闭与“关闭预览”结束对话框，不执行任务 |

## 未来只读适配入口

`LegacyRecoveryWizard(mapping, parent)` 创建向导；`apply_preview(mapping)` 接收后续完整投影。
`selection_changed` / `preview_requested` 只传递选择范围，不自带网络、磁盘、身份或迁移能力。
未来 04 适配器负责转换已经审计的事实，不能用本 UI 的确认结果代替迁移后端授权或安全校验。

输入 schema：`mentorflow.legacy-recovery-preview.v1`。

- `revision`：非空预览版本；`sequence`：当前目标下单调递增的非负整数；`target_key`：非空目标标识，仅技术页展示；`target_label`：用户可读账号名称。
- `data_present`、`data_loss_confirmed`：审计事实，UI 不扫描目录推算。
- `summary`：`plans/progress/materials/learning_records/conversations/reviews`，非负整数或缺省/空值。缺省表示待确认。
- `sources[]`：`key/kind/path/has_differences`；kind 为 primary/development/older_local，其他类型显示“其他历史副本”。
- `workspaces[]`：`source_key/key/name/plans/materials/last_active/ownership_proven`。空间身份由来源与空间 key 共同确定；ID 不进入常规表格文本。
- `conflicts`：`identical/main_only/other_only/different/path_repair`；`all_content_candidates_found` 与 `line_ending_only` 均由生产者提供。
- `readiness`：`TARGET_STORAGE_STABLE/IDENTITY_ISSUER_STABLE/SOURCE_SELECTED/CONFLICT_POLICY_READY`。缺省、数字、字符串均不能解锁。
- `confirmed_selection`：`sources[]` 与 `workspaces[{source_key,key}]`，必须指向投影中已知的来源和空间。
- `technical`：仅接收字符串 `issuer_hash/subject_hash/manifest_hashes/ledger_status`；其他字段不展示。

输出选择包含 `preview_revision/preview_sequence/target_key/sources/workspaces`。选择变化后不推算新冲突结果，需取得覆盖对应范围的新投影；不匹配时明确提示更新预览并阻止确认。同一目标的迟到/重复 sequence 被忽略；目标变化清空选择；不合法投影进入受控不可用状态。

本轮没有持久化、扫描、快照、复制、合并、迁移、导入端口，也没有账号 Vault/数据库/Keycloak 调用。安全保证是未来执行实现必须兑现的产品合同，本次没有执行验证这些底层操作。

## 验证

- `desktop/tests/test_legacy_recovery_preview.py`：45 passed。
- 既有 `desktop/tests/test_e109_capability_config.py`：14 passed。
- 合计：**59 passed in 1.45s**（Python 3.13 / PySide6 / offscreen；pytest 插件自动加载关闭，QSettings 隔离至临时测试目录）。
- 覆盖摘要、未知数量、隐藏技术信息、来源/空间选择与搜索、冲突文案、固定保证、全部 16 种准备组合、严格布尔值、无归属证明、选择不一致、目标切换、迟到响应、无效合同、数据完整性待核实、同步预览响应、关闭、确认不执行、生产代码无审计数值硬编码。
- AST 边界检查只允许表示层、Qt 与样式依赖，无 IO/数据库/网络/迁移模块导入。
- 本地离屏检查摘要、空间选择、冲突和准备页；手动查看了摘要、空间选择和准备页。offscreen 字体库为空，检查 harness 显式加载系统微软雅黑字体；未改系统字体或 DPI。
- 初次测试因临时父目录缺失而停在 fixture setup；创建调度专属临时目录后重新执行通过。
- 审计 fixture 的 88/83/330、15 路径修复及目标昵称来自任务输入；空间拆分、日期、ID、指纹及其他冲突数量为合成样本，不是再次审计的真实数据。
- 真实数据根未读取，源文件、数据库、服务器未修改；无真实迁移、安装、SMTP、Keycloak、GitHub Actions。

REGISTER_CAPABILITY = FALSE

FORGOT_PASSWORD_CAPABILITY = FALSE

MIGRATION_EXECUTED = NO

DATA_SOURCE_MUTATED = NO

SERVER_MUTATED = NO

WORKER_STATUS = SILENT
