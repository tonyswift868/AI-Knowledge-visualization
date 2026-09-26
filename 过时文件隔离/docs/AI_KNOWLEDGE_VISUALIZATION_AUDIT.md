# AI-Knowledge-visualization / MentorFlow 综合工程审计报告

> 审计日期：2026-08-19  
> 审计基线：`codex/knowledge-map-teacher-v1`，HEAD `a16cd2981d6ed8007ddda04ff7195994278b7231`  
> 审计范围：Repository Mapping、Build/Test、真实运行、Frontend/UI/UX、知识地图、Backend/API/Data、AI/RAG、Performance、Security、Testing、Engineering、开源项目联网对标  
> 变更边界：本轮只新增本报告；未修改业务代码、配置、测试或数据库。

## 1. Executive Summary

### 1.1 一句话评价

MentorFlow 已经不是“只有概念和几张页面的学生 Demo”，而是一套具有真实安全导入、证据链、知识地图、计划、主动教学、桌面端和角色/语音能力的高投入本地学习产品；但当前版本被过宽的产品边界、10k 级核心模块、双数据路径、红色全量测试基线、安全隔离缺口和缺失的开源工程门禁卡在“很有能力但尚不稳定、尚不可放心公开维护”的阶段。

### 1.2 核心结论

| 项目 | 结论 |
| --- | --- |
| 综合评分 | **55 / 100** |
| 学生项目标准 | **96 / 100** |
| 可公开展示 Portfolio 标准 | **86 / 100** |
| 优秀 GitHub 开源项目标准 | **55 / 100** |
| 可长期维护产品标准 | **49 / 100** |
| 当前最大优势 | 证据优先的导入、来源、候选审核、last-good revision 与 Provider 凭据边界，已经形成真正的产品差异化，而非简单调用一次 LLM。 |
| 当前最大缺陷 | 产品和代码同时扩张过快：核心链路聚集在 10,943 / 6,366 / 4,798 行模块中，当前后端 28 项、E2E 12 项失败，缺少 CI 让回归无法被发布流程阻断。 |
| 推荐方案 | **Plan B：推荐方案**。先恢复可信基线和安全/数据边界，再聚焦知识探索体验并渐进拆分；现在不应直接换图引擎或重写全栈。 |

### 1.3 TOP 5 必改项

1. 把当前 `28 backend failures + 12 E2E failures` 归因、修复或明确隔离，建立必过的 5 分钟快检与发布全量门禁。
2. 修复多用户模式下的远征共享 profile，并为 Provider `base_url` 增加云端 SSRF 目的地址策略。
3. 消除 legacy SQLite 与 ORM SQLite 的 best-effort 双写；在此之前至少增加对账、失败显式化和一致性测试。
4. 将 v1 地图缺失但回退到旧快照的状态显式标记，并补齐 150–1000 节点的搜索、聚焦、分层/折叠和性能基线。
5. 以用例边界拆分 `active_teaching.py`、`generic_knowledge_api.py` 与 `ActiveTeachingChainView.tsx`，先拆契约和副作用，不进行一次性重写。

### 1.4 最值得借鉴的三个项目

1. **Understand Anything**：借鉴增量图谱工件、搜索/层级/导览和“图是为了教会用户，不是为了显得复杂”的产品原则。
2. **GitNexus**：借鉴分阶段索引、预计算关系上下文、过期检测和大图 WebGL 探索模式；不要照搬代码专用 AST/PDG 或其非商业授权实现。
3. **LightRAG**：借鉴图 + 向量双路检索、增量删除、引用和可观测性；不要在当前阶段引入完整多存储/VLM/多解析器运维矩阵。

---

## 2. 审计方法、事实边界与证据等级

### 2.1 方法

- 读取当前磁盘源码、配置、脚本、测试与文档，不把 README 当实现事实。
- 记录 Git identity、工作树、真实进程和运行端口。
- 使用项目现有隔离测试脚本，测试数据库指向 `E:\Codex\Temp`，并校验两个真实数据库 hash 未改变。
- 使用打包 UI review 启动器和 Edge/Playwright 实际访问动态 loopback 端口。
- 对图谱执行真实 150 节点观察，以及 100/500/1000 节点 Dagre synthetic layout benchmark。
- 联网对标仅采用官方 GitHub 仓库、官方文档、源文件、测试/CI/许可证等一手资料。

### 2.2 证据等级

| 标记 | 含义 |
| --- | --- |
| VERIFIED-CODE | 当前 HEAD 源码直接证实。 |
| VERIFIED-RUNTIME | 当前机器实际运行、HTTP、浏览器、构建或测试证实。 |
| SYNTHETIC | 构造数据基准；只代表被测环节。 |
| UNVERIFIED | 本轮没有足够环境、数据或安全授权完成验证。 |

### 2.3 重要限制

- 未进行破坏性安全攻击、真实跨账户攻击、生产数据写入或外部 Provider 费用测试。
- Native Qt 知识地图源码已检查，但本轮没有自动化操作其全部 pan/zoom/候选交互：`UNVERIFIED`。
- FCP、LCP、CLS、INP 和浏览器 FPS 未取得可靠 trace：`UNVERIFIED`。
- 500/1000 节点数据只测 Dagre 布局算法，不等于 React DOM/SVG/Canvas 最终交互 FPS。
- 28 个后端失败包含代码回归、环境权限、Windows 路径预算和本地大型资产依赖等不同类型；不能把 28 全部直接称为产品 bug。

---

## 3. Repository Mapping

### 3.1 当前真实身份

仓库对外/目录名仍带有 `AI-Knowledge visualization`，但当前产品 README、桌面窗口和前端均以 **MentorFlow** 为产品名。这不是单纯的知识图谱展示器，而是“资料导入 → 目标/计划 → 知识地图 → 主动教学 → 练习/评估/掌握 → 复习/重排”的本地学习系统，并附带远征、AI 教师角色、2D/3D/语音运行时。

Evidence:

```text
README.md:1-36
frontend/src/App.tsx
desktop/main.py
Command: git status --short --branch; git rev-parse HEAD
Result: clean branch codex/knowledge-map-teacher-v1; HEAD a16cd298...
```

### 3.2 技术栈

| 层 | 当前实现 |
| --- | --- |
| Web Frontend | React 19、TypeScript 7、Vite 8、Zustand、React Router |
| Graph | `@xyflow/react` 12.11.3 + Dagre 3.1.1；另有 Qt 原生知识地图 |
| Rich media | Phaser、PixiJS、Three.js/VRM、Spine、Live2D、MediaPipe |
| Backend | FastAPI、Uvicorn、Pydantic 2、HTTPX |
| Data | legacy `sqlite3` (`data/app.db`) + SQLAlchemy 2/Alembic (`data/mentorflow_v2.db`) |
| Retrieval | jieba + BM25/term scoring；暂无 embedding/vector store |
| AI | Fake、OpenAI-compatible、Gemini、Anthropic；凭据引用、Provider session、结构化生成/评审 |
| Desktop | PySide6 / Qt 6；`desktop.main` 启动随机 loopback FastAPI 服务 |
| Document | TXT/Markdown/HTML/DOCX/PDF/URL/ZIP/OCR 等转换与安全预览 |
| Test | pytest、Vitest、Testing Library、Playwright、smoke/security scripts |
| Build/Release | PowerShell 脚本、Vite build、PyInstaller/本地 release 工件；无仓库 CI/Docker |

Evidence:

```text
frontend/package.json
backend/requirements.txt
backend/app/db.py:13-30
backend/app/orm.py:12-24
desktop/runtime.py:25-45
scripts/{bootstrap,dev,start,build,test,desktop}.ps1
```

### 3.3 简化架构

```text
用户
 ├─ PySide6 原生桌面壳 / Qt 原生页面
 └─ React Web UI（同源或 Vite 开发代理）
          ↓ HTTP / loopback dynamic port
FastAPI application
 ├─ legacy workspace/source/goal/plan/tutor routes (main.py)
 ├─ active teaching / classroom / code practice / focus
 ├─ generic knowledge map v1 / planning assistant / jobs
 ├─ local auth / provider session / credential reference
 ├─ teacher avatar / voice / expedition / narrative
 └─ secure ingestion / URL fetch / conversion adapters
          ↓
Service and policy layer
 ├─ chunking + hashes + source evidence
 ├─ jieba/BM25 retrieval + citation/grounding checks
 ├─ knowledge-map validation + staged revision + last-good activation
 ├─ plan/teacher turn/mastery/review/replan
 └─ exact resource reference registry / search and video adapters
          ↓
Data and external capabilities
 ├─ data/app.db (legacy sqlite3)
 ├─ data/mentorflow_v2.db (SQLAlchemy/Alembic)
 ├─ Windows Credential Locker
 ├─ OpenAI-compatible / Gemini / Anthropic providers
 ├─ controlled web fetch/search/video transcript
 └─ local voice/avatar/art/runtime packs
```

### 3.4 核心用户流

1. 创建/选择 workspace。
2. 导入文本、文件、URL、ZIP 或用户精确引用。
3. 安全预览、内容转换、chunk/hash/page evidence。
4. 目标与可行性分析，生成 plan preview，用户确认后激活。
5. 生成候选知识地图，质量门禁，用户审核关系，原子切换 last-good revision。
6. 主动教师按结构化 turn/state 推进讲解、例子、理解检查、纠错和练习。
7. 记录 mastery/mistake/review/progress，容量变化时 replan。

### 3.5 核心模块与热点

| 文件 | 行数 | 判断 |
| --- | ---: | --- |
| `backend/app/active_teaching.py` | 10,943 | 教学、材料、地图、状态、课堂等过多职责；最高维护风险。 |
| `backend/app/generic_knowledge_api.py` | 6,366 | 地图、规划、jobs、资源发现汇集；事务和后台任务边界难审。 |
| `backend/app/main.py` | 3,683 | 约 182 routes 的 legacy/application God module。 |
| `frontend/src/features/active-teaching/ActiveTeachingChainView.tsx` | 4,798 | 数据加载、地图、计划、教学 UI 与状态集中。 |
| `frontend/src/App.tsx` | 2,379 | 顶层导航、workspace、计划、多个产品面混合。 |
| `frontend/src/features/teachers/KoikatsuPmxRig.tsx` | 6,239 | 高复杂 3D/runtime 代码；需要独立加载和生命周期边界。 |
| `desktop/main_window.py` | 约 2,800 | 原生页面组装与跨功能协调中心。 |

### 3.6 工程与仓库资产

