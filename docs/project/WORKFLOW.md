# MentorFlow — 员工调度（架构师维护）

只记员工、任务、基线和交付状态；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)，这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)：先查索引再改，一条事实只写一处，冲突原位替换。

## 协作方式

- 架构师（Claude）写任务 → 用户转发给员工 → 员工在自己工位完成 → 用户把回执贴回 → 架构师审查、合入主目录、本地提交，审查通过后推送 GitHub。员工不改主目录、不推送。
- 核心部分（前端、教师/判定/检验编排）由架构师自己做；员工做边界清晰、耗时的单模块任务。
- **基线**：每次合入后在主目录本地提交，提交编号即“当前基线”。每条任务第一步都把工位切到指定基线，保证所有人从同一份代码开工。
- **测试位置**：测试放在 `tests/refactor/<模块>/`，refactor 里只放产品文件。Python 统一用 `D:\MentorFlow\.venv\Scripts\python.exe`（服务器任务除外，见其任务）。
- **工位代码**：每个工位下的 `repo` 目录是主仓库的 git worktree。员工在自己的分支 `office/<工位名>` 上提交，回执写明提交编号；架构师在主仓库直接审查该分支。

## 当前基线

| 项目 | 值 |
| --- | --- |
| 基线提交 | `399e9c4`（2026-09-27，main，含第一批全部成果与 services 包骨架）；第一批用的是 `6cca892` |
| 说明 | 第二批（MF-01～MF-07 各两项）从 `399e9c4` 开工；原拟的 T08–T14 未发出，已由第二批取代 |
| 署名 | 2026-09-27 main 上的本地提交统一改为 tonyswift868 + GitHub 隐私邮箱后重写：`6cca892` 在 main 上对应 `d7879ac`（内容完全相同）。员工分支仍基于 `6cca892`，合并时按内容压缩合入。office/* 分支只在本地，永不推送。 |

## 员工与工位

| 员工 | 工位 | 状态 |
| --- | --- | --- |
| MF-03（client） | D:\MentorFlow-office\client | 空闲 |
| MF-07（server） | D:\MentorFlow-office\server | 空闲 |
| MF-02（runtime） | D:\MentorFlow-office\runtime | 空闲 |
| MF-04（platform） | D:\MentorFlow-office\platform | 空闲 |
| MF-01（library） | D:\MentorFlow-office\library | 空闲 |
| MF-06（classroom） | D:\MentorFlow-office\classroom | 空闲 |
| MF-05（tools） | D:\MentorFlow-office\tools | 空闲 |

工位名只是称呼，不限制能接的任务类型。用户按 MF-01～MF-07 转发任务；每批尽量给每人塞满不冲突的活（用户 2026-09-27 要求）。架构师在员工完成本阶段任务前只做审查与集成，之后再做前端与核心编排。

## 任务索引

| 任务 | 员工 | 内容 | 关联需求 | 基线 | 状态 |
| --- | --- | --- | --- | --- | --- |
| T01 | library | 资料导入与正文提取（PDF/Word/网页/文本，带页码章节，分块） | PLAN-09、DATA-02 | 6cca892 | 已合入 f06cd21 |
| T02 | runtime | 多厂商模型接入 + 自带搜索 + 用量统计 | SYS-02、SYS-03、SYS-01 | 6cca892 | 已合入 dd2e0b1 |
| T03 | client | 时长账本纯逻辑 | PLAN-05、PLAN-08、TEACH-05 | 6cca892 | 已合入 dbb42b7 |
| T04 | platform | 搜索结果过滤 + 白名单/屏蔽名单初稿 | PLAN-06 | 6cca892 | 已合入 9fa6239 |
| T05 | tools | 语音识别（SenseVoice，边说边出字，首次使用下载） | VOICE-01 | 6cca892 | 已合入 36e777d |
| T06 | classroom | 老师朗读（GPT-SoVITS + 弗洛洛，常驻提速） | VOICE-01 | 6cca892 | 已合入 6935b93 |
| T07 | server | 服务器：通用同步记录、白名单/价格表接口、单设备登录（不部署） | ARCH-05、ACC-01、PLAN-06、SYS-03 | 6cca892 | 已合入 97630fe |
| 2-MF01 | MF-01 library | 扫描 PDF 识别 + PPT/EPUB/Excel | PLAN-09 | 399e9c4 | 已派发 |
| 2-MF02 | MF-02 runtime | 用量与价格 + 新模型调用入口（预设迁移） | SYS-02、SYS-03、SYS-05 | 399e9c4 | 已派发 |
| 2-MF03 | MF-03 client | 掌握度引擎 + 新数据库与读写层（实现约定 1） | ARCH-03、TEACH-02、CHK-01 | 399e9c4 | 已派发 |
| 2-MF04 | MF-04 platform | 资料验证流水线 + 名单/价格表客户端（约定 2） | PLAN-06、ARCH-06 | 399e9c4 | 已派发 |
| 2-MF05 | MF-05 tools | 语音术语纠正 + 每日刷新时钟与提醒 | VOICE-01、SYS-04、TEACH-05 | 399e9c4 | 已派发 |
| 2-MF06 | MF-06 classroom | 通用下载器与朗读底模下载 + 嘴型数据 | VOICE-01、DATA-02 | 399e9c4 | 已派发 |
| 2-MF07 | MF-07 server | 既有失败测试与部署包 + 同步客户端（调用约定 1）与挤下线检测 | ARCH-05、ACC-01 | 399e9c4 | 已派发 |

任务原文见下方「任务详情」；架构师自己的工作不列在这里。

第一批审查记录（2026-09-27）：7 个分支改动均在允许范围内；因 main 署名重写后与员工分支不共享历史，改为把 `6cca892..office/<工位>` 的差异按补丁逐个应用到 main（内容逐文件核对一致），唯一冲突为 T01/T05 同时追加依赖清单，已合并两段。合并后全部客户端测试 389 通过、2 跳过（真实麦克风识别需指定模型目录），服务器相关 115 项通过。架构师修正 1 处测试隔离问题：T01 的“无 Qt 导入”检查改在独立子进程执行。

## 回执格式

~~~text
【员工｜任务号｜完成/部分完成/受阻】
基线：开工时切到的提交编号。
提交：分支 office/<工位名> 上的提交编号。
改动：修改/新增的文件。
验证：实际运行的命令与结果（通过/失败/跳过）；用了替身的写明。
未完成：没做完或拿不准的地方。
~~~

## 任务详情

每条任务的正文就是发给员工的原文。

### [T01] library｜资料导入与正文提取

~~~text
【MentorFlow 任务 T01｜员工 library｜资料导入与正文提取】

你是 MentorFlow 项目的外包开发。只做本任务，只改“允许修改的文件”里列出的文件。

一、开工同步（第一步必须做）
PowerShell 执行：
  cd D:\MentorFlow-office\library\repo
  git status                      # 应该是干净的；不干净就停下，在回执里说明
  git checkout office/library
  git reset --hard 6cca892
repo 是主仓库的 git worktree，你的分支是 office/library。禁止 push、禁止切到 main、禁止修改 D:\MentorFlow 主目录。
Python 用 D:\MentorFlow\.venv\Scripts\python.exe（共用环境，不要卸载或升级已有包）。
先读：repo 根目录的 AGENTS.md；docs/project/CURRENT_STATE.md 里的 [PLAN-09]、[DATA-02]、[PLAN-04] 条目（按编号搜索）。

二、背景
产品要从用户资料里提炼知识点、出题、标出处，所以需要一个可靠的“文件/网页 → 带位置的正文 → 分块”模块。现有 refactor/learning/text_import.py 只支持 TXT/MD/CSV/JSON，不要改它，新写一个独立模块。

三、要做什么
新建 refactor/learning/course/extract.py（纯 Python，不依赖 Qt，不读写数据库），提供：

  @dataclass(frozen=True) class TextBlock:
      text: str; kind: str            # 'heading' | 'paragraph' | 'list' | 'table' | 'code'
      page: int | None                # PDF 为 1 起的页码，其他格式为 None
      section: tuple[str, ...]        # 所在标题路径，如 ('第3章 大语言模型基础', '3.2 提示工程')
  @dataclass(frozen=True) class SourceChunk:
      chunk_id: str                   # f"{sha256前12位}:{序号:04d}"，同一文件多次提取结果完全一致
      text: str; page_start: int | None; page_end: int | None
      section: tuple[str, ...]; char_start: int; char_end: int   # 在全文拼接文本中的位置
  @dataclass(frozen=True) class ExtractedDocument:
      title: str; media_type: str; sha256: str; char_count: int
      blocks: tuple[TextBlock, ...]; chunks: tuple[SourceChunk, ...]; warnings: tuple[str, ...]
  class ExtractError(Exception): 有 code 属性，取值：
      UNSUPPORTED_TYPE, TOO_LARGE, ENCRYPTED, SCANNED_NO_TEXT, CORRUPT, EMPTY, CANCELLED；message 为给用户看的中文说明
  def extract_file(path, *, max_bytes=50*1024*1024, cancel=None) -> ExtractedDocument
  def extract_bytes(data: bytes, filename: str, *, max_bytes=..., cancel=None) -> ExtractedDocument
  def extract_html(html: str | bytes, url: str, *, cancel=None) -> ExtractedDocument
  def chunk_blocks(blocks, *, source_sha256: str, target_chars=1200, max_chars=2000) -> tuple[SourceChunk, ...]
  cancel 为 threading.Event 或 None；长循环中检查，已设置就抛 ExtractError('CANCELLED')。

支持格式：
  - .pdf：用 pypdf 逐页取文字，记录页码；加密且无法空密码打开 → ENCRYPTED；平均每页有效字符少于 20 → SCANNED_NO_TEXT（扫描件本期不做 OCR）。
  - .docx：用 python-docx；Heading 样式作为标题并形成 section 路径；表格按行转为“单元格 | 单元格”文本，kind='table'。
  - .md / .markdown：# 标题形成 section；``` 代码块 kind='code'，整块不拆。
  - .txt / .csv / .json：按 utf-8、utf-8-sig、gb18030 顺序尝试解码；全部失败 → CORRUPT。
  - .html / .htm 及 extract_html：用 lxml 去掉 script/style/nav/header/footer/aside/form 等，尽量只留正文；h1–h6 形成 section；title 取 <title> 或第一个 h1。
  - 其他后缀 → UNSUPPORTED_TYPE。超过 max_bytes → TOO_LARGE。提取后没有文字 → EMPTY。
分块规则：按块顺序拼接；尽量在段落或句子边界（。！？.!? 和换行）切分；单块目标 target_chars、上限 max_chars；标题和其后第一段放在同一块；代码块和表格不从中间切（除非单个超过 max_chars，此时按行切）；每块记录跨越的页码范围和所在 section。

依赖：把 pypdf 和 python-docx 装进共用环境（D:\MentorFlow\.venv\Scripts\python.exe -m pip install pypdf python-docx），在 refactor/desktop/requirements-account-shell.txt 末尾加一段注释“# Material text extraction (PLAN-09)”并写上实际安装的精确版本号（==）。

四、允许修改/新增的文件
  refactor/learning/course/extract.py（新建）
  refactor/desktop/requirements-account-shell.txt（只在末尾追加上述几行）
  tests/refactor/extract/ 下的测试和小体积测试样本（每个样本 < 200 KB；PDF/Word 样本优先在测试里用代码生成）

五、不要做
  不改其他任何文件（包括 refactor/learning/course/__init__.py、text_import.py、store.py）；不做 OCR；不联网；不引入 Qt。

六、验收（全部要有测试并通过）
  D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor/extract -q -p no:cacheprovider
  必须覆盖：多页中文 PDF 的页码正确；无文字 PDF → SCANNED_NO_TEXT；加密 PDF → ENCRYPTED；docx 标题路径和表格；md 标题和代码块不被切开；gb18030 编码 txt；带导航/脚本噪声的 html 只留正文；超大文件 → TOO_LARGE；未知后缀 → UNSUPPORTED_TYPE；同一输入两次提取的 chunk_id 与内容完全一致；分块不超过 max_chars；cancel 生效。
  另外手动跑一次：挑一份真实的中文 PDF（任意公开教材/论文均可，不要提交），在回执里写出页数、块数、耗时和一段示例输出。

七、交付
  git add 上面允许的文件
  git commit -m "T01: material text extraction with page/section positions"
回执（填好后发给用户）：
【library｜T01｜完成/部分完成/受阻】
基线：6cca892
提交：office/library 上的提交编号
改动：文件列表
依赖：安装的包及精确版本
验证：命令与结果（通过/失败/跳过数量）；真实 PDF 手测结果
未完成：没做完或拿不准的地方
~~~

补充澄清（2026-09-27，回复 MF-01）：允许用 pip 从官方源安装 pypdf、python-docx 及其必需依赖，不升级/卸载已有包；“不联网”指 extract.py 运行和测试时不访问网络。T05 安装 sherpa-onnx 同理，且模型下载功能本身按任务要求联网。

### [T02] runtime｜多厂商模型接入 + 自带搜索 + 用量统计

~~~text
【MentorFlow 任务 T02｜员工 runtime｜多厂商模型接入、自带搜索与用量统计】

你是 MentorFlow 项目的外包开发。只做本任务，只改“允许修改的文件”里列出的文件。

一、开工同步（第一步必须做）
PowerShell 执行：
  cd D:\MentorFlow-office\runtime\repo
  git status                      # 应该是干净的；不干净就停下，在回执里说明
  git checkout office/runtime
  git reset --hard 6cca892
repo 是主仓库的 git worktree，你的分支是 office/runtime。禁止 push、禁止切到 main、禁止修改 D:\MentorFlow 主目录。
Python 用 D:\MentorFlow\.venv\Scripts\python.exe（共用环境，已有 httpx；不要装新包）。
先读：repo 根目录 AGENTS.md；docs/project/CURRENT_STATE.md 的 [SYS-02]、[SYS-03]、[SYS-01]、[PLAN-06]、[SYS-05] 条目；现有 refactor/learning/connections/transport.py 和 errors.py（只读，学习它的安全做法：URL 校验、禁止重定向、错误分类、取消）。

二、背景
产品需要支持多家模型，并用各家“自带的联网搜索”找学习资料；用户只填一个 API Key。DeepSeek 已经实测可用：它的 Anthropic 兼容接口 https://api.deepseek.com/anthropic/v1/messages 带工具 {"type":"web_search_20250305","name":"web_search","max_uses":N} 能在服务端搜索。实测脚本与完整响应（只读参考、可复制进测试样本）：
  E:\Codex\Temp\MentorFlowTests\search-benchmark\deepseek_search_probe.py
  E:\Codex\Temp\MentorFlowTests\search-benchmark\results\with_tool.json
其余厂商你没有 Key，只能按官方文档实现 + 用模拟响应测试，全部标记“未实测”。

三、要做什么
新建包 refactor/learning/connections/providers/，至少包含 __init__.py、base.py（数据类型与错误）、registry.py（厂商表）、以及按协议划分的适配文件（如 openai_chat.py、anthropic_messages.py、openai_responses.py、gemini.py，按需增减）。对外接口：

  @dataclass(frozen=True) class SearchSupport:
      kind: str            # 该厂商搜索的接入方式简述，如 'anthropic_server_tool' / 'openai_enable_search' / 'builtin_function' 等
      verified: bool       # 只有 deepseek 为 True
      doc_url: str; checked_on: str; notes: str
  @dataclass(frozen=True) class ProviderSpec:
      id: str              # 'deepseek','qwen','kimi','zhipu','openai','anthropic','gemini'
      display_name: str; protocol: str; default_base_url: str; default_model: str
      search: SearchSupport | None
  REGISTRY: dict[str, ProviderSpec]
  def detect_provider(base_url: str) -> str | None       # 按域名识别厂商
  @dataclass(frozen=True) class Usage:
      input_tokens: int; cached_input_tokens: int; uncached_input_tokens: int
      output_tokens: int; reasoning_tokens: int; search_requests: int
      raw: dict                                          # 厂商原始 usage，便于核对
  @dataclass(frozen=True) class WebHit: url: str; title: str; snippet: str
  @dataclass(frozen=True) class ModelResult:
      text: str; usage: Usage; provider_id: str; model: str
      search_queries: tuple[str, ...]      # 模型实际发出的搜索词
      search_hits: tuple[WebHit, ...]      # 搜索返回的全部原始结果（不去重，不过滤）
      citations: tuple[WebHit, ...]        # 模型在回答中引用的来源（厂商提供时）
      finish_reason: str
  def complete(provider_id, *, base_url, model, api_key, messages, system=None,
               enable_search=False, max_search_uses=8, max_tokens=4096, temperature=None,
               timeout=120.0, cancel=None, on_text=None, http_client=None) -> ModelResult
    - messages 为 [{'role': 'user'|'assistant', 'content': str}]；system 单独传。
    - on_text(delta: str) 可选：流式输出时逐段回调；至少 DeepSeek、OpenAI 兼容和 Anthropic 协议要支持流式。
    - http_client 可注入（测试用 httpx.MockTransport）。
    - enable_search=True 而该厂商 search 为 None 时抛 ModelError('MODEL_SEARCH_UNSUPPORTED', ...)。
    - 需要“工具调用往返”的厂商（例如搜索以函数调用形式返回、需要把参数原样回传的），在 complete 内部完成往返，调用方只拿最终结果。

各厂商的具体参数一律以官方文档为准（不要凭记忆），在 registry 里写 doc_url 和你查阅的日期。需要覆盖：DeepSeek（已实测，按上面样例）、通义千问、Kimi、智谱 GLM、OpenAI、Anthropic Claude、Google Gemini。
错误映射：复用 refactor/learning/connections/errors.py 的 ModelError；沿用现有错误码；新增 MODEL_INSUFFICIENT_BALANCE（中文提示“模型账户余额不足”，DeepSeek 为 HTTP 402，其他厂商按文档）和 MODEL_SEARCH_UNSUPPORTED。错误信息里绝不能出现 API Key。
安全：沿用 transport.normalize_url 的规则（HTTPS、禁止重定向）；超时、取消（cancel 为 threading.Event）都要生效。

四、允许修改/新增的文件
  refactor/learning/connections/providers/（新建包，内部文件自定）
  refactor/learning/connections/errors.py（只允许新增上述两个错误码需要的内容，不改已有行为）
  tests/refactor/providers/ 下的测试与样本（可复制 with_tool.json 作为 DeepSeek 样本）

五、不要做
  不改 connections/client.py、transport.py、page.py、credentials.py（接入产品由架构师做）；不读取 Windows 凭据；不发真实网络请求；不装新包。

六、验收
  D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor/providers -q -p no:cacheprovider
  必须覆盖：每家厂商至少一个“普通回答”和一个“带搜索回答”的模拟往返（按官方文档构造响应），能正确取出 text、search_queries、search_hits、citations、usage；DeepSeek 用真实样本 with_tool.json 解析，断言 8 次搜索、44 条原始结果、usage 中缓存命中 38656；流式 on_text 拼接结果等于最终 text；401/402/429/超时/取消/重定向的错误码；错误文本不含 Key；detect_provider 对各厂商默认地址正确。

七、交付
  git add 上面允许的文件
  git commit -m "T02: multi-provider adapters with native web search and usage accounting"
回执（填好后发给用户）：
【runtime｜T02｜完成/部分完成/受阻】
基线：6cca892
提交：office/runtime 上的提交编号
改动：文件列表
各厂商：搜索接入方式、文档链接、查阅日期、有无不确定之处
验证：命令与结果
未完成：没做完或拿不准的地方
~~~

### [T03] client｜时长账本纯逻辑

~~~text
【MentorFlow 任务 T03｜员工 client｜时长账本（纯计算逻辑）】

你是 MentorFlow 项目的外包开发。只做本任务，只改“允许修改的文件”里列出的文件。

一、开工同步（第一步必须做）
PowerShell 执行：
  cd D:\MentorFlow-office\client\repo
  git status                      # 应该是干净的；不干净就停下，在回执里说明
  git checkout office/client
  git reset --hard 6cca892
repo 是主仓库的 git worktree，你的分支是 office/client。禁止 push、禁止切到 main、禁止修改 D:\MentorFlow 主目录。
Python 用 D:\MentorFlow\.venv\Scripts\python.exe；本任务只用标准库。
先读：repo 根目录 AGENTS.md；docs/project/CURRENT_STATE.md 的 [PLAN-05]、[PLAN-08]、[TEACH-05]、[TEACH-07]、[CHK-01] 条目。

二、背景
学习计划每天的任务是“学习时长”（分钟）。计划生成时把总时长按可学日分好，之后不动态重排。进度按“完成量”记：每个知识点/检测有估算分钟，学完才记入。你要写一个不依赖界面和数据库的计算模块，规则如下，必须严格照做。

三、要做什么：新建 refactor/learning/course/ledger.py

1) 计划窗口与分配
  @dataclass(frozen=True) class PlanWindow:
      start: date; end: date                       # 含首尾
      daily_cap_minutes: int                       # 用户填的“这份计划每天最多学多久”，>0
      weekday_weights: tuple[float, ...] = (1.0,)*7   # 周一..周日，每个在 [0,1]
      date_weights: Mapping[date, float] = {}      # 指定日期的权重，优先于星期；如 0（没空）、0.5（只有一半时间）
  def weight_of(window, day) -> float
  def distribute(window, total_minutes: int, *, from_date: date | None = None) -> dict[date, int]
    - 只在 [from_date 或 start, end] 且权重 > 0 的日子上分配；按权重比例分；先向下取整，余数按小数部分从大到小逐个 +1，小数部分相同则日期早的优先；结果总和必须严格等于 total_minutes。
    - 没有可学日 → LedgerError('NO_STUDY_DAYS')。
  def full_day_minutes(targets, window) -> int     # 权重为 1 的日子的目标分钟数取最大值；没有权重 1 的日子则取所有目标的最大值
  @dataclass(frozen=True) class CapReport: ok: bool; full_day_minutes: int; cap: int; suggested_end: date | None
  def check_cap(window, total_minutes) -> CapReport
    - ok = full_day_minutes <= cap。不 ok 时 suggested_end = 保持同样的星期权重（date_weights 只在原窗口内有效）向后延长结束日期，使 full_day_minutes <= cap 的最早日期；延长 5 年仍不行则为 None。

2) 学习日归属（每日刷新时间）
  def study_day(ts: datetime, refresh: time, tz: tzinfo) -> date
    - ts 必须带时区，否则 LedgerError('NAIVE_DATETIME')。换算到 tz 后，本地时间早于 refresh 属于前一天，否则属于当天。
    - 一次上课中跨过刷新时间时，整次课都记在开课那天：调用方用开课时间算出 day 传给记账函数，账本本身只按传入的 day 记。

3) 记账
  @dataclass(frozen=True) class Credit:
      credit_id: str; unit_id: str; minutes: int; day: date
      state: str          # 'tentative'（暂记）或 'confirmed'（正式）
      seq: int            # 账本内部递增序号，决定先后
  class Ledger:
      def __init__(self, window: PlanWindow, total_minutes: int)     # 内部用 distribute 生成目标
      def add_tentative(self, credit_id, unit_id, minutes, day)       # 讲完一个知识点且随口问答上
      def add_confirmed(self, credit_id, unit_id, minutes, day)       # “直接考”跳过模块、检验答对等
      def confirm(self, unit_id) -> int     # 该 unit 的全部暂记转为正式（保留原 day），返回分钟数
      def revoke(self, unit_id) -> int      # 删除该 unit 的全部暂记（正式的不动），返回分钟数；被删的部分自然变成欠账
      def status(self, today: date) -> LedgerStatus
      def replan(self, today: date, new_window: PlanWindow, new_total_minutes: int) -> None
      def to_dict(self) -> dict ; @classmethod from_dict(cls, data) -> Ledger   # 纯 JSON 类型，往返后 status 完全一致
    - 同一 credit_id 重复提交且内容相同 → 忽略；内容不同 → LedgerError('CREDIT_CONFLICT')。minutes 必须为正整数。

4) 分配算法（status 每次重新计算，不保存中间结果）
  - 把所有记录按 seq 排序，先把全部“正式”依次往日历上填，再把全部“暂记”接着填。
  - 每一笔都填进当前段（见 replan）里“最早的还没填满的目标日”，填满再往后，可以填到未来日子（即超前）。这就实现了“补的时间先还前面欠的那天”。
  - 超出全部目标的部分记为 overflow_minutes，不报错。

  @dataclass(frozen=True) class LedgerStatus:
      today_target: int                 # 今天的目标（今天不是学习日则为 0）
      today_confirmed: int; today_tentative: int     # 分配到今天这个目标里的正式 / 暂记分钟
      debt_minutes: int                 # 当前段内今天之前所有目标日“目标 − 已填”之和
      needs_negotiation: bool           # debt_minutes >= 2 × full_day_minutes（欠两天以上，要和用户协商重排）
      ahead_minutes: int                # 分配到今天之后日子里的分钟
      ahead_days: int                   # 从明天起连续填满的可学日天数
      remaining_minutes: int            # 总时长 − 所有记录（正式+暂记）
      remaining_confirmed_minutes: int  # 总时长 − 正式记录
      overdue_days: int                 # today > end 且 remaining_confirmed_minutes > 0 时为 (today − end).days，否则 0
      completed: bool                   # remaining_confirmed_minutes <= 0
      history_debt_minutes: int         # 历次重排时冻结下来的欠账合计（只记历史，不再计入 debt）
      overflow_minutes: int

5) 重排（用户改截止日期 / 加资料 / 删模块后调用）
  - today 之前的日子全部冻结：目标和已填情况保留为历史，当时的欠账累加到 history_debt_minutes，不再要求补。
  - 新目标 = distribute(new_window, new_total_minutes − today 之前所有记录的分钟, from_date=today)；差值 <= 0 时新目标全为 0。
  - 从此以后，seq 在重排之后的记录，以及 day >= today 的记录，只往新段里填；重排前 day < today 的记录留在历史段。
  - new_window.end < today 或 new_total_minutes < today 之前已记录的分钟 → LedgerError。
  - 可多次重排，每次形成一个新段。

6) 检验时长估算（建计划时用）
  def estimate_check_minutes(point_count: int, *, minutes_per_question: float, problem_ratio: float = 0.10, problem_questions: int = 4) -> int
    = round(point_count × (1 − problem_ratio) × 1 × minutes_per_question + point_count × problem_ratio × problem_questions × minutes_per_question)
    （90% 的点一题答对；10% 的点有问题，约 2 次答对 + 2 次答错 = 4 题）

  LedgerError 带 code 属性，message 为中文。

四、允许修改/新增的文件
  refactor/learning/course/ledger.py（新建）
  tests/refactor/ledger/ 下的测试

五、不要做
  不改其他文件；不用第三方库；不做持久化、界面或网络。

六、验收
  D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor/ledger -q -p no:cacheprovider
  必须覆盖：
  - 用户原例：两周计划，第一周周一开始、第二周周日结束，周六权重 0，第二周周一权重 0.5；总时长 1150 分钟时：两个周六为 0，第二周周一为 50，其余 11 天各 100，总和 1150；
  - 余数分配规则与总和严格相等（多组随机输入）；无可学日报错；
  - check_cap：超上限时 suggested_end 正确，延长 5 年仍不行为 None；
  - study_day：刷新时间 04:00、Asia/Shanghai，03:59 归前一天、04:00 归当天；无时区报错；
  - 欠账先还：前一天没学，今天学的分钟先填前一天，today_* 为 0 直到还清；
  - 超前：多学的填到明天及以后，ahead_days 正确，之后每天目标不变；
  - 暂记转正式、撤销暂记后变欠账；needs_negotiation 在欠满两天时变 True；
  - 逾期天数；完成判定只看正式记录；
  - 重排：历史冻结、history_debt 累加、新段只接收新记录、多次重排；非法重排报错；
  - credit_id 幂等与冲突；to_dict/from_dict 往返后 status 完全一致；
  - estimate_check_minutes 的例子（如 100 个点、每题 2 分钟 → 260）。

七、交付
  git add 上面允许的文件
  git commit -m "T03: study-time ledger"
回执（填好后发给用户）：
【client｜T03｜完成/部分完成/受阻】
基线：6cca892
提交：office/client 上的提交编号
改动：文件列表
验证：命令与结果
未完成：没做完或拿不准的地方（规则有歧义就写出你的理解，不要自己改规则）
~~~

补充澄清（2026-09-27，回复 MF-03）：重排后历史段每日“已填”和 history_debt_minutes 在重排时定格，之后 confirm/revoke 不回算它们；但记录本身照常更新，remaining_minutes、remaining_confirmed_minutes、completed 按最新记录算。历史段中被 revoke 的暂记分钟累加为当前段第一天之前的“补学目标”，当前段仍纯 FIFO 从头重算（先被超前吸收，再减今天已完成，最后才成欠账），不计入 history_debt；测试：当前段无记录时撤销 → debt 恰好增加撤销分钟；有超前时撤销 → ahead 减少、debt 不变。（此条为补充澄清三修正后的最终说法）
补充澄清二（2026-09-27）：needs_negotiation = debt_minutes > 0 且 debt_minutes >= 2 × full_day_minutes（无欠账一律 False）；ahead_days 只统计目标 > 0 的日子，目标为 0 的日子跳过、不打断连续，全为 0 时为 0；各补测试。

### [T04] platform｜搜索结果过滤 + 白名单/屏蔽名单初稿

~~~text
【MentorFlow 任务 T04｜员工 platform｜搜索结果过滤与来源名单】

你是 MentorFlow 项目的外包开发。只做本任务，只改“允许修改的文件”里列出的文件。

一、开工同步（第一步必须做）
PowerShell 执行：
  cd D:\MentorFlow-office\platform\repo
  git status                      # 应该是干净的；不干净就停下，在回执里说明
  git checkout office/platform
  git reset --hard 6cca892
repo 是主仓库的 git worktree，你的分支是 office/platform。禁止 push、禁止切到 main、禁止修改 D:\MentorFlow 主目录。
Python 用 D:\MentorFlow\.venv\Scripts\python.exe；只用标准库（不装新包）。
先读：repo 根目录 AGENTS.md；docs/project/CURRENT_STATE.md 的 [PLAN-06] 条目（尤其“验证”里两类问题）。

二、背景
模型用厂商自带搜索找学习资料，原始结果有噪声。一次真实搜索（学习 Datawhale hello-agents）的完整结果在：
  E:\Codex\Temp\MentorFlowTests\search-benchmark\results\with_tool.json
其中 content 里 type 为 web_search_tool_result 的块是原始结果（url、title）。已知噪声：CSDN 转载站、他人 fork 的仓库（wshao12/hello-agents 是 datawhalechina/hello-agents 的 fork）、无关仓库的 raw.githubusercontent 文件、高校 WebVPN 代理链接（*.webvpn*.edu.cn）、空 URL、同一篇 arXiv 论文的多种链接。用户的底线：绝不能出现和学习无关、甚至色情/赌博的网站。
本任务只做“确定性的过滤和整理”，不做网页读取、不调用模型（那些由架构师接入）。

三、要做什么
1) 新建 refactor/learning/course/source_filter.py：
  @dataclass(frozen=True) class RawHit: url: str; title: str = ''; snippet: str = ''; query: str = ''
  @dataclass(frozen=True) class ScreenedHit:
      url: str                    # 规范化后的 URL
      original_url: str; title: str
      tier: str                   # 'whitelist' | 'neutral' | 'low' | 'blocked'
      category: str               # 白名单类别或识别出的类别，未知为 'unknown'
      reasons: tuple[str, ...]    # 机器可读原因，如 'repost_site','possible_fork','raw_file','webvpn_proxy','duplicate','adult','gambling','piracy','invalid_url'
      duplicate_of: str | None    # 重复时指向保留的那条规范化 URL
  def normalize_url(url: str) -> str | None
    - 去掉 #片段、utm_*/spm/from/share 等追踪参数；域名小写；去尾部斜杠；空/非 http(s)/格式错误 → None。
    - arXiv：abs/pdf/export.arxiv.org/web3.arxiv.org 等各种形式统一为 https://arxiv.org/abs/<编号>（去掉版本号 vN）。
    - raw.githubusercontent.com/<owner>/<repo>/<ref>/<path> 统一为 https://github.com/<owner>/<repo>/blob/<ref>/<path>。
  def github_repo(url) -> tuple[str, str] | None     # 取 (owner, repo)，大小写不敏感
  def screen(hits, *, lists=None) -> list[ScreenedHit]
    - 保持输入顺序；规范化后相同的视为重复，保留第一条，其余 reasons 含 'duplicate' 并填 duplicate_of。
    - 按名单判 tier：命中屏蔽名单 → blocked；命中“低质/转载”名单 → low；命中白名单 → whitelist；其余 neutral。
    - 同名仓库出现在多个 owner 下：owner 在白名单 github_orgs 里的保留原样，其他 owner 的加 'possible_fork'（tier 不高于 neutral）；都不在白名单时，保留第一条，其余加 'possible_fork'。
    - raw 文件（原始链接是 raw.githubusercontent）加 'raw_file'。
    - WebVPN/校园代理（域名含 webvpn）→ blocked，原因 'webvpn_proxy'。
    - 标题或 URL 含明显色情/赌博/盗版关键词 → blocked（关键词表放在名单文件里，中英文都要有）。
  def load_lists(path=None) -> SourceLists     # 默认读 refactor/learning/course/data/source_lists.json；格式有误要报清楚的错
  def is_probable_fork(owner, repo, *, fetch) -> bool | None
    - fetch(url) -> dict 由调用方注入；请求 https://api.github.com/repos/{owner}/{repo}，读 'fork' 字段；出错返回 None。本模块自身不联网。

