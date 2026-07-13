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

## 能力

- 文本、TXT/Markdown/HTML/DOCX/PDF、URL、受控本地路径导入；ZIP 资料包支持安全预览、勾选确认、文档/代码文本解析、去重和内部路径引用。
- ZIP 安全限制：上传 100 MB、解压后总计 500 MB、单文件 50 MB、2000 成员、深度 12、单文件压缩比 100:1、整体压缩比 50:1；拒绝 Zip Slip、链接、加密 ZIP、ZIP Bomb、可执行/二进制内容。
- preview token、文件 hash/mtime 复核、敏感目录与 symlink 防护。
- jieba + BM25 检索、真实页码、引用卡片、Grounding Critic、冲突面板。
- Fake、OpenAI-compatible、Gemini、Anthropic Provider；ProviderSession 只在后端内存短期保存，Key 不进入 storage、数据库、日志或响应。
- 容量感知排期、preferred weekdays、不可用日期、review/final buffer、版本化 Replan。
- SQLAlchemy 2.x ORM、Alembic、Job 状态、Tutor、rubric 评估、Mastery/Mistake/Review/Progress。

## 验证

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1
```

当前实测：Backend 90 passed，Frontend 44 passed，E2E 8 passed，Build、Narrative Runtime、Archive import smoke、smoke/security 全部通过；预设默认保存到 `data/presets`，计划支持开始日期、截止日期、全周期每日场景任务和每日 00:00 刷新提醒；Narrative Runtime V1 验收证据位于 `artifacts/narrative_runtime_v1_acceptance`，V6 证据包位于 `artifacts/archive_import_acceptance`，V5 Provider 证据包位于 `artifacts/provider_real_mode_acceptance`。

更多说明见 `docs/ARCHITECTURE.md`、`docs/PRODUCT_FLOW.md`、`docs/SAFETY_AND_GROUNDING.md` 与 `TASK_COMPLETION_REPORT.md`。