- `AGENTS.md`：项目树内未发现；本轮遵循任务运行时直接提供的 agent/worker/storage 指令。
- tracked inventory：backend 约 264、frontend 约 357、content 约 1,158 个文件。
- tests：约 105 个后端测试文件、35 个前端 test 文件、14 个 Playwright spec。
- 未发现项目级 `LICENSE`、`CONTRIBUTING.md`、`CODE_OF_CONDUCT.md`、`SECURITY.md` 或 `.github/workflows`。
- 未发现 Dockerfile、compose 或 `.env.example`。
- ignored/local 体积快照：release 约 3.98 GB、root `node_modules` 约 1.42 GB、runtime 约 984 MB、data 约 635 MB；这些是本地快照，不是 clone 体积。
- 根目录存在被 `.gitignore` 排除的 `MINIMAX_API_KEY.txt`；未读取内容、未被 Git 跟踪，不等于仓库泄漏。

---

## 4. Build、Test 与 Runtime Baseline

### 4.1 命令结果

| 检查 | 结果 | 耗时/数据 | 判定 |
| --- | --- | ---: | --- |
| Backend pytest | 1092 passed, 28 failed, 47 skipped, 12 warnings | 1410.25s | **FAIL** |
| Frontend Vitest | 34 files, 316 passed | 95.08s | PASS |
| TypeScript + Vite build | 3147 modules，exit 0 | 5.67s wrapper wall-clock；Vite log 3.05s | PASS with chunk warning |
| Playwright E2E | 8 passed, 12 failed, 8 skipped | 3.2 min | **FAIL** |
| smoke test | exit 0 | isolated DB | PASS |
| security smoke | exit 0 | isolated DB | PASS |
| Python `compileall` | exit 0 | — | PASS |
| `pip check` | exit 0 | — | PASS |
| `npm ls --depth=0` | exit 0 | — | PASS |
| `npm audit --omit=dev --audit-level=high` | 0 vulnerabilities | 2026-08-19 | PASS |
| Python lint/typecheck | ruff/mypy/pyright/black unavailable | — | UNVERIFIED / missing gate |

Evidence:

```text
Command: powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1
Result codes: 0,1,0,0,1,0,0
Evidence log: E:\Codex\Temp\ai-kv-audit-qa\scripts-test.log
Worktree before/after: clean
Live DB hashes: unchanged per scripts/test.ps1 guard
```

### 4.2 失败分组

后端 28 项失败不是一个单一问题：

- 核心行为/契约：feasibility、goal receipt、fast-track、knowledge map、provider handoff、W6 acceptance。
- Windows/环境：本地 KKS 资源权限、路径长度预算。
- Voice/Qwen release trust：manifest schema、trust policy 预期码、runtime ownership、cache stage 文件。
- 测试/路由漂移：部分期望请求得到 `405 Method Not Allowed`。

Playwright 的 12 项失败中，服务端反复出现：

```text
sqlite3.IntegrityError: UNIQUE constraint failed: orm_expedition_profiles.user_key
```

它会造成远征、闲聊、Provider、导入和 focus 流的连锁超时，因此不能把 12 项都当成彼此独立的页面 bug；但“单个服务端一致性问题能污染整个 E2E run”本身是严重稳定性证据。

### 4.3 Runtime

| 指标 | 当前基线 |
| --- | ---: |
| 隔离 Uvicorn 从启动到探针完成 | 3.688s |
| 第一次 `/api/health` | 1123.39ms |
| 后续 10 次 health | 10/10 HTTP 200 |
| 热态 health median / avg / max | 5.44 / 5.66 / 6.91ms |
| OpenAPI path 数（UI review runtime） | 271 |

开发脚本使用固定 `8000/5173`；原生桌面通过 `desktop.main` 使用随机 loopback 端口。审计过程中没有把历史 8000 进程当作当前代码证据。

---

## 5. Frontend、Product 与 UI/UX

### 5.1 第一印象

真实浏览器截图显示：

- 视觉语言统一，暖白底、粉/绿强调色、卡片、按钮、导航和数据面板一致。
- 首屏价值陈述明确：“把想学会变成今天能完成的事”。
- 空 workspace、任务、进度、Provider 状态均有可理解的面板。
- 390×844 下 `scrollWidth == innerWidth`，无横向溢出。
- Tab 可到达导航和主要按钮。

这部分已经达到成熟 Portfolio 水准，不应推倒重做。

Evidence:

```text
E:\Codex\Temp\ai-kv-audit-ux\desktop-home.png
E:\Codex\Temp\ai-kv-audit-ux\mobile-home.png
E:\Codex\Temp\ai-kv-audit-ux\current-course.png
```

### 5.2 主要产品体验问题

1. **产品面过多**：学习任务、闲聊、AI 角色库、进度、远征、Provider、设置、2D/3D/语音均在一个主产品中，知识地图与学习闭环的主叙事被削弱。
2. **首屏“先看知识地图”卡片不是直接操作控件**：`App.tsx:1052-1058` 的表现更像 CTA，但实际是普通容器，用户要自己推断去哪里。
3. **地图状态语义混淆**：v1 revision 不存在时显示错误，同时从 snapshot 渲染约 150 个节点，却没有“旧快照/只读 fallback”标签。
4. **角色预设真实 500**：`GET /api/teacher-avatar-card-presets` 返回 `HTTP_ERROR / 内置角色卡清单无法读取`。
5. **移动端顶栏信息密度仍高**：虽无溢出，但导航和 Provider 状态争夺首屏空间；需做任务优先级而非继续塞入口。

### 5.3 空、加载、错误、成功状态

| 状态 | 评价 |
| --- | --- |
| Empty | 首页/进度/闲聊基本明确；知识地图缺失与旧 snapshot 组合不明确。 |
| Loading | 多处有 busy/loading；超长 Provider/地图 job 的阶段化反馈仍不一致。 |
| Error | 全局 ErrorBoundary 存在；部分 `.catch(() => {})` 静默吞错，角色预设 500 能稳定返回业务错误但页面恢复能力不足。 |
| Success | 计划/候选/学习任务有成功反馈；跨页面“当前 workspace/current course”语义需要统一。 |

### 5.4 前端工程质量

正面：

- 使用 lazy import 隔离 TeacherAvatarStudio/Koikatsu/Phaser/MediaPipe 等重模块。
- Provider reconnect 有 cancellation/竞态保护。
- 图谱有键盘列表 fallback、React 渲染默认转义和 Markdown 安全验证。
- 测试覆盖规划、教学、语音、角色、图谱、Provider、归档等大量产品面。

问题：

- `App.tsx` 和 `ActiveTeachingChainView.tsx` 同时承担 orchestration、数据访问和渲染。
- API 返回大量 `any`，前端 `Workspace/Plan` 仍混用任意字典。
- `KnowledgeMapView.tsx`（列表/质量面板）与 active-teaching ReactFlow 图谱并存，产品入口和权威实现分裂。
- `vite.config.ts:3` 只 include `src/test/**`，因此 `features/teachers/kksSwitchLifecycle.test.ts` 的 3 个生命周期测试未被默认 Vitest 命令发现。

---

## 6. 知识地图 / 可视化专项

### 6.1 当前实现

主交互图位于 `ActiveTeachingChainView.tsx:462-563`：

- React Flow + Dagre 左到右布局。
- zoom/pan/fit、Controls、MiniMap。
- 节点/边点击后展示 evidence。
- 关系、审核状态、计划状态、掌握状态过滤。
- `model_common` 显示切换。
- 候选 confirm/reject。
- `nodesDraggable=false`、不可连线：定位为只读探索/审核图，而非编辑器。
- ResizeObserver 不可用时提供键盘列表 fallback。

Native Qt 图谱 `desktop/knowledge_map_view.py` 也实现 pan/zoom/fit、分层 DAG、tooltip/evidence；本轮未完成 GUI 自动化，交互结论为 `UNVERIFIED`。

### 6.2 实测交互矩阵

| 能力 | 结果 |
| --- | --- |
| Zoom / pan / fit | VERIFIED-RUNTIME（ReactFlow controls） |
| Node selection / evidence | VERIFIED-RUNTIME |
| Edge selection / evidence | VERIFIED-RUNTIME |
| Filter / model_common toggle | VERIFIED-RUNTIME |
| Candidate confirm/reject callback | VERIFIED-RUNTIME fixture；真实生产数据未写入 |
| Keyboard fallback | VERIFIED-RUNTIME |
| Search by node text | 缺失 |
| Focus selected / breadcrumb | 部分：选择可看详情，无强路径/面包屑 |
| Expand / collapse / clustering | 缺失 |
| Drag nodes | 明确禁用 |
| Large graph center/root recovery | MiniMap/fit 有帮助，但没有语义中心或返回路径 |

### 6.3 性能基线

SYNTHETIC Dagre，只测 layout：

| 节点 / 边 | median |
| ---: | ---: |
| 100 / 191 | 127.85ms |
| 500 / 991 | 678.83ms |
| 1000 / 1991 | 1543.19ms |

真实 UI 中约 150 节点 snapshot 在点击继续课程、等待 700ms 后 DOM 已出现；该观察不是严格加载时间或 FPS。

判断：

- **当前 React Flow 方案继续优化仍足够**，因为产品当前更缺搜索、聚焦、语义分层、折叠和增量布局，而不是 renderer 功能。
- 1000 节点 Dagre 已达约 1.54s，应先把 layout 放入 worker、缓存位置、按可见子图布局并减少重算。
- 只有当真实目标稳定超过约 2k–5k 可见节点，且浏览器 FPS/INP 基线证明 ReactFlow DOM 模式不达标时，才评估 Sigma.js/WebGL 或 G6 Canvas/WebGL。
- 不建议现在为“看起来先进”迁移 G6/Sigma；迁移会重做现有候选审核、证据、键盘 fallback 和业务组件。

### 6.4 图谱最重要的产品问题

不是“颜色不够炫”，而是图仍偏展示/审核，尚未成为高效知识探索器：

- 没有文本/语义搜索与命中聚焦。
- 没有按主题/章节/掌握度的聚类折叠。
- 没有从当前学习节点回溯先修路径的专用模式。
- 缺失 v1 revision 时，旧 snapshot 与红色错误并存且未标 stale。
- 大图只靠 fit/minimap，用户仍容易迷失。

---

## 7. Frontend Performance

### 7.1 Build 产物

| 产物 | minified | gzip |
| --- | ---: | ---: |
| `index-*.js` | 987.31 kB | 306.39 kB |
| `index-*.css` | 261.79 kB | 49.17 kB |
| Phaser chunk | 1,198.01 kB | 319.08 kB |
| KoikatsuPmxRig chunk | 2,403.19 kB | 434.53 kB |
| 全 dist，含大量资源 | 76.64 MiB raw | 约 65.2 MiB gzip sum |