2) 新建 refactor/learning/course/data/source_lists.json（初稿，给用户审）：
  {
    "version": 1, "updated": "YYYY-MM-DD",
    "whitelist": [ {"match": "域名后缀", "brand": "名称", "category": "encyclopedia|official_docs|academic|education|standards|government|reference", "reason": "一句话为什么可信"} ],
    "github_orgs": [ {"owner": "官方组织名", "brand": "...", "reason": "..."} ],
    "low": [ {"match": "域名后缀", "reason": "转载/内容农场/SEO 镜像等"} ],
    "blocked": [ {"match": "域名后缀或模式", "reason": "..."} ],
    "blocked_keywords": { "adult": [...], "gambling": [...], "piracy": [...] },
    "candidates": [ {"match": "...", "brand": "...", "category": "...", "reason": "拿不准、请用户决定的条目放这里"} ]
  }
  标准：只收“维基百科这一级别”、广受认可的来源；中英文都要覆盖（官方文档、顶会/期刊/预印本、知名高校与公开课、标准组织、政府统计等）。CSDN 这类不进白名单（放 low）。拿不准的一律放 candidates，不要进 whitelist。白名单 60–200 条为宜，每条都要有 reason。

四、允许修改/新增的文件
  refactor/learning/course/source_filter.py（新建）
  refactor/learning/course/data/source_lists.json（新建）
  tests/refactor/source_filter/ 下的测试与样本（可把 with_tool.json 复制进来作为样本）

