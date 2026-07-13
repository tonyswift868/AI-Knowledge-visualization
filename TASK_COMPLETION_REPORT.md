# MentorFlow V6 Secure ZIP Archive Import 完成报告

## 结论

MentorFlow V6 Secure ZIP Archive Import 已在 V5 基础上完成并实测通过；ProviderSession、真实 Provider、资料导入、Grounding、计划、Tutor、进度、Replan、测试和生产启动能力均保留。

## MentorFlow V6 Secure ZIP Archive Import

- 产品流程为“上传 ZIP → 安全扫描 → 目录预览 → 支持/忽略/拒绝展示 → 用户勾选 → 确认导入 → 逐文件解析 → 去重 → Chunk/索引 → 汇总结果”；上传不会直接全部解压入库。
- 默认限制为 ZIP 100 MB、解压后总计 500 MB、单文件 50 MB、成员 2000 个、目录深度 12、单文件压缩比 100:1、整体压缩比 50:1，预览 TTL 30 分钟。
- Zip Slip、绝对/盘符/UNC 路径、symlink/junction/特殊文件、ZIP Bomb、加密 ZIP、可执行/二进制内容和内容类型不匹配均有明确拒绝或错误码；嵌套归档只忽略，不递归解压。
- 新增两阶段端点：`POST /api/workspaces/{workspace_id}/sources/archive/preview` 与 `POST /api/workspaces/{workspace_id}/sources/archive/import`。预览保存在 `data/archive_previews/<preview_id>/`，导入结束、取消或过期后清理临时目录。
- 支持 PDF、DOCX、Markdown、TXT、HTML，以及 Python/Java/JS/TS/TSX/JSX/Go/Rust/C/C++/C#/Kotlin/Swift/PHP/Ruby/Shell/PowerShell/SQL/JSON/YAML/TOML/XML/CSV/IPYNB 等文本资料；代码只解析为文本，不执行、不安装依赖；IPYNB 仅提取 Markdown 和 Code cell。
- `Source` 保留 `archive_preview_id`、`parent_archive_name`、`archive_relative_path` 与语言；标题、计划证据和 Tutor 引用包含 `study_bundle.zip / internal/path`，同 Workspace 按内容 SHA-256 去重，单文件失败不影响其他文件。
- 前端资料区现在整体接收拖放，并显示拖放高亮与已接收反馈；ZIP 导入结果使用高对比度统计标签。创建向导按已保存/已解锁的最高阶段开放自由跳转，未到达阶段保持禁用。
- 可行性评估会根据资料量、开始日期、截止日期与学习频率估算总工作量，自动加入 20% 复习/拖延/切换损耗，计算并持久化 `recommended_minutes_per_day`；创建向导提供本地草稿、项目目录 JSON 预设保存/加载和可配置保存位置。
- 计划保存开始日期与截止日期；计划页提供每日刷新时间设置，默认每天 00:00，并支持页面打开时的浏览器提醒。
- 新增 `data/presets/mentorflow_story_theater_default.json` 学习剧场预设；计划会为每个可用学习日生成任务，每个任务包含日期幕、地点、角色、知识焦点、分支选项和证据驱动 Tutor 入口。真实 Provider 可返回并覆盖世界观、角色和每日场景。
- 新增 Alembic migration `0004_archive_import`，并在 `artifacts/archive_import_acceptance` 生成正常预览/导入、Zip Slip、ZIP Bomb、可执行文件、测试、E2E、Build 与证据报告。

## MentorFlow V5 Real Provider Mode

- Provider Runtime 使用短生命周期内存 ProviderSession；API Key 不写 SQLite、ORM、日志、响应或验收 artifact，session 过期后真实 Workspace 返回 `PROVIDER_SESSION_EXPIRED`。
- Zustand 是前端唯一 ProviderStore；Settings、首页状态、创建流程、Plan、Tutor 共用 `provider_session_id`，API Key 仅存在当前页面内存。
- Demo Workspace 明确使用 Fake；真实 Workspace 不接受 Fake Provider，缺少已验证 session 或 Provider 请求失败时直接报错，不静默 fallback。
- Feasibility、Plan、Tutor、Evaluator、Replan 返回 `generation_meta`，包括 mode、provider、model、session、request_id、latency 和 `fallback_used=false`。
- V5 证据包由真实 MockTransport 测试和构建/E2E 命令生成，位置为 `artifacts/provider_real_mode_acceptance`。