Vite 明确警告多个 chunk 超过 500 kB。总 dist 不等于首屏下载，因为重模块已有 lazy loading，不能把 76.64 MiB 直接写成 Initial JS。

### 7.2 Web Vitals

| 指标 | 状态 |
| --- | --- |
| FCP | UNVERIFIED |
| LCP | UNVERIFIED |
| CLS | UNVERIFIED |
| INP | UNVERIFIED |
| 首屏 network waterfall | UNVERIFIED |
| Graph interaction FPS | UNVERIFIED |

下一轮必须先以固定设备/浏览器/数据集记录 baseline，再设数值目标。

### 7.3 统一性能问题表

| 测试 | 当前表现 | 问题 | 严重度 | 建议 |
| --- | ---: | --- | --- | --- |
| 隔离启动 | 3.688s；首次 health 1123ms | 冷启动主要阶段未拆分 | P2 | 分别记录 import、migration、runtime bootstrap、ready probe |
| 热态 health | median 5.44ms | 仅代表轻量探针，不代表核心 API | P3 | 对 workspace/map/plan/tutor 记录 p50/p95 |
| 主 JS | 987.31kB min / 306.39kB gzip | 首屏主 chunk 偏大 | P2 | route/feature/vendor split，目标先降 30% |
| Phaser lazy chunk | 1,198.01kB min | 首次进入远征成本高 | P2 | 保持专用入口 lazy，预加载只在明确意图后发生 |
| Koikatsu lazy chunk | 2,403.19kB min | 3D 首次进入成本和解析压力高 | P2 | 进一步拆 renderer/loader，独立性能预算 |
| Dagre 100 nodes | 127.85ms median, SYNTHETIC | 可接受但未含 React render | P3 | 作为小图基线保留 |
| Dagre 500 nodes | 678.83ms median, SYNTHETIC | 过滤/重算可感知卡顿 | P2 | worker、缓存、只布局可见子图 |
| Dagre 1000 nodes | 1543.19ms median, SYNTHETIC | 已超过流畅反馈窗口 | P1 | progressive render；Phase 0 后设 <800ms 门槛 |
| FCP/LCP/CLS/INP | UNVERIFIED | 无法判断首屏和交互体验 | P2 | 固定 Edge/设备/数据集采集 trace |
| Graph FPS | UNVERIFIED | 不能据此决定换图引擎 | P2 | 100/500/1000 节点采集平均 FPS/INP/内存 |

---

## 8. Backend Architecture 与 API

### 8.1 总体判断

后端能力丰富、策略和安全模块不少，但路由层、服务层和数据层边界被历史增长侵蚀。静态扫描约 293 个 route decorators、约 290 个唯一 method/path；仅找到 1 个显式 FastAPI `response_model`。大量 handler 使用 `dict[str, Any]` 和临时字典响应，使前后端契约漂移只能靠测试捕获。

### 8.2 分层

| 层 | 当前表现 |
| --- | --- |
| Controller/routes | `main.py`、`active_teaching.py`、`generic_knowledge_api.py` 过重，路由与业务/事务混合。 |
| Service/domain | secure ingestion、provider、knowledge policy、revision、grounding 等已有较好模块化。 |
| Repository/data | legacy sqlite 函数与 ORM Session 并存，部分写入采用 best-effort bridge。 |
| DTO/schema | 输入端有 Pydantic；输出端和内部状态大量 Any/dict。 |
| Errors | 有统一 `app_error_body`、request ID、redaction；仍有广泛 `except Exception: pass`。 |
| Auth | 本地 auth、cookie/Bearer、workspace ownership 已有；远征域没有绑定 user。 |

### 8.3 API 设计

优点：

- 主要错误包含 code/message/details/request_id。
- provider/session、knowledge map job、candidate decision、preview token 等有明确资源语义。
- 写路径通常使用 POST/PATCH/DELETE；导入预览/确认分离。

问题：

- legacy 与 active-teaching/generic-knowledge 路径并存，知识地图还存在多个 snapshot/map/revision 语义。
- 输出 schema、分页、sorting/versioning 不统一。
- Provider current 的 428 在多个页面被反复轮询，E2E log 中形成噪声。
- 一些后台任务由 FastAPI `BackgroundTasks` 或 `asyncio.create_task` 执行，重启语义和并发上限不清晰。
- `health()` 固定返回 `demo_mode: True`（`main.py:658-659`），与真实模式不符。

### 8.4 关键可靠性边界

1. `main.py:672-704` 等路径先写 legacy DB，再尝试 ORM commit，异常被吞掉；没有事务原子性或对账。
2. `generic_knowledge_api.py:3217-3227`、`3731+` 使用进程内 BackgroundTasks；job 虽有表，但执行队列不持久。
3. Provider sessions 存在进程内 `_sessions`，重启即丢；同时 `expires_at=None` 与短 TTL 文档不一致。
4. `expedition_service.py:99-104` 的 `_profile()` 在并发首次请求下先查后插，E2E 已复现唯一键竞争。

---

## 9. Database 与数据层

### 9.1 当前结构

- `data/app.db`：legacy sqlite3，workspace/source/goal/plan/chat 等历史路径。
- `data/mentorflow_v2.db`：SQLAlchemy/Alembic，知识地图、jobs、教学、计划执行、mastery、远征等。
- 当前本机文件约 18.2 MB 和 83.8 MB；只是本机 snapshot，不代表发布体积。

### 9.2 风险

| 风险 | 证据 | 严重度 |
| --- | --- | --- |
| 双写非原子 | `main.py:672-704`、`services.py:125-158` | P1 |
| 首次 profile 竞态 | E2E UNIQUE `orm_expedition_profiles.user_key` | P1 |
| migration 可逆性弱 | 部分 migration 使用 `create_all`/no-op downgrade | P2 |
| 数据模型跨域 | workspace ownership 与 expedition local profile 分裂 | P1 |
| N+1/full scan | 存在 Python 循环加载/汇总；本轮未取得 query plan | UNVERIFIED |
| 索引充分性 | 未对真实大数据运行 EXPLAIN | UNVERIFIED |

推荐路线不是马上换 PostgreSQL，而是先统一写模型、增加对账和并发测试。SQLite 对单机产品仍可成立。

---

## 10. AI / LLM / RAG 专项

### 10.1 AI 是否构成产品价值

**是。** 当前 AI 能力不只是一次 completion：

- Provider 抽象、验证 session、Credential Locker 引用和 raw key 拒绝。
- 资料转换、chunk/hash/page/quote evidence。
- exact resource reference registry，保留 BV/AV/URL/DOI/arXiv 身份与来源。
- 结构化知识地图 provider contract、质量门禁、候选审核、staged/last-good revision。
- 结构化 TeacherTurn、状态机、理解检查、错误纠正、mastery/replan。
- Grounding/citation 元数据和失败关闭路径。

这些能力已经是产品核心，重构时必须保留。

### 10.2 当前瓶颈

- 检索仍以 jieba + BM25/term matching 为主；跨文档语义召回弱。
- `grounding_service.py:16-21` 更偏引用形状/存在性检查，没有独立的 unsupported-claim entailment。
- 没有 embedding/vector、rerank 或 local/global graph query mode。
- Provider response JSON、token/cost/size 的统一上限和观测不足。
- 后台生成依赖进程生命周期。
- casual chat 远端错误会回退本地模板，但 response meta 有 `fallback_used`；对闲聊可接受，对教学主链不应复用该策略。

### 10.3 推荐演进

1. 先为当前 BM25 建立 recall benchmark 和可解释基线。
2. 再增加可选 embedding rerank，而不是直接替换 BM25。
3. 保留 page/quote/source identity，向量召回不能抹掉 provenance。
4. 对复杂跨文档问题增加 local/global 图查询或社区摘要实验，但先限于离线 benchmark。
5. 对所有生成统一记录 provider/model、latency、token、cache、revision、source coverage、fallback。

---

## 11. Security Review

### 11.0 Critical

本轮**未验证到 Critical**。没有发现被 Git 跟踪的真实密钥、无需用户交互的任意代码执行、可直接读取任意本机文件或可确认的数据批量破坏路径。该结论不代表所有未执行攻击面均安全。

### 11.1 High

#### SEC-H-01 Provider Base URL SSRF（云端/多用户条件）

- Evidence：`schemas.py:113-124` 只把 `base_url` 当字符串；`provider_runtime.py:61-90` 创建 session；`provider_service.py:203-214` 直接由 HTTPX 请求。
- 影响：若云端用户可配置 Base URL，服务端可访问 loopback、私网、metadata 或本机服务。
- 本地单用户模式：风险较低，因为用户本就控制本机；云端模式为 High。
- 建议：按部署模式限制 scheme/host/port，DNS resolve 后拒绝私网/loopback/link-local，重定向每跳复核，允许管理员显式 allowlist。

#### SEC-H-02 远征 profile 跨账户共享

- Evidence：`expedition_service.py:27-30` 固定 `USER_KEY='local'`；`99-104` 所有请求取同一 profile；auth workspace ownership 没有覆盖此域。
- 影响：启用多用户 auth 时，不同账户共享角色、抽卡、队伍和奖励状态。
- 建议：从 request principal 传 `user_id`，所有 expedition query 强制 scope；增加 A/B 两账户隔离 E2E。

### 11.2 Medium

#### SEC-M-01 XML DTD/entity probe 可绕过

- Evidence：`conversion_adapters.py:523-530` 只扫描 `signature_probe_bytes` 前缀，再由 `ElementTree.fromstring(raw)` 解析全部 XML。
- Safe reproduction：5666-byte XML 将 DTD 放在 4096 字节后，entity 被解析。
- 建议：使用 `defusedxml` 或解析器级禁用 DTD/entity，不依赖前缀字符串扫描。

#### SEC-M-02 多处上传先整体读入内存

- Evidence：`active_teaching.py:2063`，`main.py:3089,3194,3345,3363,3526,3583`。
- 影响：请求体虽可能最终被 size policy 拒绝，但读取峰值已发生。
- 建议：统一 bounded streaming reader、Content-Length 预检、逐块 hash/limit、入口级并发预算。

#### SEC-M-03 Provider response / code runner 资源上限不完整

- Evidence：Provider HTTP response 没有统一最大 body；`code_practice.py:598-668` 有时间边界但内存耗尽边界不足。
- 建议：stream + maximum bytes，子进程 Job Object/内存限制，输出上限。