五、不要做
  不改其他文件；不联网；不调用模型；不做网页读取。

六、验收
  D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor/source_filter -q -p no:cacheprovider
  必须覆盖（用真实样本 with_tool.json）：空 URL 被丢弃；adg.csdn.net → low；wshao12/hello-agents 标 possible_fork 而 datawhalechina/hello-agents 保留；WebVPN 链接 → blocked；raw.githubusercontent 的 hello-agents README 规范化后与 github.com 版本判为重复；arXiv 的 pdf/abs/export 形式合并；developers.openai.com → whitelist official_docs。另外单测：追踪参数清理、色情/赌博/盗版关键词拦截（中英文）、名单文件格式错误的报错、is_probable_fork 用假 fetch 的三种结果。
  回执里附：对 with_tool.json 全部 44 条结果的筛选结果表（URL、tier、reasons）。

七、交付
  git add 上面允许的文件
  git commit -m "T04: search hit screening and source lists draft"
回执（填好后发给用户）：
【platform｜T04｜完成/部分完成/受阻】
基线：6cca892
提交：office/platform 上的提交编号
改动：文件列表
名单：白名单/低质/屏蔽/候选各多少条
验证：命令与结果；44 条筛选结果表
未完成：没做完或拿不准的地方
~~~

### [T05] tools｜语音识别（边说边出字，首次使用下载模型）

