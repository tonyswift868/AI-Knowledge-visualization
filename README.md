# MentorFlow

MentorFlow 是证据优先的目标驱动学习工作流：导入资料，分析目标与时间，生成可预览计划，按任务学习，并用引用、评估、错题和复习记录反馈进度。

## 启动

```powershell
cd "E:\aiPlay\AI-Knowledge visualization"
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1
```

开发地址：`http://127.0.0.1:5173`；API 文档：`http://127.0.0.1:8000/docs`。

生产式本地运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

原生 Windows 客户端（PySide6 / Qt 6）：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\desktop.ps1
```

生成可双击的开发版软件入口：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_native_desktop.ps1
```

输出为 `release\native\MentorFlow.exe`、`MentorFlow.lnk` 和 `MentorFlow-Debug.lnk`。
前两个无控制台，后者显示运行日志，便于排查。它们都链接当前源码，适合现阶段快速迭代；原生页面
稳定后再通过 Qt 官方部署工具冻结成独立 EXE 安装包。

软件会自动启动只监听本机随机端口的服务，并显示原生注册或登录界面。首次注册
会认领当前已有任务、资料和进度；密码使用 Argon2id 哈希保存，原始密码不会
写入数据库。任务、资料、知识地图、候选审核、计划、课堂、附件、模型连接和数据库
诊断已经使用 Qt 原生控件；“完整功能”页继续保留尚未迁移的旧工具，因此桌面化不会
以丢功能为代价。

“模型连接”页可直接安全保存和验证真实 Provider：Key 只写入 Windows Credential
Locker，软件配置仅保留凭据引用、Base URL 和模型名，并在每次启动时自动重连。
账户、数据库、备份和恢复入门见 `docs/native-desktop-user-guide.md`。

## 能力

- 文本、TXT/Markdown/HTML/DOCX/PDF、URL、受控本地路径导入；ZIP 资料包支持安全预览、勾选确认、文档/代码文本解析、去重和内部路径引用。
- ZIP 安全限制：上传 100 MB、解压后总计 500 MB、单文件 50 MB、2000 成员、深度 12、单文件压缩比 100:1、整体压缩比 50:1；拒绝 Zip Slip、链接、加密 ZIP、ZIP Bomb、可执行/二进制内容。
- preview token、文件 hash/mtime 复核、敏感目录与 symlink 防护。
- jieba + BM25 检索、真实页码、引用卡片、Grounding Critic、冲突面板。
- Fake、OpenAI-compatible、Gemini、Anthropic Provider；真实 Provider 只接受 Windows Credential Locker 引用，原始 Key 不进入浏览器、HTTP API、数据库、日志或响应。
- 通用知识地图 v1 使用能力注册表、版本证据、候选审核和 last-good 原子激活；没有已验证真实 Provider 时拒绝新生成，不创建结构兜底课程。
- 主动教师使用结构化 `TeacherTurnV1` 和后端状态机执行讲解、示例、检查、纠错与阻塞记录。
- 容量感知排期、preferred weekdays、不可用日期、review/final buffer、版本化 Replan。
- SQLAlchemy 2.x ORM、Alembic、Job 状态、Tutor、rubric 评估、Mastery/Mistake/Review/Progress。

## 验证

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1
```

知识地图 v1 的迁移、凭据写入、规范 API、能力边界和真实 Provider 发布门禁见 `docs/knowledge-map-teacher-v1.md`。测试结果必须以当前工作树实际执行输出为准，README 不保存会过期的通过数量。

更多说明见 `docs/ARCHITECTURE.md`、`docs/PRODUCT_FLOW.md`、`docs/SAFETY_AND_GROUNDING.md` 与 `TASK_COMPLETION_REPORT.md`。