#### SEC-M-04 Manifest DOM XSS 条件面

- Evidence：`ImageTeacherAdapter.ts:55-64` 将 `manifest.displayName` 插入 `innerHTML` attribute。
- 缓解：资产包存在信任/签名边界；若 manifest 可被未受信导入，风险上升。
- 建议：使用 DOM API 设置 `alt`/text，不拼 `innerHTML`。

### 11.3 Low

- 根目录本地 `MINIMAX_API_KEY.txt` 已被 `.gitignore` 排除且未跟踪；内容未读取。作为本机明文 secret hygiene 属 Low，不构成已证实泄漏。建议迁移到 Credential Locker 后删除本地明文副本。
- health/runtime 配置暴露的模式信息不准确，主要影响诊断而非直接机密性，归入 ISSUE-20/P3。

### 11.4 已验证的正面安全设计

- Provider API 不接受 raw key；只接受 Credential Locker reference。
- SQL 查询主要参数化。
- ZIP/import 有 traversal、symlink、成员数、深度、压缩比、总量等限制。
- URL fetch 有独立 secure fetcher。
- 通用错误输出有 request ID 和 redaction，500 未暴露 stack。
- `npm audit --omit=dev` 为 0；`pip check` 通过。
- 本轮未发现被 Git 跟踪的真实 API secret。

---

## 12. Testing 与 Engineering

### 12.1 测试体系评价

优点：

- 数量和领域覆盖远高于普通个人项目。
- 有 backend unit/integration、frontend component、Playwright E2E、smoke/security。
- `scripts/test.ps1` 使用临时 DB、临时上传目录，并 hash 校验真实 DB。
- 有真实失败路径、归档/安全/Provider/知识地图/语音 trust 边界测试。

主要问题：

- 当前全量基线红灯，且单次 backend 约 23.5 分钟。
- E2E 的一个数据库竞态可污染多个 case，缺少更强 fixture isolation/seed idempotency。
- 没有 CI，所以失败不阻断合并/发布。
- Vitest include 漏掉 feature colocated test。
- Python 缺少 lint/typecheck/formatter gate。
- 没有稳定的 graph performance/CWV regression suite。

### 12.2 最值得新增/修复的测试

1. 双数据库写入一侧失败时，对账与可恢复性。
2. 远征首次 profile 的 20–100 并发请求，必须只创建一行。
3. 两账户 expedition/provider/workspace 全域隔离。
4. 云端 Provider URL 对 loopback/private/link-local/DNS rebinding/redirect 的拒绝。
5. DTD 在任意偏移位置均被拒绝。
6. 上传超过上限时的峰值 RSS 和临时文件清理。
7. job 在 queued/running 时进程重启后的恢复。
8. 100/500/1000 节点图谱搜索、布局、选择、过滤性能。
9. v1 revision missing + legacy snapshot fallback 的状态标签与恢复操作。
10. 将 `kksSwitchLifecycle.test.ts` 纳入默认 Vitest discovery。

### 12.3 工程化缺口

| 能力 | 当前 |
| --- | --- |
| formatter/lint | TS/Python 无统一仓库门禁；Python 工具缺失 |
| typecheck | TS build 覆盖；Python 缺失 |
| pre-commit | 未发现 |
| CI | 未发现 `.github/workflows` |
| release | 本地脚本和大型 release 工件较多；可复现公开发布链不足 |
| container | 未发现 Docker/compose；本地桌面可不强制，但 cloud docs 缺可执行模板 |
| logging | request ID/error redaction 较好 |
| metrics/tracing | 缺少统一 latency/job/token/queue/DB metrics |
| OSS governance | 无 LICENSE/CONTRIBUTING/SECURITY/CODE_OF_CONDUCT |

---

## 13. 开源项目联网对标

检索日期均为 2026-08-19。选择依据是与当前产品的“知识图谱 + AI/RAG + 可视化探索 + 本地/证据”至少一项核心能力直接相关，而不是 Star 数。

### 13.1 Understand Anything

- Repository: https://github.com/Egonex-AI/Understand-Anything
- 为什么选：最接近“图谱用于理解/学习，而非只展示”的产品哲学。
- Architecture：Tree-sitter 结构事实 + LLM 语义，多 agent pipeline，增量 `.ua/knowledge-graph.json`。
- Frontend/Visualization：pan/zoom/search、层级、domain view、guided tours、diff impact。
- Engineering：MIT、tests、CI、CONTRIBUTING、SECURITY、多平台安装。
- 值得学：增量图工件、搜索/导览/层级、只读本地 viewer。
- 不适合照搬：代码 AST/导入图是特定领域；MentorFlow 的资料证据与学习状态不能退化成代码图模型。

### 13.2 GitNexus

- Repository: https://github.com/abhigyanpatwari/GitNexus
- Architecture source: https://raw.githubusercontent.com/abhigyanpatwari/GitNexus/main/ARCHITECTURE.md
- 为什么选：大图探索、hybrid retrieval、影响/路径分析和 WebGL 的成熟参考。
- Architecture：Tree-sitter 多阶段 pipeline、LadybugDB、worker pool、BM25 + semantic + RRF。
- Visualization：Sigma.js + Graphology WebGL；浏览器模式约受 5k files 内存限制，backend mode 扩展。
- Product：impact/blast radius/trace 让图谱回答具体问题，而不是只有画布。
- 值得学：预计算关系上下文、staleness、worker、搜索/路径工具。
- 不适合：代码专用 AST/PDG、跨仓库 agent hooks；许可证为 PolyForm Noncommercial，不能复制实现到目标开源产品。

### 13.3 LightRAG

- Repository: https://github.com/HKUDS/LightRAG
- 为什么选：当前 BM25 evidence retrieval 向 graph+vector 演进的最佳参考之一。
- Architecture：图 + vector 双层，local/global/hybrid/mix/naive modes，增量更新/删除。
- AI：rerank、引用、多解析器、角色化模型、多 storage backend。
- Engineering：pytest offline/integration，MIT。
- 值得学：双路检索、增量删除、明确 query mode、引用与观测。
- 不适合：一次引入多向量库、图数据库、MinerU/Docling/VLM 会显著增加桌面分发和运维成本。

### 13.4 Microsoft GraphRAG

- Repository: https://github.com/microsoft/graphrag
- Docs: https://microsoft.github.io/graphrag/index/architecture/
- 为什么选：图谱抽取、community report、local/global/DRIFT 查询的方法论基准。
- 优势：可配置 DAG、entity/relationship/claim、community/report、cache/factory。
- 局限：官方明确项目主要处于 maintenance mode，indexing 昂贵，并提醒从小规模开始。
- 值得学：评测方法、query taxonomy、缓存边界。
- 不适合：标准 per-chunk LLM + community report map-reduce 对当前本地桌面成本过高；GraphML→Gephi 也不是成熟集成产品 UX。

### 13.5 React Flow / xyflow

- Repository: https://github.com/xyflow/xyflow
- Docs: https://reactflow.dev/learn
- 为什么选：当前已经采用，属于最现实的继续优化基线。
- 优势：自定义节点/边、Controls/MiniMap、React 生态、可访问性、Playwright/Changesets。
- 结论：保留，先完成业务探索能力和性能测量。

### 13.6 AntV G6

- Repository: https://github.com/antvis/G6/tree/v5
- 为什么选：大图布局、Canvas/SVG/WebGL/SSR 和 GPU/Rust/WASM 路线参考。
- 优势：多 renderer、丰富 layout/interaction/plugin、React nodes。
- 结论：作为 2k+ 节点性能候选，而非现在迁移目标。

### 13.7 次级参考：Cytoscape.js / Sigma.js

- Cytoscape.js: https://github.com/cytoscape/cytoscape.js
- Sigma.js: https://github.com/jacomyal/sigma.js
- 用途：前者适合成熟图分析/布局生态，后者适合 WebGL 大图渲染；都不能替代产品层的 evidence/review/mastery 交互设计。

### 13.8 六个主要对标项目的八维复核

下面把每个主要对标项目按同一八维模板展开。未在一手资料中确认的能力明确标 `UNVERIFIED`，不从相邻项目或通用印象推断。

#### Understand Anything

| 维度 | 观察 | 对 MentorFlow 的含义 |
| --- | --- | --- |
| Architecture | Tree-sitter 提取确定性结构，LLM 生成摘要/层级/领域/导览；多 agent pipeline；增量 JSON 图工件。 | 借鉴“确定性事实与语义生成分层”，不要让 LLM 决定所有边。 |
| Frontend | 本地只读 dashboard，persona detail、domain view、guided tours。 | 地图应围绕不同用户任务组织视图，而非只有一张画布。 |
| Visualization | pan/zoom/search、architecture layer、domain flow、force knowledge graph。 | 优先补 search/layer/tour；与当前 ReactFlow 能力兼容。 |
| Backend | viewer 可只读本地图文件；分析 pipeline 由插件/脚本生成工件，非长期在线 API 服务。 | 不适合照搬为 MentorFlow backend，但“生成工件与查看解耦”值得学。 |
| AI | LLM 负责摘要、标签、领域映射、导览；结构边来自 parser。 | 对资料图也应让证据/规则负责确定性边界，LLM 只做候选。 |
| Performance | file analyzers 分批并发，默认增量只重算变化文件；官方提醒首次大仓库 token 成本高。 | 增量 material/map 索引比更换 renderer 更优先。 |
| Engineering | MIT、CI、Vitest/Python tests、CONTRIBUTING、SECURITY、多平台 installer。 | 是当前仓库 OSS 基础设施的直接基准。 |
| Product | 明确主张“graphs that teach”，提供搜索、导览、影响和 onboarding 具体任务。 | MentorFlow 应把图与“今天学什么/为什么”绑定。 |

#### GitNexus