~~~text
【MentorFlow 任务 T05｜员工 tools｜语音识别】

你是 MentorFlow 项目的外包开发。只做本任务，只改“允许修改的文件”里列出的文件。

一、开工同步（第一步必须做）
PowerShell 执行：
  cd D:\MentorFlow-office\tools\repo
  git status                      # 应该是干净的；不干净就停下，在回执里说明
  git checkout office/tools
  git reset --hard 6cca892
repo 是主仓库的 git worktree，你的分支是 office/tools。禁止 push、禁止切到 main、禁止修改 D:\MentorFlow 主目录。
Python 用 D:\MentorFlow\.venv\Scripts\python.exe（Python 3.12，已有 PySide6 6.11.1、numpy）。
先读：repo 根目录 AGENTS.md；docs/project/CURRENT_STATE.md 的 [VOICE-01]、[DATA-02] 条目。

二、背景
用户回答老师时可以说话：点“开始录音” → 边说边识别、文字实时出现 → 点“结束录音” → 用户自己点发送。旧版用本机 sherpa-onnx + SenseVoice int8，识别准。旧实现只读参考（不许修改、不许在运行时依赖这些路径）：
  D:\MentorFlow\backend\app\asr_supervisor.py（旧 MentorFlow）
  E:\aiPlay\ai_agent\tools\kristina_sensevoice_worker.py、E:\aiPlay\ai_agent\agent_core\local_asr_sensevoice_client.py（属于另一个项目 Kristina，只读）
  本机已有模型 E:\Kristina_Runtime\asr\models\sherpa\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17（Kristina 的，只读；本地测试时可以“复制”到你的测试数据目录使用，不能改原目录）
SenseVoice 是整句模型，不是流式模型；“边说边出字”用 VAD + 对当前这句反复重识别实现。

三、要做什么：新建包 refactor/learning/voice/asr/
  - model_manager.py：
      ModelManager(data_root: Path)：模型放在 data_root / 'models' / 'asr' / <模型名>/
      status() -> dict：installed(bool)、model_name、size_bytes（需下载的总大小）、download_url、path
      download(progress: Callable[[int, int], None], cancel: threading.Event) -> None：从 k2-fsa/sherpa-onnx 官方 GitHub Releases 下载 SenseVoice int8 模型和 silero VAD 模型（确认准确的官方链接、大小与 SHA256，写进代码常量并在回执中列出）；先下到临时文件，校验通过再原子替换；支持断点续传；取消后不留半成品。
      界面在用户同意后才会调用 download；未安装时不许自动下载。
  - engine.py：AsrEngine：加载 SenseVoice（sherpa_onnx.OfflineRecognizer.from_sense_voice，use_itn=True，语言自动）和 VAD；feed(pcm_int16_16k: bytes) 喂音频；pending_partial() 返回当前句的临时结果；VAD 判句尾后产出该句最终结果。识别在后台线程做，不能阻塞调用线程。
  - recorder.py：用 PySide6 QtMultimedia（QMediaDevices、QAudioSource）采集默认麦克风，16 kHz、单声道、16 位；设备不支持时自行重采样到 16 kHz。
  - service.py：AsrService(QObject)：
      信号：state_changed(str: 'idle'|'recording'|'finishing'|'error')、partial_text(str)（整段文本 = 已确定的句子 + 当前句临时结果）、final_text(str)、error(str code, str message)
      方法：start()（模型未安装 → error('ASR_MODEL_REQUIRED', ...)，不开麦）、stop()（停止采集，把最后一句识别完再发 final_text）、cancel()
      大约每 400 ms 更新一次 partial；静音超过约 800 ms 判为句尾。错误码至少：ASR_MODEL_REQUIRED、ASR_MIC_UNAVAILABLE、ASR_MIC_PERMISSION、ASR_ENGINE_FAILED。
依赖：在共用环境安装 sherpa-onnx（选能在 Python 3.12 / Windows 上运行的版本），在 refactor/desktop/requirements-account-shell.txt 末尾追加“# Voice input (VOICE-01)”和精确版本号。

四、允许修改/新增的文件
  refactor/learning/voice/asr/（新建包）
  refactor/desktop/requirements-account-shell.txt（只在末尾追加上述几行）
  tests/refactor/asr/ 下的测试（音频样本 < 500 KB；可用本机 TTS/录音生成一两段中文短句 wav）

五、不要做
  不改其他文件（包括 refactor/learning/voice/__init__.py）；不修改任何 E:\ 下的文件；不做界面（按钮和输入框由架构师接）；不做说话人识别等额外功能。

六、验收
  D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor/asr -q -p no:cacheprovider
  必须覆盖：模型未安装时 start 报 ASR_MODEL_REQUIRED 且不开麦；下载器用本地假服务器（或假下载函数）测试进度、校验失败、取消、断点续传；用中文 wav 样本走真实识别（本机模型存在时运行，不存在则 skip 并在回执说明），结果含关键字；partial 随时间更新、stop 后给 final；识别不在主线程执行。
  回执里写实测：模型加载耗时、一句 5 秒中文的识别耗时、说话时第一次出现文字的延迟（尽量用真实麦克风测一次）。

七、交付
  git add 上面允许的文件
  git commit -m "T05: streaming-style SenseVoice voice input"
回执（填好后发给用户）：
【tools｜T05｜完成/部分完成/受阻】
基线：6cca892
提交：office/tools 上的提交编号
改动：文件列表
模型：下载链接、大小、SHA256
依赖：安装的包及精确版本
验证：命令与结果；实测耗时
未完成：没做完或拿不准的地方
~~~

### [T06] classroom｜老师朗读（GPT-SoVITS + 弗洛洛，常驻提速）

~~~text
【MentorFlow 任务 T06｜员工 classroom｜老师朗读】

你是 MentorFlow 项目的外包开发。只做本任务，只改“允许修改的文件”里列出的文件。

一、开工同步（第一步必须做）
PowerShell 执行：
  cd D:\MentorFlow-office\classroom\repo
  git status                      # 应该是干净的；不干净就停下，在回执里说明
  git checkout office/classroom
  git reset --hard 6cca892
repo 是主仓库的 git worktree，你的分支是 office/classroom。禁止 push、禁止切到 main、禁止修改 D:\MentorFlow 主目录。
客户端 Python 用 D:\MentorFlow\.venv\Scripts\python.exe（不装新包）。GPT-SoVITS 引擎用它自带的 Python 运行（见下），作为独立子进程。
先读：repo 根目录 AGENTS.md；docs/project/CURRENT_STATE.md 的 [VOICE-01]、[TEACH-05] 条目。

二、背景
老师朗读用本机 GPT-SoVITS，声音用弗洛洛（Phrolova，中文）。旧版每次都要加载一会儿才开始读，用户体验差；本任务目标是“常驻预热 + 分句流水线”，让老师开口足够快。以下全部只读（不许修改、不许往里面写文件）：
  引擎：E:\AI_Tools\GPT-SoVITS-Kurisu\engine\GPT-SoVITS-v2pro-20250604（自带 runtime\python.exe，Python 3.9，CUDA 可用，显卡 RTX 5070 Ti Laptop）
  弗洛洛声音包：E:\AI_Tools\GPTSoVITS\voice_packs\phrolova-b-gpt-sovits，以及 E:\AI_Tools\GPTSoVITS\VoicePacks 下的 phrolova-*-gpt-sovits 变体；目录 E:\AI_Tools\GPTSoVITS\voice_catalog
  环境记录：E:\AI_Tools\GPTSoVITS\Phrolova\ENVIRONMENT.txt
  旧 MentorFlow 做法：D:\MentorFlow\backend\app\teacher_voice_pack.py、D:\MentorFlow\scripts\build_local_gpt_sovits_voice_packs.py
  提速思路参考（属于另一个项目 Kristina，只读，只借思路）：E:\AI_Tools\Kristina-TTS-P0-Bakeoff（含 GPT-SoVITS-cpp、GPT-SoVITS_minimal_inference）
运行时产生的日志、缓存、临时音频一律写到你工位下 D:\MentorFlow-office\classroom\temp\ 或程序传入的 data_root，不写 E:\ 下的上述目录。

