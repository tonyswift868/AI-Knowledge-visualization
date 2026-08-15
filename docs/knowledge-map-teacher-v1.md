# MentorFlow 通用知识地图与主动教师 v1

## 安全启动顺序

1. 复现基础 Windows 环境时安装 `backend/requirements.lock.txt`；需要与本次验收完全一致的 Docling 复杂文档环境时改为安装 `backend/requirements-documents.lock.txt`。`backend/requirements-documents.txt` 只用于开发时选择 Docling 顶层版本，不是完整锁文件。Docling 未安装会如实显示为 unavailable，本地文档适配器仍按各自健康状态工作。
2. 使用 `scripts/backup_knowledge_map_state.py` 备份 `app.db`、`mentorflow_v2.db`、上传资料和非敏感运行配置。脚本记录 SHA-256、SQLite `integrity_check`、表行数和恢复命令，不读取密钥文件。
   备份文件本身保持只读；恢复时先停止应用并保留当时状态，再把目标文件复制回项目。Windows 会保留只读属性，因此只对复制后的项目目标文件清除只读属性，绝不能改写备份目录中的原件。
3. 在 `backend` 目录执行 `..\.venv\Scripts\python.exe -m alembic upgrade head`。规范知识地图、候选、材料处置、计划执行和掌握事件表只由 Alembic 管理。应用 lifespan 会一次性初始化仍在兼容期的旧课堂表；请求连接只以 `mode=rw` 打开数据库并校验所需表，缺迁移时返回 `DATABASE_MIGRATION_REQUIRED`，不会在请求期间补表。
4. 如需真实 Provider，在项目根运行：

   ```powershell
   .\.venv\Scripts\python.exe .\scripts\store_provider_credential.py
   ```

   终端使用隐藏输入。脚本只输出 `mfcred_...` 引用；把这个引用粘贴到设置页。原始 Key 不进入浏览器、HTTP API、SQLite、日志或项目文件。替换现有凭据时使用 `--replace mfcred_...`。如果启动时发现旧版本留下的 `mentorflow_api_key_v1:*` 浏览器项，前端只枚举键名并立即删除，不读取或迁移值，同时提示轮换对应 Provider 密钥。MiniMax 教师语音和私有克隆工具同样只接受 Credential Locker 引用，不再读取密钥文件或明文环境变量。
5. 启动后先查看 `GET /api/active-teaching/material-capabilities`。主动教学上传控件和旧工作区的直接文件选择器都从该响应生成浏览器 `accept`；两个后端上传入口均调用同一个 `ConversionRegistry`，不再各自维护 MIME/扩展名白名单。用户可见限制以该能力响应为准。

## 规范流程

- `POST /api/active-teaching/workspaces/{id}/knowledge-map/jobs` 创建任务；没有已验证真实 Provider 时返回 `428 PROVIDER_VERIFICATION_REQUIRED`。
- `GET /api/active-teaching/knowledge-map/jobs/{job_id}` 轮询到 `ready`、`ready_with_review`、`failed` 或 `cancelled`。
- `GET /api/active-teaching/workspaces/{id}/knowledge-map?revision=active` 读取 last-good 活动修订。
- 候选确认/驳回会创建新修订并重新执行图质量门；不会就地改图，也不会按名称自动合并。
- `POST .../mastery-plans/preview` 只生成草案。未纳入资料会阻止激活；容量不足会生成顺延草案，必须显式确认后调用 `POST .../{plan_id}/activate`。
- `GET /api/active-teaching/workspaces/{id}/learner-preference-profile` 返回显式维护、带修订号的 `LearnerPreferenceProfileV1`。教师只使用该档案生成兴趣类比；没有档案时使用策略注册表中的中性结构类比，不从聊天语气、长度或时间猜测兴趣。
- 旧 `knowledge-map/generate` 只保留为 202 兼容包装；旧 `knowledge-graph` 写接口返回 410。

任务缓存同时绑定来源指纹、策略版本、Provider 类型、Base URL、模型和 Credential Locker 引用；只有完整生成指纹一致的活动修订才可复用。生成期间资料或 Provider 配置变化会以稳定错误结束任务，不会激活过时结果。进程重启时，持久化但未完成的 `queued/converting/extracting/validating` 任务会标为可重试的 `KNOWLEDGE_MAP_JOB_INTERRUPTED`，对应非活动 staged 修订标为失败，last-good 地图和活动计划保持不变。

## 迁移和数据边界