| 维度 | 观察 | 对 MentorFlow 的含义 |
| --- | --- | --- |
| Architecture | typed 多阶段 Tree-sitter pipeline、worker pool、LadybugDB、registry、MCP/backend bridge。 | 借鉴阶段合同、staleness 和 worker；不照搬代码图 schema。 |
| Frontend | React/Vite explorer + chat；browser 与 CLI/backend 两种模式。 | 可借鉴轻量本地 viewer 与强 backend 分层，但当前桌面不需要双实现继续扩张。 |
| Visualization | Sigma.js + Graphology WebGL，cluster/trace/impact；浏览器模式官方说明约受 5k files 内存限制。 | 若真实可见节点跨入数千级，再以它作为 WebGL 迁移基准。 |
| Backend | CLI 持久 LadybugDB，bridge mode 让 Web UI 读取本地索引；连接 lazy open/evict。 | 可借鉴持久索引/连接生命周期，不必引入独立图 DB。 |
| AI | BM25 + semantic + RRF，预计算关系上下文，MCP/agent 使用结构化 impact/trace。 | 借鉴“先计算上下文再交给模型”，减少多轮盲查。 |
| Performance | native/WASM 双实现、worker threads/Web Workers、buffer pool/timeout/WAL 参数；有明确规模边界。 | MentorFlow 需要先建立自己的规模合同和 worker budget。 |
| Engineering | testing/architecture 文档、CI/Playwright、贡献/安全文件；许可证为 PolyForm Noncommercial。 | 只能学模式，不能直接复制不兼容授权实现。 |
| Product | 把图转化为 impact/blast radius/trace 等任务，而非图本身。 | 将知识图转化为先修路径、薄弱点和下一步建议。 |

#### LightRAG

| 维度 | 观察 | 对 MentorFlow 的含义 |
| --- | --- | --- |
| Architecture | KV/vector/graph 双层检索与可替换 storage；local/global/hybrid/mix/naive query modes。 | 先实验 optional vector/rerank，保留当前 SQLite/BM25 fallback。 |
| Frontend | 有 WebUI/API server，但核心价值在 RAG framework；本轮未对 UI 成熟度做浏览器评测：`UNVERIFIED`。 | 不把它作为 UI 设计模板。 |
| Visualization | 不是主要 integrated explorer 基准：`UNVERIFIED`。 | 不用它证明当前应换图引擎。 |
| Backend | async ingestion/query、多个 storage backend、并发和 cache 配置。 | 借鉴接口与观测，不引入全套 backend matrix。 |
| AI | dual-level graph/vector、rerank、citations、多 chunk strategy、增量删除。 | 是当前 BM25 向语义/图检索演进的首要 AI 参考。 |
| Performance | 官方强调减少 community-report/multi-hop LLM 调用和增量更新成本；具体跨项目可比延迟未复测：`UNVERIFIED`。 | 必须用本项目数据集 benchmark，不能引用对方宣传数值定目标。 |
| Engineering | MIT、pytest offline/integration、文档和多部署配置。 | 借鉴 offline/online test 分层。 |
| Product | 更像 RAG 基础设施而非个人学习产品。 | 学检索能力，不学产品 IA。 |

#### Microsoft GraphRAG

| 维度 | 观察 | 对 MentorFlow 的含义 |
| --- | --- | --- |
| Architecture | 可配置 DAG：chunk、entity、relationship、claim、community、report、embedding。 | 可借鉴离线 evaluation taxonomy，不照搬全 pipeline。 |
| Frontend | 核心项目没有 MentorFlow 式 integrated product UI：`UNVERIFIED`；官方给出外部可视化指南。 | 不作为前端产品模板。 |
| Visualization | 主要导出 GraphML 并借助 Gephi 等工具。 | 说明算法 pipeline 强不等于产品探索体验成熟。 |
| Backend | Python data pipeline、cache/factory extension points、query engine。 | 可借鉴 stage cache 和可重跑边界。 |
| AI | local/global/DRIFT 等图增强查询方法，entity/community report 依赖 LLM。 | 可小规模验证跨文档全局问题，不直接用于所有学习资料。 |
| Performance | 官方明确 indexing 昂贵并建议从小规模开始；跨项目可比数值未复测。 | 成本模型必须作为采用门禁。 |
| Engineering | MIT、pytest/typecheck/CI、正式文档；仓库明确处于 maintenance mode。 | 学方法和工程，不把它作为活跃产品路线图。 |
| Product | 研究方法/数据转换套件，不是正式支持的终端产品。 | 不应拿它的功能列表替代 MentorFlow 用户研究。 |

#### React Flow / xyflow

| 维度 | 观察 | 对 MentorFlow 的含义 |
| --- | --- | --- |
| Architecture | React 组件、hooks/store、可定制 node/edge/viewport。 | 与当前前端一致，迁移成本最低。 |
| Frontend | React 原生组合，自定义节点/toolbar/panel。 | 继续把 evidence/review/mastery 作为业务组件。 |
| Visualization | Controls、MiniMap、Background、selection、custom edges 等；当前项目已使用核心能力。 | 当前缺口主要在业务探索，不在基础控件。 |
| Backend | 无后端/RAG：不适用。 | 不能解决 job、检索或数据一致性。 |
| AI | 无 AI：不适用。 | AI 合同必须留在 MentorFlow domain。 |
| Performance | 官方提供性能实践，但本轮未取得与本项目同数据的官方对比数值：`UNVERIFIED`。 | 先优化 memo/可见子图/layout worker，再测 renderer 上限。 |
| Engineering | MIT、monorepo、Playwright、Changesets、文档/API reference。 | 借鉴组件回归和版本管理。 |
| Product | 通用节点编辑/展示 substrate，不定义知识学习体验。 | 保留 substrate，产品价值由 MentorFlow 构建。 |

#### AntV G6

| 维度 | 观察 | 对 MentorFlow 的含义 |
| --- | --- | --- |
| Architecture | TypeScript graph engine，layout/behavior/plugin 分层，支持 React node。 | 可作为未来 renderer abstraction 候选。 |
| Frontend | 有 React ecosystem 和主题/组件，但不是完整学习产品。 | 迁移仍需重做当前业务 UI glue。 |
| Visualization | Canvas/SVG/WebGL/SSR、多 layout/interaction/plugin、3D 扩展。 | 对数千节点和多 layout 更有潜力。 |
| Backend | 无 RAG/backend：不适用。 | 不解决后端瓶颈。 |
| AI | 无 AI：不适用。 | 不解决检索、证据或 hallucination。 |
| Performance | 部分 layout 使用 GPU/Rust 并行；支持 Canvas/WebGL；本轮没有同机同图对比 benchmark：`UNVERIFIED`。 | 只有 ReactFlow 未达性能门槛时才做 spike。 |
| Engineering | MIT、Jest/Playwright、Changesets、版本化文档。 | 借鉴 renderer benchmark 和发布工程。 |
| Product | 通用可视化引擎，能力丰富但容易诱发“为了换库而换库”。 | 当前列为 Not Recommended，保留技术验证选项。 |

---

## 14. Feature Gap Analysis

| 能力 | MentorFlow | Understand Anything | GitNexus | LightRAG | GraphRAG | 是否值得引入 |
| --- | --- | --- | --- | --- | --- | --- |
| 安全多格式导入 | 强：ZIP/OCR/页码/hash/预览 | 非重点 | Repo/ZIP | 多解析器 | 文本 pipeline | 保留并强化 |
| 来源证据/候选审核 | 强 | 节点代码/摘要 | 代码关系 | 引用 | claims/community | 核心差异，不得破坏 |
| 图谱搜索 | 缺 | 强 | 强 | 非 UI 核心 | 外部可视化 | **高价值** |
| 导览/学习路径 | 计划有，图内弱 | Guided tours | trace/impact | query mode | local/global/DRIFT | **高价值** |
| 大图渲染 | ReactFlow/Dagre，未测 FPS | 交互 dashboard | Sigma WebGL | 非核心 | Gephi | 测量后决定 |
| 语义/向量检索 | 无 | 语义搜索 | BM25+vector+RRF | 强 | embeddings | **中高价值，渐进引入** |
| 增量索引/删除 | revision 有，检索索引弱 | 强 | staleness/incremental | 强 | cache pipeline | 值得 |
| 持久任务队列 | DB job + 进程内执行 | pipeline | workers | concurrency | DAG | **高价值** |
| 账户隔离 | workspace 较好，expedition 缺 | 本地 | 本地 | 部署自定 | 部署自定 | **发布前必须** |
| CI/OSS 工程 | 缺 | 强 | 强 | 强 | 强 | **发布前必须** |
| 3D/角色/远征 | 很强但分散焦点 | 无 | 无 | 无 | 无 | 不再扩张，证明用户价值后保留 |

“别人有”不等于“我们需要”。当前最值得引入的是搜索/聚焦、增量语义检索、持久 job、CI 和隔离；最不值得的是为了技术炫耀直接引入完整 GraphRAG、多数据库或 3D 大图。

---

## 15. 评分

| 类别 | 权重 | 得分 | 主要理由 / 扣分 |
| --- | ---: | ---: | --- |
| Architecture | 12 | 6 | 领域能力丰富；双 DB、进程内 job/session、God modules。 |
| Code Quality | 12 | 6 | 安全/策略模块较好；Any/dict、吞异常、超大函数/文件。 |
| Frontend Engineering | 10 | 7 | React/TS/lazy/test 扎实；顶层组件和状态/API 混合，test discovery 漏项。 |
| UI / UX | 10 | 8 | 真实 UI 成熟、移动端无溢出；产品面过宽、地图 fallback 语义和 live 500。 |
| Visualization | 12 | 7 | ReactFlow、evidence、filter、keyboard fallback；缺搜索/聚类/路径，大图性能不足。 |
| Backend Engineering | 10 | 5 | 功能深；290 级 API、仅 1 response_model、后台任务/事务边界弱。 |
| Performance | 10 | 4 | 热 API 快且 lazy chunk 已做；1000-node layout 1.54s、chunk 大、CWV/FPS 无基线。 |
| Security | 8 | 4 | 凭据、归档、redaction 做得好；云端 SSRF、跨账户远征、XML/上传资源风险。 |
| Testing | 8 | 4 | 测试很多且隔离脚本优秀；当前 28 backend + 12 E2E 失败。 |
| Engineering / DevOps | 4 | 1 | 有本地脚本；无 CI、pre-commit、容器模板。 |
| Documentation | 4 | 3 | 设计/安全/桌面文档丰富；历史 handoff 噪声和 OSS 入门材料缺失。 |
| **总计** | **100** | **55** | 严格按当前可发布基线评分。 |

### 四种标准

- **学生项目：96 / 100** — 远超常规课程项目，功能深度、测试量和安全意识突出。
- **Portfolio：86 / 100** — 视觉和能力非常能展示；红色测试与产品叙事分散影响可信度。
- **优秀开源项目：55 / 100** — 无 LICENSE/CI/贡献/安全流程，当前默认验证不绿。
- **长期维护产品：49 / 100** — 数据一致性、账户隔离、后台任务、回归和可观测性尚未达到长期运营要求。

---

## 16. TOP 20 Issues

### ISSUE-01 当前全量验收基线为红