三、要做什么：新建包 refactor/learning/voice/tts/
  - config.py：引擎目录、引擎 Python、声音包目录等路径集中在这里，可被环境变量覆盖；声音包按 voice_catalog 里的描述加载（参考音频、参考文本、GPT/SoVITS 权重）。
  - runtime.py：TtsRuntime：以子进程启动引擎自带的推理 API（GPT-SoVITS 自带 api_v2.py 或你写的更轻的推理服务，二选一，写明理由）；只监听 127.0.0.1；启动后立即用一句短文本预热；健康检查；程序退出时可靠关闭子进程；子进程崩溃后下次调用自动重启一次。
  - text_prep.py：朗读前的文本处理：去掉 Markdown 标记、代码块（读作“这里有一段代码”之类的简短提示）、链接、公式符号；数字/单位/英文缩写的合理读法；按中文标点分句，过长的句子再按逗号切，单句不超过约 60 字。
  - service.py：TtsService(QObject)：speak(text)（分句后流水线：播放第 n 句时合成第 n+1 句）、stop()（立即停止播放并丢弃队列）、信号 state_changed、sentence_started(int, str)、error(code, message)；播放用 QtMultimedia（QAudioSink 或 QMediaPlayer）；同一时刻只读一段，新 speak 先 stop 旧的。
  - 预热时机：提供 warm_up()，由程序启动后在后台调用，不阻塞界面。

性能目标（本机，预热后）：一句约 20 字的中文从 speak 到开始出声 ≤ 1.5 秒；长段落句与句之间没有明显停顿。回执写实测：冷启动到可用耗时、预热后首句出声延迟、实时率（合成时长/音频时长）、显存占用。
声音包对比：用三句测试文本（例：“我们先来看一个生活中的例子。”“这个概念其实和你熟悉的游戏存档很像。”“你刚才的回答方向是对的，只差最后一步。”）分别用每个 phrolova 变体合成，wav 放到 D:\MentorFlow-office\classroom\temp\tts-samples\，回执里列出文件名，供用户试听挑选。默认先用 phrolova-b-gpt-sovits。

四、允许修改/新增的文件
  refactor/learning/voice/tts/（新建包；可含一个随引擎子进程运行的推理脚本）
  tests/refactor/tts/ 下的测试（text_prep 单测、用假子进程测试 runtime 的启动/崩溃重启/关闭）

五、不要做
  不改其他文件（包括 refactor/learning/voice/__init__.py）；不修改或写入 E:\ 下任何已有目录；不重新训练模型；不装新包到共用环境；不做界面。

六、验收
  D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor/tts -q -p no:cacheprovider
  并实际运行一次端到端：启动 → 预热 → 朗读一段约 150 字的中文（含一个代码块和一个英文缩写）→ 中途 stop。回执写实测数据和样本文件名。

七、交付
  git add 上面允许的文件
  git commit -m "T06: resident GPT-SoVITS teacher voice with sentence pipelining"
回执（填好后发给用户）：
【classroom｜T06｜完成/部分完成/受阻】
基线：6cca892
提交：office/classroom 上的提交编号
改动：文件列表
方案：用 api_v2 还是自写推理服务及理由
验证：命令与结果；实测性能；样本文件名
未完成：没做完或拿不准的地方
~~~

补充澄清（2026-09-27，回复 MF-06）：通用底模本机已不存在，不用 Kristina 目录的任何副本；允许从 GPT-SoVITS 官方来源（HuggingFace lj1995/GPT-SoVITS 及官方文档指定的 G2PWModel，可用 hf-mirror.com）下载到 E:\MentorFlowModels\gpt-sovits-base\（本项目目录，可写）；不写进引擎目录；路径写入 tts/config.py 可被环境变量覆盖；回执列来源、大小、SHA256；config.py 预留首次下载信息，本次不实现下载流程。
补充澄清（2026-09-27，全体）：提交身份只在命令中临时指定，格式 git -c user.name="MentorFlow Worker <工位>" -c user.email="worker-<工位>@mentorflow.local" commit ...；员工提交由架构师压缩合并进 main，不进入公开历史。

### [T07] server｜服务器：通用同步记录、白名单/价格表接口、单设备登录（不部署）

~~~text
【MentorFlow 任务 T07｜员工 server｜服务器改动（只写代码和测试，不部署）】

你是 MentorFlow 项目的外包开发。只做本任务，只改“允许修改的文件”里列出的文件。

一、开工同步（第一步必须做）
PowerShell 执行：
  cd D:\MentorFlow-office\server\repo
  git status                      # 应该是干净的；不干净就停下，在回执里说明
  git checkout office/server
  git reset --hard 6cca892
repo 是主仓库的 git worktree，你的分支是 office/server。禁止 push、禁止切到 main、禁止修改 D:\MentorFlow 主目录。
Python 环境：共用 .venv 里没有 fastapi，不要往共用 .venv 装东西。在你的工位建独立环境：
  D:\MentorFlow\.venv\Scripts\python.exe -m venv D:\MentorFlow-office\server\.venv-backend
  再按 backend 的依赖文件（优先 backend\requirements.lock.txt）安装；用它跑后端测试。
禁止连接服务器、禁止 ssh、禁止部署、禁止改线上任何东西。
先读：repo 根目录 AGENTS.md；docs/project/CURRENT_STATE.md 的 [ARCH-05]、[ACC-01]、[DATA-02]、[PLAN-06]、[SYS-03] 条目。

二、背景
新版客户端要同步很多新数据（模块、知识点、掌握记录、判定、报告等），但服务器同步对记录类型和字段是写死的白名单（backend/app/enterprise_tenancy/structured_sync.py 的 _REFACTOR_KIND_BY_RECORD、_REFACTOR_FIELDS）。本任务一次性做三件事，之后统一部署（部署由架构师征得用户同意后进行）。

三、要做什么
A. 通用课程记录类型 course_record
  - 在 _REFACTOR_KIND_BY_RECORD 增加 'course_record'，映射到现有 kind 'learning_run'（如果你读代码后认为 'history' 更合适，写明理由，不要新增 kind）。
  - 字段严格为 {'schema', 'plan_key', 'entity', 'entity_id', 'payload'}：
      schema：正则 ^mentorflow\.course\.[a-z_]{1,40}\.v[0-9]{1,3}$
      entity：正则 ^[a-z_]{1,40}$，且必须等于 schema 中间那段
      entity_id：符合现有 ID_PATTERN
      plan_key：符合 ID_PATTERN，或空字符串（账号级数据）
      payload：JSON 对象；规范化 JSON 序列化后 ≤ 256 KiB；嵌套深度 ≤ 32
  - 归属、CAS、幂等、冲突等其他逻辑完全沿用现有实现；已有记录类型的行为不能有任何变化。
B. 只读目录接口（白名单、价格表）
  - 新建 backend/app/enterprise/catalog_api.py：
      GET /api/enterprise/catalog/source-whitelist
      GET /api/enterprise/catalog/model-prices
    需要登录（与同步接口相同的认证方式）；从环境变量 MENTORFLOW_CATALOG_DIR（默认 /run/mentorflow/catalog）读取 source-whitelist.json、model-prices.json；文件必须是含 "version" 的 JSON 对象且 ≤ 1 MiB；返回原文 + ETag（内容 SHA256），支持 If-None-Match → 304；Cache-Control: private, max-age=300；文件不存在 → 404 CATALOG_NOT_CONFIGURED；格式错误 → 503 CATALOG_INVALID。错误响应格式沿用现有统一格式。
  - 在应用装配处注册这个 router（找到现有 router 的注册位置，照同样方式加）。
  - 新建示例文件 deploy/enterprise/staging/rp05/catalog/model-prices.example.json：每条含 provider、model、currency、input_cache_hit_per_mtok、input_cache_miss_per_mtok、output_per_mtok、source_url、checked_on、verified；DeepSeek 按官方价格页填写并附链接和日期，其他厂商拿不准就标 verified:false。白名单文件由另一名员工起草，这里不用写。
C. 单设备登录（新登录挤掉旧设备）
  - 在 backend/app/enterprise/identity/native.py 的 NativeSessions：同一 subject 登录成功后，撤销该 subject 的其他所有会话族（新会话保留）。
  - 被挤掉的会话再来 refresh 或用 access token 访问时，返回 401，错误码 NATIVE_SESSION_SUPERSEDED，中文提示“账号已在另一台设备登录”，而不是普通的失效提示。为此在内存里保留“被挤掉的令牌摘要 → 原因”的有界记录（有容量上限和过期时间，不存明文令牌）。
  - 新增 GET /api/enterprise/auth/native/session（Bearer access token）：有效 → 200 {"state":"active"}；被挤掉 → 401 NATIVE_SESSION_SUPERSEDED；其他 → 现有的 401。客户端上课时会定期调用它。
  - 不能破坏：令牌轮换、重放撤销整族、revoke_subject、登录限流、容量上限、登出。

四、允许修改/新增的文件
  backend/app/enterprise_tenancy/structured_sync.py（仅为 A 所需）
  backend/app/enterprise_tenancy/sync_registry.py（仅在 A 确有必要时）
  backend/app/enterprise/identity/native.py（仅为 C 所需）
  backend/app/enterprise/catalog_api.py（新建）
  应用装配文件中注册 catalog router 的那一两行（在回执写明文件和行）
  deploy/enterprise/staging/rp05/catalog/model-prices.example.json（新建）
  backend/tests/ 下新增的测试文件：test_course_record_sync.py、test_catalog_api.py、test_native_single_device.py

五、不要做
  不改数据库迁移；不改其他记录类型；不改 refactor/ 下任何文件（客户端由架构师接）；不部署、不 ssh、不连服务器；不往共用 .venv 装包。

六、验收
  用 D:\MentorFlow-office\server\.venv-backend 运行：
    python -m pytest backend/tests/test_course_record_sync.py backend/tests/test_catalog_api.py backend/tests/test_native_single_device.py -q -p no:cacheprovider
  再运行与这三处相关的已有测试（同步、原生登录相关的测试文件），确认没有回归，回执写出运行了哪些文件及结果。
  必须覆盖：A 的合法记录往返、每个字段不合法各一例、超 256 KiB、深度超限、已有类型不受影响；B 的 200/304/404/503、未登录 401、ETag 稳定；C 的挤下线后旧 refresh 与旧 access 均得到 NATIVE_SESSION_SUPERSEDED、新会话正常、session 接口三种结果、重放撤销等原有行为仍正确、有界记录不会无限增长。

七、交付
  git add 上面允许的文件
  git commit -m "T07: course_record sync kind, catalog endpoints, single-device native sessions"
回执（填好后发给用户）：
【server｜T07｜完成/部分完成/受阻】
基线：6cca892
提交：office/server 上的提交编号
改动：文件列表（含注册 router 的文件和行）
部署须知：上线需要的环境变量、挂载目录、要替换的文件（只写说明，不执行）
验证：命令与结果（新测试 + 已有相关测试）
未完成：没做完或拿不准的地方
~~~

## 第二批任务详情

### 第二批共用约定（写进了相关任务正文）