## 主要修复

- 前端移除 `@ts-nocheck`，使用 Router、Zustand、react-markdown、Vitest/Testing Library；创建流程与 Demo 流程分离。
- 文件选择、拖放、多文件 multipart 上传、状态反馈、URL/文本导入为真实请求；本地路径使用 preview token、selected IDs、hash/mtime 和安全边界。
- PDF 按真实页生成 `page_start/page_end`；DOCX/Markdown/HTML 页码为空；扫描 PDF 返回 `needs_ocr`；同一 Workspace 按 SHA-256 去重。
- URL 仅允许 HTTP(S)，逐跳复核重定向，拒绝私网/回环/保留地址，限制类型与大小，清理 HTML 并保存快照元数据。
- `RetrievalService` 使用 jieba + BM25，按标题/信任级别加权并隔离 Workspace；Tutor 按查询检索；Grounding Critic 校验 source/chunk/page/quote。
- Fake/OpenAI-compatible/Gemini/Anthropic Provider 已实现 HTTP 适配和结构化错误；Key 不持久化；提示词通过 `prompt_loader` 进入运行链路。
- 排期遵守 preferred weekdays/unavailable dates、容量和 review/final buffer；不足返回 backlog/options；Replan 保存版本、保留已完成任务、生成 diff/job 并归档旧版。
- SQLAlchemy 2.x ORM 覆盖核心实体、Session/Message/Citation、Conflict、Job 等；Alembic 初始迁移位于 `backend/alembic`。
- FastAPI 使用 lifespan；错误统一为 `{error:{code,message,details,request_id}}`，响应含 `X-Request-ID`。
- 学习页默认关闭外部知识；用户显式开启后才发送 supplemental 请求，并在独立区域标明“AI 补充，未包含在当前资料中”。冲突面板展示双方来源、chunk、页码和原文证据，支持选择来源并持久化解决结果。

## 实测结果

```text
Backend: 90 passed
Frontend: 44 passed
E2E: 8 passed
Frontend build: passed
Production build script: passed
Smoke: passed
Security smoke: passed
scripts/test.ps1 exit code: 0
Archive import smoke: passed
Alembic: 0004_archive_import (head)
Provider real-mode smoke: passed (MockTransport)
No-fake-fallback: passed
```

验证命令：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

生产启动前运行 `build.ps1`，再运行 `start.ps1`，访问 `http://127.0.0.1:8000`；实测 `/api/health`、`/`、`/docs`、`/openapi.json` 均返回 200。

## 数据与迁移

原有 SQLite 数据由兼容层保留；规范化 ORM 数据库为 `data/mentorflow_v2.db`，启动时执行 `alembic upgrade head`，不会删除旧数据。删除上传源时仅清理 `data/uploads` 内副本，不删除用户原始本地文件。

## 已知边界

真实 Provider 需要合法 Base URL、Model 和 API Key；默认 Demo 使用 Fake Provider。OCR 引擎未内置，扫描 PDF 会明确停在 `needs_ocr`。URL 抓取是受限单次请求，不是通用爬虫。

数据迁移当前为 Alembic `0001_mentorflow_v2`、`0002_conflict_record_fields`、`0003_provider_workspace_mode`、`0004_archive_import` 与 `0005_narrative_runtime_v1`；前端路由清单和来源/计划 API 契约位于 `frontend/src/app` 与 `frontend/src/features`，Narrative Store 位于 `frontend/src/stores/useNarrativeStore.ts`。

V5 已知限制：证据包使用 `httpx.MockTransport`，不调用真实付费 API；ProviderSession 默认 TTL 为 30 分钟；真实模型的返回质量、额度和网络延迟仍取决于用户提供的 Provider。

V6 已知限制：第一版只处理 ZIP，不递归导入嵌套归档；PDF 仍沿用既有 OCR 边界；超大资料包需要在配置限制内分批导入；浏览器端显示的是预览扫描结果，最终导入前后端仍会重新校验 ZIP 哈希、成员路径、大小和内容哈希。