**标题：** 当前全量验收基线为红  
**严重度：** P1  
**证据：** backend 1092 pass / 28 fail；Playwright 8 pass / 12 fail。  
**当前实现：** `scripts/test.ps1` 串联 backend/frontend/build/E2E/smoke/security，但没有 CI，当前 aggregate exit 1。  
**问题：** 大量测试存在不一致，且没有 CI 阻断。  
**实际影响：** 任何发布结论都不可信；新改动无法区分自身回归与既有失败。  
**建议方案：** 建立 failure taxonomy 和 baseline allowlist；核心快检必须全绿，旧环境/大型资产测试移入明确慢检。  
**预计收益：** 恢复可判断的工程基线。  
**修改范围：** tests、fixtures、scripts、CI。  
**预计风险：** 不应为了绿灯删除真实断言。

### ISSUE-02 远征 profile 未按账户隔离

**标题：** 远征 profile 未按账户隔离  
**严重度：** P1  
**证据：** `expedition_service.py:27-30,99-104`。  
**当前实现：** 所有 expedition 请求都通过固定 `USER_KEY='local'` 读取同一 profile。  
**问题：** 固定 `USER_KEY='local'`。  
**实际影响：** 多用户 auth 下跨账户共享状态/奖励。  
**建议方案：** 所有 expedition service 接收 principal user_id 并强制查询 scope。  
**预计收益：** 修复真实数据隔离边界。  
**修改范围：** expedition service/models/routes/tests。  
**预计风险：** 需要迁移现有 local profile。

### ISSUE-03 云端 Provider Base URL 可形成 SSRF

**标题：** 云端 Provider Base URL 可形成 SSRF  
**严重度：** P1  
**证据：** `schemas.py:113-124`、`provider_runtime.py:61-90`、`provider_service.py:203-214`。  
**当前实现：** Provider session 验证凭据引用和必填字段，但直接把用户提供的 Base URL 交给 HTTPX。  
**问题：** 缺少部署感知的目的地址策略。  
**实际影响：** 云端用户可借服务端访问私网/metadata。  
**建议方案：** DNS/IP/redirect 每跳校验 + admin allowlist。  
**预计收益：** 关闭云部署高风险入口。  
**修改范围：** provider URL policy/service/security tests。  
**预计风险：** 需保留本地 Ollama 等合法 loopback 使用场景。

### ISSUE-04 双 SQLite best-effort 双写缺乏一致性合同

**标题：** 双 SQLite best-effort 双写缺乏一致性合同  
**严重度：** P1  
**证据：** `main.py:672-704`、`services.py:125-158`，异常吞掉。  
**当前实现：** 部分用例先写 legacy sqlite，再 best-effort 写 SQLAlchemy ORM，两个 commit 不在同一事务。  
**问题：** 一侧提交成功、一侧失败时没有回滚或对账。  
**实际影响：** workspace、source、plan/retrieval 可能看到不同状态；实际不一致尚未复现，标记 `UNVERIFIED`。  
**建议方案：** 选定单一 source of truth；过渡期用 outbox/repair job 和一致性扫描。  
**预计收益：** 降低最难定位的数据漂移。  
**修改范围：** data/repository/service/migration。  
**预计风险：** 高，必须渐进迁移。

### ISSUE-05 进程内后台任务与 session 缺少重启语义

**标题：** 进程内后台任务与 session 缺少重启语义  
**严重度：** P1  
**证据：** `generic_knowledge_api.py:3217-3227,3731+`；`provider_runtime.py` `_sessions`。  
**当前实现：** FastAPI BackgroundTasks/`asyncio.create_task` 执行工作，Provider session 只存在当前进程内。  
**问题：** API 进程承担队列；Provider session 重启即丢，且 `expires_at=None`。  
**实际影响：** 长任务可能中断，UI 与文档的 TTL/恢复语义不一致。  
**建议方案：** SQLite-backed worker lease/recovery；session 明确短 TTL 或 desktop reconnect contract。  
**预计收益：** 可恢复、可观测。  
**修改范围：** jobs/provider runtime/startup recovery。  
**预计风险：** 中高。

### ISSUE-06 核心模块已经达到 God module 规模

**标题：** 核心模块已经达到 God module 规模  
**严重度：** P1  
**证据：** 10,943 / 6,366 / 4,798 / 3,683 行热点。  
**当前实现：** 路由、业务规则、事务、状态和渲染集中在少数超大文件。  
**问题：** 路由、事务、状态机、UI、副作用混合。  
**实际影响：** Review 难、测试慢、改一处影响多域。  
**建议方案：** 以 use case + contract 拆出 material、map、plan、teaching、job；保持 API 兼容。  
**预计收益：** 明显降低变更半径。  
**修改范围：** backend active/generic/main；frontend active view。  
**预计风险：** 高，禁止大爆炸重写。

### ISSUE-07 首次远征 profile 存在并发唯一键竞态

**标题：** 首次远征 profile 存在并发唯一键竞态  
**严重度：** P1  
**证据：** Playwright 多次 `UNIQUE constraint failed: orm_expedition_profiles.user_key`。  
**当前实现：** `_profile()` 先 SELECT，无结果再 INSERT，没有原子 upsert 或冲突重读。  
**问题：** `SELECT then INSERT` 非原子。  
**实际影响：** E2E 连锁超时，真实首次并发请求可 500。  
**建议方案：** DB upsert/捕获 IntegrityError 后重读；加并发测试。  
**预计收益：** 恢复 E2E 与远征稳定性。  
**修改范围：** expedition repository/service/tests。  
**预计风险：** 低中。

### ISSUE-08 上传上限在完整读入后才检查

**标题：** 上传上限在完整读入后才检查  
**严重度：** P1  
**证据：** `active_teaching.py:2063`，`main.py:3089,3194,3345,3363,3526,3583`。  
**当前实现：** 多个 route 直接 `await file.read()`，各自再交给解析/策略函数。  
**问题：** size policy 不能阻止读取峰值。  
**实际影响：** 大文件/并发上传造成内存压力或 DoS。  
**建议方案：** 统一 bounded streaming reader。  
**预计收益：** 可预测内存。  
**修改范围：** upload helpers/routes。  
**预计风险：** 中。

### ISSUE-09 XML entity 防护可被偏移绕过

**标题：** XML entity 防护可被偏移绕过  
**严重度：** P1  
**证据：** 5666-byte safe reproduction；`conversion_adapters.py:523-530`。  
**当前实现：** 只在 `signature_probe_bytes` 前缀扫描 DTD/entity，再解析完整 XML。  
**问题：** 只扫描 XML 前缀。  
**实际影响：** entity expansion 资源消耗。  
**建议方案：** parser-level DTD/entity disable/defusedxml。  
**预计收益：** 移除文档导入解析漏洞类别。  
**修改范围：** conversion adapters/tests。  
**预计风险：** 低。

### ISSUE-10 大图探索能力和性能基线不足

**标题：** 大图探索能力和性能基线不足  
**严重度：** P1  
**证据：** 1000-node Dagre median 1543ms；缺搜索/折叠/路径。  
**当前实现：** ReactFlow 提供 fit/minimap/filter/evidence，Dagre 对全部可见节点同步布局。  
**问题：** 图在中大规模仍偏展示。  
**实际影响：** 用户迷失，地图不能高效回答“我在哪里/为什么/下一步”。  
**建议方案：** 搜索聚焦、先修路径、主题折叠、worker/cached layout、FPS/INP gate。  
**预计收益：** 最直接提升核心产品价值。  
**修改范围：** graph selector/layout/UI/tests。  
**预计风险：** 中。

### ISSUE-11 v1 缺失与旧 snapshot 同屏但不标 stale

**标题：** v1 缺失与旧 snapshot 同屏但不标 stale  
**严重度：** P2  
**证据：** `ActiveTeachingChainView.tsx:1179-1184,1397,3671-3674`；真实约 150 节点。  
**当前实现：** v1 read 失败时保留 error，同时 `knowledgeMapV1 || normalizeKnowledgeMapV1(graph)` 渲染 snapshot。  
**问题：** 红色错误旁仍显示完整地图，用户无法判断权威 revision。  
**实际影响：** 可能基于旧图学习/审核。  
**建议方案：** 显式 `fallback_snapshot` 状态、来源/revision、恢复/重新生成 CTA。  
**预计收益：** 提升可信度。  
**修改范围：** API state + UI。  
**预计风险：** 低。

### ISSUE-12 角色预设接口真实返回 500

**标题：** 角色预设接口真实返回 500  
**严重度：** P2  
**证据：** `GET /api/teacher-avatar-card-presets` → 500 `内置角色卡清单无法读取`；route `main.py:3505+`。  
**当前实现：** route 调用本地 preset registry，当前 UI review 打包环境无法读取内置清单。  
**问题：** 打包/本地资源清单与运行态不一致。  
**实际影响：** AI 教师角色库功能不完整。  
**建议方案：** 启动时验证资源清单；缺失时明确降级并提供诊断。  
**预计收益：** 修复可见功能。  
**修改范围：** preset registry/package/tests。  
**预计风险：** 低中。

### ISSUE-13 API 输出契约弱

**标题：** API 输出契约弱  
**严重度：** P2  
**证据：** 约 290 method/path，仅 1 个 `response_model`；当前 AST 扫描发现 93 个 route function 的 `body` annotation 含 `dict`，其中 54 个精确为 `dict[str, Any]`。  
**当前实现：** 输入端部分使用 Pydantic，输出和许多 request body 仍使用 Any/dict。  
**问题：** Any/dict 让 schema drift 到运行期才暴露。  
**实际影响：** 前端重复 normalize、E2E 对文案/形状敏感。  
**建议方案：** 先覆盖 TOP 20 核心 API 的 typed request/response/error envelope。  
**预计收益：** 更可靠的前后端集成。  
**修改范围：** Pydantic schemas/OpenAPI/frontend client。  
**预计风险：** 中。

### ISSUE-14 Retrieval 仍是 BM25，Grounding 只验证引用形状

**标题：** Retrieval 仍是 BM25，Grounding 只验证引用形状  
**严重度：** P2  
**证据：** `retrieval_service.py:12-29`、`grounding_service.py:16-21`。  
**当前实现：** 以 jieba/BM25/term score 从 workspace chunks 检索，critic 检查 citation 存在/形状。  
**问题：** 语义召回和 unsupported claim 检测不足。  
**实际影响：** 跨文档/同义问题召回弱，引用存在不等于回答被证据蕴含。  
**建议方案：** benchmark 后加 embedding rerank + claim-evidence review。  
**预计收益：** 提升真实 AI 质量。  
**修改范围：** retrieval/eval/provider policy。  
**预计风险：** 中高、涉及成本。