约定 1｜待同步记录接口（MF-03 实现，MF-07 调用），放在 refactor/learning/course/sync_contract.py，由 MF-03 创建，MF-07 只读引用：
  @dataclass(frozen=True) class SyncRecord:
      entity: str              # 小写下划线，如 plan / module / point / judgement / credit / source_chunk
      entity_id: str
      plan_key: str            # 账号级数据为 ''
      schema: str              # f"mentorflow.course.{entity}.v{N}"
      payload: dict            # JSON 对象，规范化序列化后 ≤ 256 KiB
      deleted: bool
      local_revision: int      # 本机每次修改递增
  class RecordStore(Protocol):
      def pending_records(self, limit: int) -> list[SyncRecord]
      def mark_synced(self, entity: str, entity_id: str, local_revision: int, server_revision: int) -> None
          # 只有本机修订号仍等于 local_revision 时才清除“待上传”，期间又被改过则保持待上传
      def apply_remote(self, records: list[tuple[SyncRecord, int]]) -> None
          # (记录, 服务器修订号)；结构类按修订号后写覆盖；进度类（judgement、credit 等只追加事件）按 entity_id 去重合并，永不删除本机未上传的事件
      def sync_cursor(self) -> str
      def set_sync_cursor(self, cursor: str) -> None

约定 2｜服务器下发的白名单格式（MF-07 写转换脚本，MF-04 写客户端）：与 refactor/learning/course/data/source_lists.json 相同，但只保留 version、updated、whitelist、github_orgs、low、blocked、blocked_keywords 七个字段，不含 candidates（用户决定拿不准的不放）。

### [2-MF01] library｜扫描版 PDF 识别 + 更多文件格式

~~~text
【MentorFlow 第二批｜MF-01（工位 library）｜扫描版 PDF 识别 + 更多文件格式】

开工同步（第一步）
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；上次遗留的 tests/refactor/extract/.tmp 未跟踪文件可以删掉；其他不干净先停下说明
  git checkout office/library
  git reset --hard 399e9c4          # 最新集成基线，含第一批全部成果
Python：D:\MentorFlow\.venv\Scripts\python.exe。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（A、B 可分两次提交）
禁止 push；禁止改主目录 D:\MentorFlow；不改 E:\AI_Tools 与 Kristina 的文件。
先读：AGENTS.md；docs/project/CURRENT_STATE.md 的 [PLAN-09]、[ENV-01]。
范围：下面的文件是预期范围；确有必要可改相邻文件，回执逐个说明原因。
注意：主线上 tests/refactor/extract/test_extract.py 的 test_no_qt_or_database_imports 已被架构师改成在独立子进程里检查（原写法在多套测试一起跑时误报），以主线为准。

A. 扫描版 PDF 文字识别
  - 新建 refactor/learning/course/ocr.py：PDF 页渲染成图片（建议 pypdfium2），本机离线 OCR（建议 RapidOCR + onnxruntime；优先“模型随安装包自带、不另行下载”的方案；若必须下载模型，按“先说明大小、征得同意再下载”设计并在回执说明）。
  - 改 course/extract.py：extract_file / extract_bytes 增加可选参数（如 ocr=None）；默认行为完全不变（扫描件仍返回 SCANNED_NO_TEXT）；启用时只识别无文字的页，结果进入同样的 blocks/chunks（带页码），warnings 注明哪些页是识别出的。
  - 进度回调（第几页/共几页）、取消、页数上限（默认 300，超出报清楚的错）。
B. 更多文件格式（用户原话：AI 能读的格式都应该支持）
  - .pptx：每页幻灯片作为一个 section（“第 N 页 + 标题”），标题/正文/备注/表格都提取。
  - .epub：按章节（目录顺序）用已有的 HTML 提取逻辑处理，章节名进 section。
  - .xlsx：每个工作表一个 section，按行转成“单元格 | 单元格”文本（kind='table'），跳过全空行，单表行数上限可配。
  - 选成熟的库（如 python-pptx、openpyxl；EPUB 可直接用 zipfile + 已有 HTML 提取，不一定要新库）。
依赖：只装所需包及必需依赖、不升级已有包；在 refactor/desktop/requirements-account-shell.txt 末尾追加“# Scanned PDF OCR and more formats (PLAN-09)”和精确版本；先确认 Python 3.12 / Windows 有可用安装包。

验收
  D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor/extract tests/refactor/ocr -q -p no:cacheprovider
  A：默认不启用时行为不变；启用时代码生成的中文扫描 PDF 能识别出关键字、页码正确；混合文档只识别无文字页；进度、取消、页数上限。
  B：每种新格式至少一个样本（测试里用代码生成）；section、表格、确定性分块 ID；损坏文件 → CORRUPT；未知后缀仍 UNSUPPORTED_TYPE。
  实测：一份真实扫描件、一份真实 PPT（不提交），回执写页数、耗时、识别样例与大致准确度。

回执
【MF-01｜library｜第二批｜完成/部分完成/受阻】
基线：399e9c4 ／ 提交：office/library 上的编号 ／ 改动：文件及原因 ／ 依赖：包与精确版本、是否要下载模型 ／ 验证：命令与结果、实测 ／ 未完成
~~~

### [2-MF02] runtime｜用量与价格 + 新模型调用入口

~~~text
【MentorFlow 第二批｜MF-02（工位 runtime）｜用量记录与价格 + 新模型调用入口】

开工同步（第一步）
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard 399e9c4          # 最新集成基线，含你的 T02
Python：D:\MentorFlow\.venv\Scripts\python.exe（不装新包）。提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push；禁止改主目录；不发真实模型请求（没有 Key）。
先读：AGENTS.md；CURRENT_STATE.md 的 [SYS-02]、[SYS-03]、[SYS-05]、[SYS-01]、[ARCH-08]；deploy/enterprise/staging/rp05/catalog/model-prices.example.json。
范围：预期范围见下；确有必要可改相邻文件，回执逐个说明原因。

A. 用量记录、价格表与花费估算（用户用自己的钱；统计不全宁可不做）
  - refactor/learning/connections/usage.py：UsageLog(path)（每个账号一个 SQLite 文件，标准库 sqlite3）；record(provider_id, model, purpose, usage: providers.Usage, plan_id='', request_id='', at=None)，同 request_id 幂等；summary(start, end, group_by='purpose'|'model'|'day')：次数、输入/缓存命中/未命中/输出 token、搜索次数、缓存命中率（命中 ÷ 输入）、金额。
  - refactor/learning/connections/pricing.py：PriceCatalog 读价格 JSON（沿用 example 格式，可补字段如每次搜索费用、上下文档位，补了要同步改 example）；cost(provider, model, usage) 返回金额和币种，价格缺失返回 None，不猜；estimate_tokens(text)（中英文分别估，写依据）；estimate_cost(...) 返回低/高区间。
  - 更新 model-prices.example.json：七家默认模型（见 providers.REGISTRY）及各家常用型号，按官方价格页填，写 source_url 与查阅日期；只有真读到官方价格页才 verified:true；搜索另计费的写上。
B. 新模型调用入口（给新界面用；旧页面仍用旧的 connections/client.py，不要改它）
  - refactor/learning/connections/gateway.py：ModelGateway(account_id, data_root)：
    · 预设：按厂商保存（厂商 id、显示名、地址、模型名、温度、超时；Key 继续用现有 credentials.py 存 Windows 凭据）；新建预设时按 REGISTRY 填好默认地址和模型，用户只需粘贴 Key。
    · 迁移：读取现有 connections.json（例如管理员现有的 “DeepSeek V4 Flash / https://api.deepseek.com / deepseek-v4-flash”），自动识别为 deepseek 厂商，Key 沿用原凭据，不要求重填；迁移不破坏旧文件（旧页面还在用）。
    · complete(purpose, messages, *, system=None, enable_search=False, on_text=None, cancel=None, plan_id='') → providers.complete；每次调用（成功或失败）按 A 的 UsageLog 记账（失败无 usage 时记次数与错误码）；错误沿用 ModelError。
    · test_connection()：发一条极短请求验证 Key 与模型可用（只在用户点击时调用）。
    · search_supported()：当前预设的厂商是否支持自带搜索（给界面提示“资料搜索需要支持搜索的厂商”）。
    · 同一时刻可有多个请求；close() 取消全部进行中的请求。

验收
  D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor/usage tests/refactor/gateway tests/refactor/providers -q -p no:cacheprovider
  A：记录幂等、多维汇总、命中率、分项计价、价格缺失、估算区间、价格文件错误；用 T02 的 DeepSeek 真实样本 usage 算一次金额写进回执。
  B：用假的 providers.complete 和临时凭据后端测试预设增删改、默认值、旧文件迁移（含 Key 沿用）、每次调用都记账（含失败）、取消与 close、不支持搜索的提示；不读真实 Windows 凭据。

回执
【MF-02｜runtime｜第二批｜完成/部分完成/受阻】
基线：399e9c4 ／ 提交：office/runtime 上的编号 ／ 改动：文件及原因 ／ 价格表：各型号、来源、日期、是否核实 ／ 验证：命令与结果 ／ 未完成
~~~

### [2-MF03] client｜掌握度引擎 + 新数据库与读写层

~~~text
【MentorFlow 第二批｜MF-03（工位 client）｜掌握度引擎 + 新数据库与读写层】

开工同步（第一步）
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard 399e9c4          # 最新集成基线，含你的 T03 账本
Python：D:\MentorFlow\.venv\Scripts\python.exe；只用标准库。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（A、B 分开提交）
禁止 push；禁止改主目录。
先读：AGENTS.md；CURRENT_STATE.md 的 [ARCH-03]（最重要）、[ARCH-05]、[TEACH-02]、[TEACH-04]、[TEACH-06]、[TEACH-07]、[CHK-01]、[PLAN-02]、[PLAN-04]、[PLAN-05]、[PLAN-08]、[PLAN-10]、[DATA-02]、[DATA-01]、[RPT-01]。
范围：预期范围见下；确有必要可改相邻文件，回执逐个说明原因。

A. 掌握度引擎：新建 refactor/learning/course/mastery.py（纯计算，确定性，可序列化，事件按 ID 幂等）
规则（必须照做；有歧义在回执写出理解，不要改规则）：
 1 数据：PointRef(point_id, module_id)，MasteryBook(points) 给出全部点（顺序即计划顺序）。
   Judgement 事件：event_id、seq、at（带时区）、context ∈ {'casual','quick_confirm','module_test','retest','skip_test','check'}、module_id、attempt_id（module_test/skip_test 同一次检测共享，其他可空）、results=((point_id, correct), ...)（一题多点各自对错）、angle（检验时的角度，其他可空）。
   AttemptClosed 事件：event_id、seq、module_id、attempt_id、kind ∈ {'module_test','skip_test'}，表示一次检测结束，据此判定。
   apply(event)：同 ID 同内容忽略，不同内容报错；按 seq 决定先后（乱序到达也按 seq 重算）。
 2 错误计数：casual、quick_confirm 答错 → 该点 casual_errors+1；module_test、retest、skip_test 答错 → module_errors+1。
 3 模块检测：本次检测里，一个点“掌握” = 至少一个结果且全对；没考到算没掌握。掌握点数 ÷ 模块总点数 ≥ 90% 为通过。
   通过：掌握 → green，没掌握 → weak，模块 passed。不通过：掌握 → green，没掌握 → red，模块 failed。
 4 重测（retest，只针对 red）：答对一次 → green；答错仍 red。模块没有 red 了 → passed。
 5 直接考（skip_test）：按同样比例判定；通过 → 模块 skipped（算完成），掌握 green、没掌握 weak；不通过 → 模块回到 untested，所有点回到 untested（错误计数保留）。
 6 可以进入检验：所有模块都是 passed 或 skipped。
 7 检验等级：初始 = casual_errors>0 且 module_errors>0 → 1，否则 2；满 3 级即通过（之后不再变）。check 事件答对 +1（最高 3）、答错 −1（最低 0）。
 8 出题顺序 next_check_items(n=1, min_gap=3) -> [(point_id, angle)]：候选为未通过检验的点。先覆盖：检验里没问过的点排前，其中“重点”（任一错误计数>0 或 weak）优先，再按等级低、错误总数多、计划顺序；全部问过后：按等级低、错误多、距上次被问久。间隔：一个点问过后至少隔 min_gap 道其他检验题才再问，没有别的候选时可不遵守。角度 ANGLES=('recall','apply','contrast','fix_error','transfer')，取该点没用过的第一个，全用过取最久没用的。
 9 查询：point()、module()、check_ready()、check_complete()、stats()（给学习报告：每点两类错误数、检验答题次数与答对率、模块检测次数）；to_dict()/from_dict() 往返一致。

