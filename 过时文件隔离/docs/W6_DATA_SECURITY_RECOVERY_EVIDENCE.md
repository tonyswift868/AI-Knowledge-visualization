# W6 数据、安全与恢复证据

## 结论边界

本批次没有宣称 W6-01 已完成生产收敛。当前运行时仍是明确的双数据库：

- legacy 主业务链：`backend/app/db.py`，默认 `data/app.db`；Workspace、Source、Chunk、Goal、Assessment、Plan、StudyDay、Task、Session、Archive Preview 等由 `backend/app/main.py`、`services.py`、`archive_import.py` 直接读写。
- ORM 扩展链：`backend/app/orm.py`，默认 `data/mentorflow_v2.db`；Learning Run、学生状态、Knowledge Map 版本、Narrative、Expedition、奖励账本等由相应 service 读写。
- 双写边界：`main.py` 和 `services.py` 对 Workspace、Source、Goal、Feasibility、Plan、Task、Session 同时写 legacy 和 ORM，但多个异常分支会吞掉 ORM 写入错误。因此 legacy 当前仍是这些核心 HTTP 工作流的返回依据，ORM 不是它们的可靠镜像。
- 交叉读取边界：`learning_run.py::_task_context` 从 legacy Plan 找 Task，再把 Learning Run 写入 ORM；`adaptive_narrative.py` 同时从 legacy 取资料/计划、向 ORM 写语义与剧情成果；`knowledge_map_service.py` 从 legacy Source/Chunk 取证据、向 ORM 写版本化地图。

因此目前可证实的 source-of-truth 决策是：

1. 不删除或覆盖任何现存数据库。
2. clean-install 的 Workspace→资料→目标→计划→教学入口继续以 legacy 为兼容主路径。
3. Learning Run、版本化知识地图、叙事和 Expedition 只以 ORM 为主路径。
4. 在迁移工具、逐对象对账和真实历史 DB 盘点完成前，禁止把任一数据库整体标成唯一 source of truth。

## 新增恢复边界

`backend/app/database_recovery.py` 提供显式、未接入启动流程的 SQLite 恢复基元：

- 只读 `quick_check`、`foreign_key_check` 和逐表计数；
- 使用 SQLite Online Backup API 生成一致副本；
- 目标已存在时拒绝覆盖；
- 先写 `.partial`，校验后原子发布；
- 损坏备份拒绝恢复，且不留下目标或半成品；
- 恢复只允许写到新路径，替换生产库仍需人工备份与确认。

这能证明“备份和恢复机制可用”，不能证明“生产双库已迁移”。

## 迁移测试矩阵

`backend/tests_w6/test_data_security_recovery.py` 覆盖：

- 空 ORM DB：Alembic `upgrade head` 连续两次，版本为 `0012_knowledge_map_foundation`；
- legacy 旧库：重复 `init_db()` 保留已有 Workspace；
- 含数据 ORM DB：升级到 head 后重复升级，保留 Workspace，外键检查无错误；
- SQLite 备份/恢复：表计数与内容一致，拒绝覆盖；
- 损坏备份：恢复前拒绝，目标和 `.partial` 均不存在。

重要限制：当前迁移链 `0001_mentorflow_v2.py` 和 `0012_knowledge_map_foundation.py` 都调用当下 `Base.metadata.create_all()`；这不是冻结的历史 schema 演进。多数 downgrade 也不构成可依赖的完整回滚路径。现阶段应以“迁移前一致备份 + 新路径恢复”作为安全回退，不应声称 downgrade 已获生产验证。

## W6-03 红队证据

新增测试验证：

- Provider 返回非法 JSON 时抛 `INVALID_PROVIDER_JSON`，不伪造成 demo 成功；
- Provider 超时时抛 `PROVIDER_TIMEOUT`，错误不包含 API key，也不静默切换 Fake Provider；
- 嵌套对象、Authorization、URL query、常见 key/value 文本可由 `security_redaction.py` 脱敏；
- Expedition 奖励 sidecar 重试失败时，已通过 Learning Run 仍保持 `passed`、revision 不回退，奖励保持 `pending + retryable`；
- 奖励唯一键和并发幂等已有 `backend/tests/test_expedition.py` 覆盖；
- archive 的 zip-slip、绝对路径、UNC、symlink、文件数、展开大小、压缩比、加密包、嵌套包、篡改/过期、空选择、重复导入、失败重试和临时目录清理已有 `backend/tests/test_v6_archive_import.py` 覆盖。

`security_redaction.py` 是可复用边界，但本批次遵守公共文件禁改约束，尚未接入所有日志调用点；因此“脱敏函数通过测试”不等于“所有未来日志都已全局脱敏”。

## Root 集成点

1. 在允许修改 `main.py`/`services.py` 后，不能继续静默吞掉 ORM 双写异常；至少生成可查询 reconciliation 事件。
2. 先对真实 `app.db` 与 `mentorflow_v2.db` 做只读盘点和对象级对账，再决定分阶段迁移方向。
3. 将 `security_redaction.redact()` 接入统一日志/错误证据出口，而不是散落调用。
4. 发布前应由操作员对两个数据库分别生成不可覆盖备份，并在新路径执行恢复 smoke；不要原地覆盖生产文件。