- `0012_knowledge_map_foundation`：来源修订、转换清单和版本图基础。
- `0013_knowledge_map_teacher_v1`：任务、候选、材料处置、计划激活、偏好、教师回合与事件。
- `0014_material_disposition_revision_scope`：材料处置按不可变地图修订保存。
- `0015_mark_unverified_legacy_maps`：旧 Provider-free 图标为 `legacy_unverified` 和只读；不删节点、边、工作区、资料或计划。
- `0016_migrate_generic_knowledge_support_tables`：接管旧通用地图辅助表和资源发现表，运行请求只校验迁移，不再创建这些表。
- `0017_canonical_plan_execution`：新增规范计划执行任务、修订绑定的掌握事件和规范外部资源表；v1 激活与授课不再向旧 `active_knowledge_*` 图表写入投影。

新图先写 `staged`，质量门通过后才原子切换；失败修订保留错误，上一张活动图和活动计划继续可读。计划任务保存不可变概念快照和证据哈希，掌握事件绑定地图修订、规范节点、任务和证据哈希。旧进度只允许以来源修订和证据内容哈希完全一致的方式关联，不使用名称相似匹配。

## 能力边界

扫描 PDF 首次加载 Docling/OCR 模型时采用版本化的 120 秒材料转换预算
（`conversion-budget.v2`）；热启动仍复用同一通用转换路径。超过预算时保持
`CONVERSION_TIMEOUT` 可重试失败，不会回退为伪造文本或结构节点。

- 注册表内材料走同一领域无关流水线。图片进入 RapidOCR，扫描 PDF 进入版面/OCR，音频进入 SenseVoice/sherpa-onnx，ZIP 逐项安全处理。混合 PDF 会合并 Docling 的版面/OCR 项与 PyMuPDF 检出的缺失原生文本块，避免扫描页被识别时静默丢失其他文本页，并保留页码与可用 bbox。
- 视频、未知格式、损坏/加密或超限材料会返回稳定错误码和可读原因，绝不伪造地图。
- Docling 2.119.0 是可选复杂文档适配器；未安装时能力接口会报告健康状态，已注册且健康的原生适配器仍可工作。
- `model_common` 默认隐藏且不进入计划；只有用户确认后才可参与。网络资源发现只读取当前规范修订并写入独立资源表，不改节点、边、地图修订或计划快照。

教师进入概念后由后端状态机直接产生“目标定位 → 核心讲解 → 结构类比 → 完整示例/代码 → 最小检查”，随后才等待学习者回答。Provider 只填充受 Pydantic 校验的表达内容，不能改变阶段、宣布掌握或修改计划；失败后的补救轮数和补救任务估时均来自版本化教学策略。`manual_skip` 只记录跳过，不写成掌握。

## 发布门禁

本地契约模拟、转换测试、浏览器交互和构建不能替代真实 Provider 验收。发布前必须使用一个由用户明确配置的 OpenAI-compatible Provider，对“文本 + 漂亮 JSON + 文档 + OCR + 音频”混合资料包运行端到端，并核对每份材料处置、节点/边证据、候选状态和计划激活。未完成该步骤时不得宣称通用语义提取已经完成。

凭据引用已写入 Credential Locker 后，优先执行隔离验收器。它会在
`E:\Codex\Temp` 创建独立的 `app.db`、`mentorflow_v2.db`、上传目录和随机
loopback 端口；不会复制、打开或迁移项目中的活动数据库。服务通过正常
FastAPI lifespan 启停，因此验收启动的自有运行时也会在退出时关闭：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_isolated_real_knowledge_map_acceptance.py `
  --credential-ref mfcred_... `
  --base-url https://provider.example/v1 `
  --model provider-model-id `
  --audio-file E:\path\to\clear-speech.wav `
  --audio-sentinel "这段音频中清晰说出的原句"
```

验收工具不接受原始 Key，只接收 Credential Locker 引用。`--audio-sentinel` 必须是音频中清晰说出且预期由 ASR 原样识别的一段文字。隔离运行目录始终保留 `isolated-run.json`、服务日志和验收 stdout/stderr；成功时另有 `acceptance-report.json`，失败时 `isolated-run.json` 记录稳定失败状态和日志位置。运行器还会比较活动 `app.db`、`mentorflow_v2.db` 与整个主动教学上传目录的执行前后内容指纹；任何变化都会把验收强制标为失败。工具会验证随机领域哨兵确实出现在对应材料证据中、音频哨兵出现在带开始/结束时间的引用中、每个材料概念具有证据支持的 `contains` 关系，并逐项确认候选关系；只有五份材料全部 `included`、材料层节点和边均有证据、地图状态为 `ready`、计划成功激活且活动状态指纹完全不变时才输出 `passed`。

`run_real_knowledge_map_acceptance.py` 仍可供已经明确指向测试数据库的受控
服务使用，但不得直接对日常使用中的本地服务执行发布门禁。