B. 新数据库与读写层（ARCH-03 为准）
  - refactor/learning/course/store.py：CourseStore(account_id, data_root)，数据库 learning/accounts/<account_id>/course.sqlite3（WAL、外键开启、schema_version 表、可升级）；用户上传的原件存同目录 files/<sha256>（本层只负责按 sha 存取原件，不同步）。
  - 表（字段按需细化，写清注释）：plans（目标、最终目标、水平与补充说明、起止日期、每日上限、星期权重、特殊日权重、刷新前的计划状态：creating/ready/active/checking/completed，archived 标记，修订号）；sources（url 或本机文件、sha256、标题、站点、tier、可信度、是否采用）；source_chunks（ID 用 extract 的 chunk_id、序号、正文、页码范围、章节）；stages / modules（顺序、前置、估算学习与检测分钟）/ points（简述、估算分钟、出处块 ID 列表，至少 1 个）；judgements 与 attempt_closed（A 的事件，原样存）；credits（T03 账本的记录；账本的窗口配置存在 plans 上）；teacher_profile（按计划，一个 JSON 文档）；messages（按计划和模块的对话，含角色、内容、类型、时间；支持把一段旧对话标记为“已压缩”并存一条摘要）；reports；chat_sessions / chat_messages（自由闲聊）。
  - 读写 API：按用途提供事务方法（例如 create_plan、save_intake、add_sources、set_source_adopted、save_outline、save_generated_module、reorder_modules、delete_module、start_plan、archive_plan、restore_plan、delete_plan（真删，含其报告）、append_judgement、append_attempt_closed、add_credit / confirm_credits / revoke_credits、save_teacher_profile、append_message、compress_messages、save_report、闲聊会话读写），以及 load_ledger(plan_id) -> Ledger、load_mastery(plan_id) -> MasteryBook（用 A 与 T03 从事件重算，不另存权威状态）。
  - 同步：新建 refactor/learning/course/sync_contract.py，按下面“约定 1”原样定义 SyncRecord 与 RecordStore；CourseStore 实现 RecordStore：每次改动写入本机修订号并进入待上传；实体到 SyncRecord 的映射一张表一个 entity（原件不同步，source_chunks 同步正文）；结构类后写覆盖，进度类（judgement、attempt_closed、credit）只追加、按 ID 去重、永不删除未上传事件。
    约定 1（与 MF-07 共用，不得改名改义）：
      @dataclass(frozen=True) class SyncRecord: entity: str; entity_id: str; plan_key: str; schema: str; payload: dict; deleted: bool; local_revision: int
      class RecordStore(Protocol):
          pending_records(limit) -> list[SyncRecord]
          mark_synced(entity, entity_id, local_revision, server_revision) -> None   # 本机修订号仍等于 local_revision 才清待上传
          apply_remote(records: list[tuple[SyncRecord, int]]) -> None             # (记录, 服务器修订号)
          sync_cursor() -> str ; set_sync_cursor(cursor: str) -> None
      schema 统一为 f"mentorflow.course.{entity}.v{N}"；payload 规范化 JSON 后 ≤ 256 KiB（超出的实体要拆分设计，例如长对话按条存）。
  - 不接界面、不接模型、不接网络；不删旧的 plans.sqlite3 等旧文件（DATA-01 不兼容旧数据，但旧页面还在用，删除由架构师在换界面时做）。

验收
  D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor/mastery tests/refactor/store -q -p no:cacheprovider
  A：规则 1–9 每条至少一个测试，含一题多点、90% 边界（10 对 9 通过、12 对 10 不通过）、没考到算没掌握、重测清红、直接考两种结果、等级上下限、对错穿插不过关、覆盖优先与间隔、角度轮换、乱序按 seq、幂等与冲突、序列化。
  B：建库与升级、每个写方法的事务与回滚、删除/归档/恢复、重排与删除模块后账本重排（调用 T03 的 replan）、load_ledger/load_mastery 与直接用事件算的结果一致、RecordStore 全部方法（含“上传途中又被改”的修订号竞争、远端结构类覆盖、进度类去重合并、未上传事件不被远端删除）、并发读写（两个连接）不损坏。

回执
【MF-03｜client｜第二批｜完成/部分完成/受阻】
基线：399e9c4 ／ 提交：office/client 上的编号（A、B） ／ 改动 ／ 表结构摘要 ／ 验证：命令与结果 ／ 未完成（含对规则的理解）
~~~

### [2-MF04] platform｜资料验证流水线 + 名单与价格表客户端

~~~text
【MentorFlow 第二批｜MF-04（工位 platform）｜资料验证流水线 + 名单与价格表客户端】

开工同步（第一步）
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard 399e9c4          # 最新集成基线，含你的 T04 与其他人的第一批成果
Python：D:\MentorFlow\.venv\Scripts\python.exe（不装新包）。提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...
禁止 push；禁止改主目录。
先读：AGENTS.md；CURRENT_STATE.md 的 [PLAN-06]（“过滤流程”“重搜”“用户决定”）、[ARCH-06] 第 3 步、[PLAN-04]、[ARCH-05]。
可用模块：course/source_filter.py、course/extract.py、connections/providers（WebHit）、planning/research.py 的 ResearchService（防内网、限重定向和大小的安全抓取）、planning/plan_source.py 的 AuthenticatedPlanSource 与 learning/sync_transport.py（带登录的服务器请求写法）。
范围：预期范围见下；确有必要可改相邻文件（例如给 research.py 加一个返回原始字节的安全抓取方法），回执逐个说明原因。

A. 资料验证流水线：新建 refactor/learning/course/research_pipeline.py（不调用模型，相关性打分由架构师接）
  - 输入：搜索结果（url、title、snippet）、已采用/已拒绝 URL、名单。
  - 流程：source_filter.screen → 去掉 blocked、duplicate、已采用/已拒绝；low 默认不展示（可选包含）→ 并发抓取（默认 4 路，同站点 ≤ 2，单个 15 秒，总数上限可配）→ 按内容类型用 extract 提取（HTML、PDF、纯文本；其他拒绝）→ 验证：抓取失败、正文过短、页面标题/主标题与搜索标题明显不符、正文命中色情/赌博/盗版关键词 → 拒绝，给原因码和中文说明。
  - 输出：VerifiedSource（规范化 URL、最终 URL、标题、站点、tier、类别、原因标签、ExtractedDocument、展示摘要、抓取时间、内容 SHA256）、RejectedSource（URL、标题、原因码、说明）。
  - ResearchPool：保存验证通过但未展示的资料；take(n) 按 whitelist → neutral →（可选）low 取下一批；标记采用/拒绝；“再找找”时先给上一轮没展示的。
  - 抓取器可注入；默认基于 ResearchService；取消随时生效。
B. 名单与价格表客户端：新建 refactor/learning/course/catalog_client.py
  - 从服务器 GET /api/enterprise/catalog/source-whitelist 和 /model-prices 拉取（需登录，沿用 AuthenticatedPlanSource / SyncTransport 的认证写法：会话提供器 + CA 证书，每次请求现取令牌，不缓存令牌）。
  - 本机缓存（账号数据目录下）+ If-None-Match/ETag，304 用缓存；服务器 404（还没配置）或网络失败 → 用本机缓存，没有缓存则用内置文件（名单用 course/data/source_lists.json 去掉 candidates；价格用仓库里的 example 的一份内置拷贝，放 course/data/model-prices.json）。
  - 服务器名单格式（与 MF-07 约定，不得自改）：与 source_lists.json 相同，只含 version、updated、whitelist、github_orgs、low、blocked、blocked_keywords，不含 candidates。拿到后校验格式，不合格就不用、继续用旧的。
  - 提供 load_lists() 给 A 使用（这样白名单来自服务器）、load_prices() 给价格计算使用；刷新在后台线程，最多每 6 小时一次。

验收
  D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor/research_pipeline tests/refactor/catalog_client -q -p no:cacheprovider
  A：假抓取器下通过与各拒绝原因、PDF/HTML 两条路径、并发与同站点限制、取消、ResearchPool 分批与采用/拒绝、with_tool.json 回放（配假页面）。联网实测一次：对 with_tool.json 筛选后保留的真实 URL 真实抓取验证，回执写尝试数、通过数、各拒绝原因计数、耗时、通过列表（标题、站点、tier）。
  B：用 httpx.MockTransport 测 200/304/404/401/网络失败/格式不合格各路径，缓存写入与读取，内置回退，名单格式校验，刷新频率限制；不连真实服务器。

回执
【MF-04｜platform｜第二批｜完成/部分完成/受阻】
基线：399e9c4 ／ 提交：office/platform 上的编号 ／ 改动：文件及原因 ／ 验证：命令与结果、联网实测统计 ／ 未完成
~~~

### [2-MF05] tools｜语音术语纠正 + 每日刷新时钟与学习提醒

~~~text
【MentorFlow 第二批｜MF-05（工位 tools）｜语音术语纠正 + 每日刷新时钟与学习提醒】

开工同步（第一步）
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard 399e9c4          # 最新集成基线，含你的 T05
Python：D:\MentorFlow\.venv\Scripts\python.exe。提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...
禁止 push；禁止改主目录；不改 E:\ 下任何文件。
先读：AGENTS.md；CURRENT_STATE.md 的 [VOICE-01]（记着你实测“牛顿”→“扭断”）、[TEACH-05]、[SYS-04]、[PLAN-05]。
范围：预期范围见下；确有必要可改相邻文件，回执逐个说明原因。注意 MF-06 本批会改 voice/asr/model_manager.py（换成通用下载器），你不要改那个文件。

A. 语音识别的课程术语纠正
  - 新建 refactor/learning/voice/asr/glossary.py：correct(text, terms) -> (新文本, [(起, 止, 原文, 替换为), ...])。术语由调用方给（计划里的知识点标题、关键术语，中英文都有）。中文按不带声调的拼音比较：完全同音替换；只差一个音节且声母相同才替换；单字术语不纠正；英文不区分大小写、容忍空格/连写。宁可少改；原文已含该术语不动；写清阈值和理由。
  - 改 voice/asr/service.py：set_glossary(terms)；partial 与 final 都纠正；新增一个信号带出替换列表（名字自定），方便界面显示“已按课程术语修正，点击撤销”。
  - 如需拼音库（如 pypinyin），装进共用环境，在 requirements-account-shell.txt 末尾追加“# ASR glossary (VOICE-01)”和精确版本。
