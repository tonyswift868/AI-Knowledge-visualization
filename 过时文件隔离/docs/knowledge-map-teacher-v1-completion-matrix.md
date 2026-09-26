# MentorFlow 知识地图与主动教师 v1 完成矩阵

本文件是发布审计清单。状态只能取 `verified`、`in_progress` 或 `missing`；
`verified` 必须能指向当前工作树中的实现和本次发布周期产生的直接证据。

| 原计划要求 | 当前状态 | 权威实现或验收证据 |
|---|---|---|
| 写入前备份数据库、上传和非敏感配置，记录哈希、完整性、行数与恢复命令 | verified | `scripts/backup_knowledge_map_state.py`；`E:\Codex\Data\backups\MentorFlow\20260813-knowledge-map-teacher-v1-prechange` |
| 不迁移或读取明文密钥，Provider 只接受 Windows Credential Locker 引用 | verified | `backend/app/credential_store.py`、`backend/app/provider_runtime.py`、`scripts/store_provider_credential.py`、`backend/tests/test_credential_store.py` |
| 0012 后仅用追加 Alembic 迁移管理 v1 表，请求期间不补建知识地图表 | verified | `backend/alembic/versions/0012_*.py` 至 `0017_*.py`、`backend/tests/test_knowledge_map_v1_migration.py` |
| 旧地图只读并标记 `legacy_unverified`，不删除旧计划或用户数据 | verified | `0015_mark_unverified_legacy_maps.py`、`backend/tests/test_knowledge_map_v1_migration.py` |
| staged 修订通过质量门后原子激活，失败保留 last-good 地图和计划 | verified | `backend/app/generic_knowledge_api.py`、`backend/tests/test_knowledge_map_v1_api.py` |
| 统一 ConversionRegistry，格式/MIME/签名/健康状态共用一个能力来源 | verified | `backend/app/conversion_adapters.py`、`GET material-capabilities`、`backend/tests/test_conversion_registry_v1.py` |
| 文本、代码、结构化数据、文档、图片 OCR、扫描/混合 PDF、音频和安全 ZIP | verified | `backend/tests/test_material_capability_matrix_v1.py`、`test_conversion_registry_v1.py`、`test_material_pipeline_v1.py` |
| 漂亮 JSON、UTF-16、GB18030 和所有异常材料有明确处置或稳定拒绝 | verified | 上述材料测试及真实 DeepSeek 混合材料报告 |
| 每份材料具有 included/needs_review/rejected 处置与重试原因 | verified | `orm_material_dispositions`、`backend/tests/test_knowledge_map_v1_api.py` |
| 无真实 Provider 返回 428，禁止结构语义兜底 | verified | `PROVIDER_VERIFICATION_REQUIRED` 契约测试、生产核心反兜底扫描 |
| material/model_common 分层，model_common 默认隐藏且未确认不进入计划 | verified | `backend/app/knowledge_map_v1.py`、前后端 v1 测试 |
| 节点和边都有 EvidenceSegmentV1；页码、bbox、音频时间范围按材料保留 | verified | 转换测试、属性测试、真实 DeepSeek 报告 |
| 不按名称自动合并；候选确认/驳回生成可审计新修订 | verified | `backend/tests/test_knowledge_map_v1_api.py`、`test_knowledge_map_v1_properties.py` |
| NetworkX 质量门保证无悬空、自环和已确认 prerequisite 环 | verified | `backend/app/knowledge_map_v1.py`、属性测试 |
| 外部资源发现与地图/计划写入解耦 | verified | `backend/app/learning_resources.py` 及资源隔离测试 |
| 保留现有调度语义，阈值配置化，预计和实际耗时隔离 | verified | `backend/config/knowledge_policy_v1.json`、`backend/tests/test_scheduler_policy_v1.py` |
| 容量不足生成需确认的顺延草案；地图更新不删除旧计划 | verified | v1 API 和调度策略测试 |
| TeacherTurnV1 主动完成讲解、类比、示例、检查后等待回答 | verified | `backend/app/teacher_turn_v1.py`、`backend/tests/test_teacher_turn_v1.py` |
| 显式兴趣档案、中性类比、两轮补救、阻塞、manual_skip 非掌握 | verified | 教师策略注册表和教师 v1 测试 |
| React Flow + Dagre、证据面板、候选审核、键盘列表、Markdown 禁 HTML | verified | `ActiveTeachingChainView.tsx`、Vitest、`knowledge_map_v1_browser.spec.ts` |
| 前端从源码重新构建 | verified | `npm run build` 于 2026-08-13 通过 |
| 真实 OpenAI-compatible Provider 混合材料端到端 | verified | `E:\Codex\Temp\mentorflow-deepseek-v4-flash-real-acceptance-final.json`，DeepSeek V4 Flash，5/5 材料 included，计划 active |
| 两个活动数据库、用户上传和力扣 150 计划/进度不被验收或清理污染 | verified | 清理报告、真实验收 live-state 指纹、SQLite integrity/FK 检查 |
| OSS ADR、依赖锁、许可证/模型归属和 SBOM | verified | `docs/adr/001-knowledge-map-v1-open-source.md`、requirements lock、第三方说明、CycloneDX SBOM |
| 旧测试迁移到 v1 契约且后端全量回归可解释 | verified | 42 个与 v1 安全边界冲突的 v0 用例按精确 node id 显式退役；替代契约 55/55 通过；后端全量 929 通过、46 跳过、23 个失败均与实施前基线逐项一致，JUnit：`E:\Codex\Temp\mentorflow-backend-final-20260813.xml` |
| 浏览器验收无 React 更新循环并覆盖 v1 图、教师和安全渲染 | verified | `Teacher2DStage` 快照反馈循环回归已修复；Vitest 303/303；v1 Playwright 1/1；闲聊 Playwright 1/1；8 个依赖无 Provider 旧地图契约的场景显式退役；默认 Playwright 强制使用临时数据库 |
| 在真实用户数据库隔离副本上验证迁移、力扣计划/进度和恢复 | verified | `scripts/rehearse_live_data_migration.py`；`E:\Codex\Temp\mentorflow-live-data-migration-rehearsal-final.json`：0011→0017、既有表行数不变、16 工作区/6 计划/6 进度哈希不变、恢复 SHA-256 一致 |

全部条目均已获得直接证据。全量后端仍有 23 个与实施前基线相同的非 v1
失败（旧主应用契约、KKS 本地资产状态和本地语音签名工具）；它们没有被伪装
成通过，也不构成本次 v1 的新增回归。