### ISSUE-15 初始/重型 JS chunk 过大

**标题：** 初始/重型 JS chunk 过大  
**严重度：** P2  
**证据：** main 987kB、Phaser 1.2MB、Koikatsu 2.4MB minified；Vite warning。  
**当前实现：** 重媒体已有部分 lazy import，但主 bundle 和专用 chunk 仍大。  
**问题：** 首屏主 bundle 仍接近 1MB，重功能首次进入成本高。  
**实际影响：** 低端设备和桌面 WebView 首次交互变慢。  
**建议方案：** route/feature split、vendor split、只在角色/远征入口加载运行时。  
**预计收益：** 首屏和导航响应。  
**修改范围：** frontend imports/build config。  
**预计风险：** 低中。

### ISSUE-16 产品范围扩张削弱主叙事

**标题：** 产品范围扩张削弱主叙事  
**严重度：** P2  
**证据：** 主导航同时承载任务、闲聊、角色、掌握、远征、Provider；大量 avatar/voice/KKS/runtime 模块。  
**当前实现：** 学习主线与角色、远征、3D、语音等增强面并列为主导航和首页内容。  
**问题：** 核心“证据驱动学习路径”被多套子产品稀释。  
**实际影响：** 新用户不知道最重要的第一步和长期价值。  
**建议方案：** 将资料→地图→计划→今日学习设为主线，角色/远征做可关闭增强层。  
**预计收益：** 第一眼产品感提升。  
**修改范围：** IA/navigation/onboarding。  
**预计风险：** 需要产品取舍。

### ISSUE-17 尚不具备开源发布基本合同

**标题：** 尚不具备开源发布基本合同  
**严重度：** P1（针对开源目标）  
**证据：** 无项目 LICENSE、CI、CONTRIBUTING、SECURITY、CODE_OF_CONDUCT。  
**当前实现：** 仓库有大量技术/交接文档和本地发布脚本，但没有公开协作治理文件。  
**问题：** 法律许可、贡献流程和安全报告渠道缺失。  
**实际影响：** 不能称为可安全协作的开源项目。  
**建议方案：** 选定许可证、建立治理文件和 required CI。  
**预计收益：** 开源发布可行。  
**修改范围：** repo meta/docs/workflows。  
**预计风险：** 许可证必须由所有者决策。

### ISSUE-18 Python 静态质量门禁缺失

**标题：** Python 静态质量门禁缺失  
**严重度：** P2  
**证据：** ruff/mypy/pyright/black 未安装，CI 不存在。  
**当前实现：** Python 主要依赖 pytest/compileall，TS 类型检查由 build 覆盖。  
**问题：** 大型 Python 模块缺少最基本的自动反馈。  
**实际影响：** import、死代码、类型和格式问题只能靠 pytest。  
**建议方案：** ruff + pyright/mypy 渐进 baseline，只对 touched files 收紧。  
**预计收益：** 缩短反馈。  
**修改范围：** pyproject/scripts/CI。  
**预计风险：** 不应一次启用全仓严格导致噪声。

### ISSUE-19 Vitest discovery 漏掉 colocated 生命周期测试

**标题：** Vitest discovery 漏掉 colocated 生命周期测试  
**严重度：** P2  
**证据：** `vite.config.ts:3` include `src/test/**`；`features/teachers/kksSwitchLifecycle.test.ts` 在范围外。  
**当前实现：** 默认测试命令只发现集中式 `src/test`，而该功能把测试放在实现旁。  
**问题：** 3 个模型切换/释放/可见性测试未默认执行。  
**实际影响：** 3D 资源泄漏/竞态保护可能回归而不被发现。  
**建议方案：** include `src/**/*.{test,spec}.{ts,tsx}` 或移动测试。  
**预计收益：** 关闭实际 discovery 缺口。  
**修改范围：** vite config。  
**预计风险：** 低。

### ISSUE-20 运行状态和环境配置不可信

**标题：** 运行状态和环境配置不可信  
**严重度：** P3  
**证据：** `health()` 总是 `demo_mode=True`；开发语音路径硬编码 `E:\AI_Tools\...`；README 固定端口而 desktop 随机端口。  
**当前实现：** health、README、dev script 和 desktop runtime 分别暴露不同且部分静态的运行信息。  
**问题：** 诊断输出不能准确说明实际模式，跨机器启动依赖隐含。  
**实际影响：** 运维/用户误判，复现困难。  
**建议方案：** typed runtime capabilities endpoint、`.env.example`/config template、启动诊断列出实际端口/模式/可选依赖。  
**预计收益：** 降低环境问题。  
**修改范围：** health/config/docs/scripts。  
**预计风险：** 低。

---

## 17. 当前项目做得最好的 10 个地方

1. **安全导入合同**：preview token、hash/mtime、ZIP traversal/symlink/bomb/size/depth 等防护。应保留并统一到所有上传入口。
2. **来源与证据优先**：页码、quote、source/chunk identity、candidate review 是产品核心，不应被向量化重构抹掉。
3. **知识地图 last-good revision**：staged → quality gate → review → atomic activation，明显优于直接覆盖。
4. **精确资源引用注册表**：BV/AV/URL/DOI/arXiv 统一身份，用户提供资源不被泛搜相似内容替换。
5. **Provider 凭据边界**：raw key 拒绝、Credential Locker reference、session verification、公开响应只给 host/reference。
6. **真实学习闭环**：目标、计划、容量、教学、掌握、错题、复习、replan，不是孤立 chat 页面。
7. **UI 视觉成熟度**：统一色彩/卡片/层级、明确价值主张，移动端无横向溢出。
8. **图谱 evidence 与可访问 fallback**：节点/边证据、候选审核、键盘列表，重构图引擎时必须保留。
9. **测试隔离脚本**：临时 DB、真实 DB hash guard、backend/frontend/e2e/smoke/security 串联，设计方向正确。
10. **Fail-closed 倾向**：Provider、知识地图能力、voice/runtime trust 多处拒绝伪造 ready/fallback，这比“总能演示”更有长期价值。

---

## 18. 改造价值矩阵

### Quick Wins

| 建议 | Impact | Effort | Risk |
| --- | ---: | ---: | ---: |
| 修复 expedition upsert 竞态 | 5 | 2 | 2 |
| 修复 avatar preset 500 | 4 | 2 | 2 |
| 标注 stale/fallback map revision | 5 | 2 | 1 |
| XML parser-level 禁 DTD/entity | 5 | 2 | 1 |
| 纳入遗漏 Vitest | 3 | 1 | 1 |
| health 返回真实 mode/capabilities | 3 | 1 | 1 |
| 新增 graph search/focus | 5 | 3 | 2 |

### Medium Improvements

| 建议 | Impact | Effort | Risk |
| --- | ---: | ---: | ---: |
| 核心 API response schemas | 5 | 3 | 2 |
| 上传统一 bounded streaming | 5 | 3 | 3 |
| 快检/慢检 CI 分层 | 5 | 3 | 2 |
| Provider SSRF deployment policy | 5 | 3 | 3 |
| 账户化 expedition migration | 5 | 3 | 3 |
| graph worker/cache/collapse | 5 | 4 | 3 |
| 可选 embedding rerank | 4 | 4 | 3 |

### Strategic Refactors

| 建议 | Impact | Effort | Risk |
| --- | ---: | ---: | ---: |
| 统一 data source / 移除 best-effort 双写 | 5 | 5 | 5 |
| 持久 job worker + restart recovery | 5 | 4 | 4 |
| 以 use case 拆三大 God modules | 5 | 5 | 4 |
| 重构产品信息架构，增强层可关闭 | 5 | 4 | 3 |

### Not Recommended

- 当前立即迁移 G6/Sigma。
- 当前引入完整 GraphRAG community-report pipeline。
- 同时引入独立 vector DB、graph DB、queue、cache server。
- 重写全部前端设计系统。
- 为追求覆盖率删除/弱化失败断言。
- 把角色/远征/3D 继续扩张为更多主导航入口。

---

## 19. 三个改造等级

### Plan A：轻量优化

目标：不改总体架构，在 1–2 个短迭代恢复可见质量和可信度。

- 修复 expedition 竞态、avatar preset 500、XML bypass、Vitest discovery、health mode。
- 地图 stale/fallback 标签 + search/focus。
- 分类当前 28/12 failures，核心 smoke/E2E 恢复全绿。
- 增加最小 CI：frontend test/build、backend core、security smoke。

预计收益：第一眼和日常开发明显改善。  
风险：双 DB、后台任务和 God modules 仍保留。

### Plan B：推荐方案

目标：在不重写产品的前提下，真正提升一个层级。

- 包含 Plan A。
- 修复账户隔离和 SSRF policy。
- 统一核心 API schema/error contract。
- 建立持久 job lease/recovery 和 Provider reconnect/TTL contract。
- 先为双 DB 增加 outbox/repair/audit，再逐域迁移单一 source of truth。
- 拆分 material/map/plan/teaching use cases。
- 图谱加入主题折叠、先修路径、worker layout 和 1000-node performance gate。
- 基于 benchmark 增加可选 embedding rerank。
- 完成 LICENSE/CONTRIBUTING/SECURITY/CI/release 文档。

预计收益：从“高投入个人产品”进入“可持续公开维护项目”。  
风险：需要 2–4 个阶段持续控制兼容性。

### Plan C：理想版本

目标：把能力范围内的最佳版本定义为“证据驱动的个人学习操作系统”，而不是功能集合。

- 产品：首页只突出资料→地图→计划→今天；角色/远征作为增强层。
- 页面：Library、Knowledge Explorer、Plan、Today/Classroom、Progress、Settings 六大域。
- Frontend：feature routes + typed API client + domain stores；重媒体完全按路由加载。
- Backend：use-case services、typed API、单一 transactional store、persistent local job worker。
- Graph：可搜索、主题层级、先修/影响路径、折叠、证据和 mastery overlay；真实超过阈值再切 WebGL。
- AI：BM25 + optional embeddings + graph-aware retrieval + claim-evidence evaluator；所有生成可追溯。
- Data：revisioned source/map/plan、增量索引、账户 scope、备份/恢复。
- Test：5 分钟 core gate、nightly full、1000-node perf、restart/security/upgrade tests。
- Deployment：桌面一键、可选 localhost web、明确 cloud mode；不强制微服务化。
- OSS：许可证、贡献、安全、版本、迁移、SBOM 和可复现 release。

---

## 20. 实施 Roadmap