B. 每日刷新时钟与学习提醒（Qt 服务，不做界面）
  - 新建 refactor/learning/services/daily_clock.py：DailyClock(QObject)
    · 配置：刷新时间（本地钟表时间，如 04:00）、时区、提醒时间（可关闭）；随时可改。
    · 信号 study_day_changed(date)：到刷新时间时发出（计算规则与 course/ledger.py 的 study_day 一致，直接调用它）。
    · 提醒：到提醒时间时，调用注入的回调 today_unfinished() -> bool，为 True 才发 reminder_due()；同一天只提醒一次。
    · 用户正在上课时（注入 in_class() -> bool）不打断：刷新时间到了也照常发 study_day_changed，由上层决定记到哪天（TEACH-05：没退出课堂就记前一天），但在 in_class 为 True 时额外发 late_night_notice()（“太晚了，注意休息”）。
    · 可靠性：电脑睡眠唤醒、系统改时间、跨时区时，最多 30 秒内纠正；只在软件运行时工作（SYS-04：不做开机自启和后台常驻）。
    · 时间源可注入（测试用假时钟）。

验收
  D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor/asr tests/refactor/daily_clock -q -p no:cacheprovider
  A：扭断→牛顿（术语表有“牛顿”时）、表里没有不改、原文已含不动、单字不改、声母不同不改、英文大小写/空格、替换位置正确、服务层 partial/final 生效、原 ASR 测试不回归；用上次真实麦克风流程再测一次，回执写前后对比。
  B：假时钟下刷新时间前后、同日只提醒一次、未完成才提醒、上课中深夜提示、睡眠唤醒跳变、改刷新时间立即生效、改时区。

回执
【MF-05｜tools｜第二批｜完成/部分完成/受阻】
基线：399e9c4 ／ 提交：office/tools 上的编号 ／ 改动：文件及原因 ／ 依赖 ／ 验证：命令与结果、实测对比 ／ 未完成
~~~

### [2-MF06] classroom｜通用下载器与朗读底模下载 + 朗读嘴型数据

~~~text
【MentorFlow 第二批｜MF-06（工位 classroom）｜通用下载器与朗读底模下载 + 朗读嘴型数据】

开工同步（第一步）
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 399e9c4          # 最新集成基线，含第一批全部成果
Python：D:\MentorFlow\.venv\Scripts\python.exe。提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...
禁止 push；禁止改主目录；不改 Kristina、E:\AI_Tools 下任何文件。
先读：AGENTS.md；CURRENT_STATE.md 的 [VOICE-01]、[DATA-02]、[ENV-01]、[TEACH-05]。
范围：预期范围见下；确有必要可改相邻文件，回执逐个说明原因。注意 MF-05 本批会改 voice/asr/service.py 并新增 glossary.py，你不要改这两个文件。

A. 通用下载器 + 朗读底模下载
  - 新建 refactor/learning/voice/download.py：资产描述（名称、地址列表=主地址+备用镜像按序尝试，HuggingFace 资产默认加 hf-mirror.com，GitHub 资产不加；大小；SHA256；安装相对路径；是否压缩包 zip/tar.bz2 及解压后校验清单）；status() 不联网；download(progress, cancel) 断点续传、逐个校验、全部通过后原子安装、失败/取消不留半成品、镜像失败换下一个；中文错误码（网络失败、校验失败、磁盘不足、已取消）。
  - 改 voice/asr/model_manager.py 用它实现（对外接口与行为不变，ASR 原测试照过）。
  - 新建 voice/tts/model_manager.py：朗读底模（你上次核对过的 8 个文件 + G2PW 包解压）安装到 data_root/models/tts/gpt-sovits-base/；改 voice/tts/config.py：优先用 data_root 下已安装的，开发机没有则回退 E:\MentorFlowModels\gpt-sovits-base（保留环境变量覆盖）。
  - 回执另写“正式分发要准备什么”：引擎运行时（含 Python/CUDA 依赖）和弗洛洛声音包各多大、放哪、是否必须联网下载；只写结论。
B. 朗读嘴型数据（给 Live2D 对口型，Live2D 接入由架构师做）
  - 在 TtsService 播放时，按约 50 毫秒一帧计算正在播放的音频响度（0–1，平滑，静音接近 0），通过信号 mouth_level(float) 发出；与实际出声同步（按音频设备实际消耗的进度，不按合成完成时间）；停止或句间静音时回到 0。
  - 另提供 sentence_timing 信息（每句的开始/结束时刻），供界面高亮正在读的句子。
  - 性能：不增加首音延迟，CPU 占用可忽略。

验收
  D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor/voice_download tests/refactor/asr tests/refactor/tts -q -p no:cacheprovider
  A：进度、续传、换镜像、校验失败、取消、磁盘不足（可模拟）、zip/tar.bz2 解压与校验、原子安装；ASR/TTS 不回归。实测：把 TTS 底模真实下载到临时 data_root（不要删 E:\MentorFlowModels 那份），回执写耗时和实际走的地址。
  B：用合成音频验证响度曲线与静音归零、与播放进度同步、stop 立即归零、首音延迟与上次相比不变差（回执写实测）。

回执
【MF-06｜classroom｜第二批｜完成/部分完成/受阻】
基线：399e9c4 ／ 提交：office/classroom 上的编号 ／ 改动：文件及原因 ／ 验证：命令与结果、实测 ／ 正式分发调查 ／ 未完成
~~~

### [2-MF07] server｜既有失败测试与部署包 + 新数据同步客户端与挤下线检测

~~~text
【MentorFlow 第二批｜MF-07（工位 server）｜既有失败测试与部署包 + 新数据同步客户端与挤下线检测】

开工同步（第一步）
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard 399e9c4          # 最新集成基线，含你的 T07
后端测试继续用 D:\MentorFlow-office\server\.venv-backend；客户端代码测试用 D:\MentorFlow\.venv\Scripts\python.exe。提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署。
先读：AGENTS.md；CURRENT_STATE.md 的 [ARCH-05]、[ACC-01]、[ACC-02]、[DATA-02]、[ARCH-03]；客户端现有同步写法：refactor/learning/sync_transport.py、sync_controller.py、sync_records/、planning/plan_source.py。
范围：预期范围见下；确有必要可改相邻文件，回执逐个说明原因。

A. 既有失败测试 + 部署包（不执行）
  - backend/tests/test_change_password_binding.py 的 3 个既有失败：查根因。测试过时 → 更新测试并写清真实行为；代码缺陷 → 写清缺陷、影响、最小修复，只有确定是改密绑定本身缺陷才修并补测试；不改其他账号流程行为（ACC-02）。
  - 部署包 deploy/enterprise/staging/rp05/mf-u09/：README（以 MF-U04 镜像为底只覆盖 native.py、structured_sync.py、catalog_api.py、account_core_mvp.py；目录文件只读挂载到 /run/mentorflow/catalog；只改最后一份 override 的 backend 镜像与挂载；只重建 backend；每步检查命令；回滚到 MF-U04；上线后验证 /health、目录接口未放文件 404、放文件后 200 与 ETag、session 接口、挤下线手测）。脚本默认只打印命令，显式参数才执行，你本人不执行。
    上次部署做法供参考：活跃项目 mentorflow-e1-preview；当前镜像 mentorflow-mf-u04-backend:MF-U04（以上一版为底只覆盖改动源码构建）；backend Compose 叠加顺序 /opt/mentorflow/releases/21337c1eda64f7e087fcade77c3d4cd7dce4a0af-exact-source/deploy/enterprise/staging/rp05/compose.account-core-mvp.yml → /opt/mentorflow/artifacts/gatea-native-20260913-08/backend.override.json → /opt/mentorflow/artifacts/r2-04-final-20260917/runtime.override.json → /opt/mentorflow/artifacts/mf-d04-20260922/runtime.override.json（上次只改了最后一份的 backend image）；回滚配置目录 /opt/mentorflow/state/mentorflow-e1-preview/mf-b03-rollback；数据库 0041 不动；原生认证单进程（MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS=1、WEB_CONCURRENCY=1）。
  - 名单转换：tools/ 下写脚本，把 refactor/learning/course/data/source_lists.json 转成服务器下发格式（约定 2：只含 version、updated、whitelist、github_orgs、low、blocked、blocked_keywords，不含 candidates），配测试。
B. 新数据同步客户端 + 挤下线检测
  - 新建 refactor/learning/course/sync_course.py：CourseSync(record_store, transport)：用“约定 1”的 RecordStore（由 MF-03 在 refactor/learning/course/sync_contract.py 定义并实现；你在测试里自己写一个内存版 RecordStore，按约定 1 的签名，不要自己创建 sync_contract.py，届时以 MF-03 的为准）：
    · 上传：pending_records → 按服务器 course_record 格式（你在 T07 定义的）经现有同步协议写入（沿用 sync_transport / 现有 CAS、幂等请求 ID 的做法）→ mark_synced；冲突时拉取远端后再决定（结构类后写覆盖由 RecordStore 处理）。
    · 拉取：按游标增量拉取 course_record → apply_remote → 更新游标。
    · 失败可重试、不丢数据；进行中可取消；同一时刻只跑一轮。
    约定 1（与 MF-03 共用）：
      @dataclass(frozen=True) class SyncRecord: entity: str; entity_id: str; plan_key: str; schema: str; payload: dict; deleted: bool; local_revision: int
      class RecordStore(Protocol):
          pending_records(limit) -> list[SyncRecord]
          mark_synced(entity, entity_id, local_revision, server_revision) -> None
          apply_remote(records: list[tuple[SyncRecord, int]]) -> None
          sync_cursor() -> str ; set_sync_cursor(cursor: str) -> None
  - 新建 refactor/learning/services/session_guard.py：SessionGuard(QObject)：上课期间（start()/stop() 控制）每 60 秒调用 GET /api/enterprise/auth/native/session（沿用现有认证写法，每次现取令牌）；收到 401 NATIVE_SESSION_SUPERSEDED → 发 superseded()（“账号已在另一台设备登录”）；其他 401 → 发 reauth_required()；网络失败不报错、按退避重试；也提供一个可在任意请求失败时调用的 note_error(code) 入口，统一识别 NATIVE_SESSION_SUPERSEDED。

验收
  后端：.venv-backend 跑 test_change_password_binding.py、T07 三个测试文件、test_native_auth.py、test_sync_registry.py、test_product_sync_contract.py、tests/server_sync/test_contract.py，结果写进回执；转换脚本测试。
  客户端：D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor/sync_course tests/refactor/session_guard -q -p no:cacheprovider（用 httpx.MockTransport 模拟服务器的 course_record 读写，覆盖上传、增量拉取、冲突、失败重试、取消、上传途中本机又改动；SessionGuard 的三类结果与退避）。

回执
【MF-07｜server｜第二批｜完成/部分完成/受阻】
基线：399e9c4 ／ 提交：office/server 上的编号 ／ A：根因与处理、部署包清单 ／ B：改动 ／ 验证：命令与结果 ／ 未完成
~~~
