# MentorFlow 原生桌面版验收记录

验收日期：2026-08-14（Asia/Shanghai）

## 交付边界

- Windows GUI 启动器：`release/native/MentorFlow.exe`
- Qt 原生注册、登录、任务、资料、知识地图、节点/关系证据、候选审核、计划、课堂、
  附件/录音导入、Provider 连接、数据库诊断和在线备份。
- 同一软件进程负责启动、健康检查和关闭仅监听随机 loopback 端口的 FastAPI 服务。
- 尚未迁移的 Live2D、远征等旧工具继续由“完整功能”隔离兼容页提供，避免功能或数据丢失。

## 当前磁盘证据

- `app.db`：`PRAGMA integrity_check=ok`，外键错误 0。
- `mentorflow_v2.db`：`PRAGMA integrity_check=ok`，外键错误 0，Alembic
  `0021_local_login_throttle`。
- 与 `20260814-001213-native-manual` 不可变备份比较：
  - `orm_progress=83`
  - `active_knowledge_progress_snapshots=150`
  - `active_plan_tasks=11363`
  - `active_plans=55`
  - `active_materials=70`
  - `active_receipts=55`
  - 上述表实时行数与备份完全一致。
- 备份包含 153 个文件条目的 SHA-256 清单、SQLite 完整性/行数报告和 `RESTORE.txt`。

## 已执行验证

- 原生账户、地图、计划、教师、材料组合回归：37 passed。
- 原生桌面隔离冒烟：注册、认证 Cookie、资料上传、能力注册表、页面构造、地图画布通过。
- 扫描 PDF OCR 冷启动回归：通过；转换预算由 `conversion-budget.v2` 配置为 120 秒。
- 前端兼容层：29 test files、303 tests passed；生产构建成功。
- `npm audit --omit=dev`：0 vulnerabilities。
- 全部后端：940 passed、46 skipped、23 failed。
  23 个失败与改造前记录的基线数量一致，涉及旧 API 预期、KKS 本地只读资源权限、
  语音发布信任夹具和旧 W6 验收矩阵；本次原生/认证/知识地图 v1 聚焦组合均为绿色。
- 真实 Provider：`api.deepseek.com` 验证成功；请求 `deepseek-chat`，服务报告
  `deepseek-v4-flash`，约 1410 ms。Key 仅存 Windows Credential Locker。

## 不夸大的限制

- 当前 `MentorFlow.exe` 是面向持续调试的 Windows GUI 启动器，会调用项目 `.venv`
  和当前源码；它不是可以脱离项目目录分发的冻结安装包。
- 实时麦克风录制尚未原生化；原生课堂可导入录音文件并调用本地 ASR。
- Live2D/WebGL 和远征等非核心旧工具仍在隔离兼容页，不应被描述为已经原生重写。
- 当前只开放首个本地账户；在所有工作区 API 完成逐用户过滤前不开放第二账户。