### Phase 0 — Baseline

- **Goal**：把当前失败和性能变成可重复基线。
- **Tasks**：failure taxonomy；固定三组数据集；记录 bundle/CWV/API/job/graph；定义 core vs slow。
- **Files**：`scripts/test.ps1`、pytest markers、Playwright config、benchmark scripts。
- **Expected result**：每个失败有 owner/category/reproduction。
- **Acceptance criteria**：core gate ≤5min 且 100% pass；full gate 输出 JSON/JUnit；真实 DB hash 永不变化；固定设备记录 100/500/1000 graph baseline。
- **Risk**：把环境失败错误归类为“可忽略”。
- **Priority**：P0（实施顺序，不等同 ISSUE P0）。

### Phase 1 — Quick Wins

- **Goal**：恢复最明显的稳定性与信任。
- **Tasks**：profile upsert、avatar 500、XML、Vitest discovery、health、stale map 标签。
- **Files**：expedition service、preset registry、conversion adapters、vite config、health、active map UI。
- **Expected result**：核心页面无已知 500，状态语义明确。
- **Acceptance criteria**：对应回归全绿；20/50/100 并发 profile 只一行；DTD 任意偏移被拒；缺 v1 时 UI 必须显示 snapshot revision 和“只读旧快照”。
- **Risk**：打包资源路径在开发/发布环境不同。
- **Priority**：P0。

### Phase 2 — Frontend / UX

- **Goal**：强化主线，降低功能噪声。
- **Tasks**：重排 IA；主 CTA；typed core client；route-level split；统一 empty/error/job state。
- **Files**：App、routes、active-teaching client/stores、styles。
- **Expected result**：首次用户明确下一步，角色/远征不压过学习主线。
- **Acceptance criteria**：新用户从首屏到导入/继续课程/地图均 ≤2 次主操作；主 bundle gzip <220kB（先以当前 306kB 为 baseline）；关键页面 Playwright 全绿。
- **Risk**：导航变化影响熟悉用户。
- **Priority**：P1。

### Phase 3 — Graph Experience

- **Goal**：把地图从展示变成探索工具。
- **Tasks**：搜索聚焦、breadcrumb、先修路径、主题折叠、layout worker/cache、可见子图。
- **Files**：KnowledgeMapV1 panel/layout/selectors/CSS/tests；Qt parity 后续。
- **Expected result**：150–1000 节点仍能定位和理解。
- **Acceptance criteria**：1000 节点搜索结果 <100ms；重复 filter 不重新全量 layout；1000-node layout worker wall <800ms 或有 progressive render；固定设备交互平均 ≥45 FPS、INP <200ms（在 Phase 0 设备上）。
- **Risk**：过早优化 synthetic 数据。
- **Priority**：P1。

### Phase 4 — Backend / API

- **Goal**：建立清晰、一致、可恢复的核心服务。
- **Tasks**：TOP 20 API schema；job lease/recovery；Provider TTL/reconnect；双 DB audit/outbox；账户化 expedition。
- **Files**：schemas、routes、job/provider runtime、repositories、migrations。
- **Expected result**：API 可生成稳定 client，重启不丢关键 job。
- **Acceptance criteria**：核心 API 100% 有 response model/error schema；queued/running 重启测试通过；双写任一侧失败有 repair receipt；两账户隔离测试全绿。
- **Risk**：迁移和兼容性。
- **Priority**：P1。

### Phase 5 — Performance

- **Goal**：降低真实首屏、重功能和生成等待成本。
- **Tasks**：bundle split、资源按需、bounded upload、provider response cap、DB query profiling、job concurrency。
- **Files**：Vite config/imports、upload/provider helpers、queries/metrics。
- **Expected result**：低端设备和大资料更稳定。
- **Acceptance criteria**：首屏 transfer 与 LCP 相比 Phase 0 至少下降 30%；无入口上传可超过 policy+1 chunk 的内存；API p95 和 job queue 可观测；无 >1MB minified JS chunk，除明确隔离且只在专用入口加载的 3D runtime。
- **Risk**：拆包过细导致请求瀑布。
- **Priority**：P1。

### Phase 6 — Testing / Security

- **Goal**：把风险变成自动门禁。
- **Tasks**：CI fast/nightly/release；SSRF/account/XML/upload/restart tests；ruff/typecheck；dependency audit。
- **Files**：`.github/workflows`、test suites、pyproject、security policy。
- **Expected result**：不可发布状态自动阻断。
- **Acceptance criteria**：PR core gate ≤10min；nightly full 100% pass 或仅有带 expiry/owner 的 quarantine；0 High security findings；npm/pip audit 与 SBOM 在 release 执行。
- **Risk**：Windows/大型资产 runner 成本。
- **Priority**：P0/P1。

### Phase 7 — Product Polish

- **Goal**：达到公开发布和长期维护标准。
- **Tasks**：LICENSE、CONTRIBUTING、SECURITY、CODE_OF_CONDUCT、release notes、安装/迁移/备份、demo dataset、telemetry opt-in policy。
- **Files**：repo root/docs/release workflow。
- **Expected result**：陌生贡献者可独立安装、验证、贡献。
- **Acceptance criteria**：干净 Windows 环境按文档一次成功；所有 license/third-party notices 完整；新用户任务测试能说清产品价值和第一步；发布包有签名/校验/迁移说明。
- **Risk**：许可证和签名需要项目所有者决策。
- **Priority**：P1。

---

## 21. 最终十问

### Q1 当前最大的技术瓶颈是什么？

核心领域已经超过单模块和进程内协调能安全承载的规模：双 DB、进程内 job/session、10k 级模块与弱 API schema 共同放大回归和一致性风险。

### Q2 当前最大的产品体验瓶颈是什么？

不是视觉，而是主线被功能面稀释；知识地图又缺搜索/路径/折叠和权威状态说明，用户看到“很多能力”但不总能快速知道下一步与当前证据版本。

### Q3 哪些代码已经写得不错，不应该动？

安全导入 policy、URL fetch 边界、exact reference registry、Credential Locker/raw-key rejection、knowledge-map quality/candidate/last-good revision、source evidence identity、TeacherTurn 结构化状态与真实 DB hash test guard。

### Q4 哪些部分“能用但明显还是学生项目水平”？

God modules、`dict[str, Any]` API、`except Exception: pass` 双写、固定 `demo_mode=True`、硬编码本机语音路径、无 CI/许可证，以及“测试很多但默认基线红”。

### Q5 哪些改动最能让用户第一眼感觉提升一个档次？

聚焦首页主线、地图搜索/聚焦/先修路径、明确 stale/fallback、消除可见 500、将角色/远征降为可选增强层。

### Q6 哪些改动最能让专业开发者认为工程质量提升一个档次？

全绿且分层的 CI、typed core API、账户隔离、持久 job recovery、双 DB 一致性合同、God module 渐进拆分和性能/security regression gates。

### Q7 哪些开源设计值得借鉴？

Understand Anything 的增量图和导览；GitNexus 的阶段化索引/预计算/搜索路径；LightRAG 的图+向量和增量删除；React Flow 的可访问交互；G6/Sigma 的大图 renderer 思路。

### Q8 哪些先进实现不适合本项目？

GitNexus 的 AST/PDG/LadybugDB 全套、GraphRAG 的昂贵 community report、LightRAG 多存储/VLM 全家桶、未测基线前的 G6/Sigma 迁移、微服务拆分。

### Q9 只投入 20% 成本，做哪 20%？

修复当前红色 core/E2E、profile 竞态/隔离、SSRF/XML/upload、avatar 500、stale map；加 CI；图谱补 search/focus。不要先动数据库大迁移或图引擎。

### Q10 最终推荐技术路线？

保留 React + FastAPI + PySide6 + SQLite 的单机优先路线；以 typed use-case services、单一 transactional store、SQLite-backed durable worker、BM25 + optional embedding rerank、ReactFlow 业务探索增强为主。只有真实规模证明需要时，图渲染切 WebGL；只有 cloud 规模证明需要时，数据/队列外置。

---

## 22. Evidence Appendix

### 22.1 关键本地命令

```powershell
git status --short --branch
git rev-parse HEAD
rg --files
powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1
npm.cmd --prefix .\frontend run test -- --run
npm.cmd --prefix .\frontend run build
npm.cmd --prefix .\frontend audit --omit=dev --audit-level=high
.\.venv\Scripts\python.exe -m pip check
```

### 22.2 运行/浏览器证据

```text
UI review dynamic port: 50608 (stopped after audit)
GET /api/health: 200
GET /api/teacher-avatar-card-presets: 500
OpenAPI: /api/active-teaching/workspaces/{workspace_id}/knowledge-map is registered
Knowledge map missing response: KNOWLEDGE_MAP_REVISION_NOT_FOUND
Graph fixture: node/edge evidence, filters, keyboard fallback, XSS payload not executed
```

### 22.3 QA 证据文件

```text
E:\Codex\Temp\ai-kv-audit-qa\scripts-test.log
E:\Codex\Temp\ai-kv-audit-qa\frontend-test.log
E:\Codex\Temp\ai-kv-audit-qa\frontend-build.log
E:\Codex\Temp\ai-kv-audit-qa\dagre-benchmark.log
E:\Codex\Temp\ai-kv-audit-qa\runtime-baseline-final.json
E:\Codex\Temp\ai-kv-audit-qa\dependency-check.log
```

### 22.4 清理说明

所有本轮服务/浏览器进程和 8012/8014/50608/5175/5176 监听均已停止。`E:\Codex\Temp\mentorflow-test-run-962424a6005440689164dde7cf1b29ce` 是本轮隔离测试生成的临时目录；测试脚本因结束时锁句柄未删除，Root 的精确路径递归清理又被运行时安全策略拒绝，因此未绕过策略。它不在项目目录、不是生产数据，证据日志另保留在 `E:\Codex\Temp\ai-kv-audit-qa`。

---

## 23. Final Recommendation

不要把下一轮定义为“重构整个项目”，也不要先换图引擎。先执行 Plan B 的前两段：

1. 恢复可信的 core/E2E 绿色基线。
2. 修复账户/SSRF/XML/upload/job/data consistency 等边界。
3. 用 search/focus/path/stale-state 把知识地图变成真正的知识探索器。
4. 以 use case 渐进拆分热点模块，并用 typed API/CI 固化边界。

如果只能选择一个起点：**先修 expedition profile 竞态和当前 E2E 连锁失败，并建立 CI core gate；随后立即处理地图权威状态与搜索聚焦。** 这条顺序同时恢复工程可信度和用户可感知价值。
