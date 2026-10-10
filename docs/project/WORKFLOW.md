# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读开头的「文档维护规则」和「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 开头三节和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-10）：线上 2.0.7（第九十五、九十六批：同步提速与刷新、注册页预热与输入修复）。96-MF04（启动先出窗口、更新提示在登录窗口；部分完成：打包版 1.58 秒出窗）已合入 16f8c6b8，2.0.7 已发布（2026-10-10）。等用户在 2.0.6 上确认注册页能输入、“我的计划”同步后直接出现。第九十七批（直接考、资料推荐、缓存调查、BUG-45 方案）已起草待转发。收到回执时列出本批还差谁的回执。

## 协作方式

- **先讨论、后派活（最优先）**：问题先和用户讨论清楚（现象、原因、牵连范围、前置依赖）再写任务；收到回执只讲审查结果、问题和要用户定的事，用户同意后再把整批任务原文放在回复最后，然后停下等转发。不出看着笃定但有漏洞的方案。
- **不懂就问**：有不懂或感觉有偏差，直接问用户。
- **一批派完、并行优先**：能并行、互不冲突的一次派完，人数不设上限；改同一文件同一部分的排先后；有依赖的写明先后与原因。
- **不重复造轮子（硬规定）**：每份任务第一节写“用的现成项目与许可证”，参照行业标准；要自己写的写明为什么没有现成的。
- **方案由架构师出**：架构师查证据、读相关代码、找开源做法，在任务里写清“怎么做”；员工照做。
- **派任务前的检查**：每条完成标准都是这个员工凭自己的权限能做到的（取决于外部的写成“查清后二选一”）；互相依赖的工作不拆成并行任务各自要求全量通过；写之前读相关代码、查环境。
- **员工的测试（用户 2026-10-10 定）**：工作中只跑与改动相关的测试，全量只在最后跑一次，同一套测试和演练不重复跑；不做 AI 模拟学生的长流程测试。
- **员工自主**：任务写清决策原则，员工自己决定、在回执写理由；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。
- **两轮规则**：同一个问题第一轮按现方法修，没解决第二轮换方法（先查开源做法）；还不行带数据找用户定。
- **花钱的测试**：只为具体改动花钱，事先写下预期；能离线的先离线；预算分段，第一段没达到预期就停。
- **发版**：修好就用流水线发版，让用户在装好的软件上点更新亲测（操作手册“发布新版”）。
- **动用户数据要先问**：会让用户已有数据失效、被挡或被隐藏的设计必须先经用户同意；改数据格式一律带迁移（[ARCH-03]）。
- **功能先行**：功能没做完前不加安全加固（AGENTS.md）；审查时删掉员工多加的加固。
- **问题留档**：每修好一个问题，同一次提交里在 CURRENT_STATE 记 BUG 条目或更新原条目。
- **状态如实**：任务写好标“已起草，待转发”；用户说发了才改“进行中”；合入后从员工表和待派队列删掉，任务原文也删。
- **部署**：每次改服务器都先向用户说明改什么、拿到同意再做。
- **向用户汇报**：合入后先给一张“环节 × 状态”表，再说问题和建议；话尽量短。
- **署名**：主目录提交用 `git -c user.name="tonyswift868" -c user.email="205869700+tonyswift868@users.noreply.github.com" commit ...`，结尾加 Co-Authored-By 行；不写进共享 git 配置。员工用各自工位名提交。

## 架构师操作手册

- **合入员工分支**：主目录 `git merge --no-ff office/<工位>`（或 `git diff --binary <基线> <员工提交> | git apply --index --3way`），逐文件看改动是否在任务范围内；跑本批改动相关的测试（`pytest $(git diff --name-only <基线> HEAD | grep '^tests/.*test_.*\.py$')`），两份以上改动合在一起时再跑一次全量。
- **全量测试**（约 10 分钟，2026-10-10 约 3280 通过、28 跳过）：`TMP=D:/MentorFlow/.local/tmp TEMP=D:/MentorFlow/.local/tmp .venv/Scripts/python.exe -m pytest tests/refactor -q -p no:cacheprovider --basetemp=D:/MentorFlow/.local/test-tmp/<子目录>/pt`，输出重定向到文件、放后台跑，跑完删子目录。
- **基线**：合入并提交后 `git rev-parse --short HEAD` 就是下一批任务的基线。
- **发布新版**（[SYS-06]）：①合入并推送 main；②手动运行 GitHub 工作流 mf-build（版本号、更新说明、是否必须及原因、基线版本，build_only=false），约 5 分钟；③下载它的 unsigned 产物，本机 `.venv/Scripts/python.exe -B scripts/release/sign_and_stage.py --artifact <zip> --private-key .local/release-keys/mentorflow-app-2026.private.key --work .local/test-tmp/release-<版本>/sign --ssh-identity E:/Codex/Secrets/MentorFlow/ssh/mentorflow-e1-ops-ed25519`；④手动运行 mf-publish（版本号），约 10 分钟；⑤用客户端代码对公网 stable.json 验签，看 aiteacher.clannad.win/download。触发和查看运行可用 GitHub 网页，或用本机 git 凭据调 GitHub API（.local/tmp/watch_run.sh 轮询到结束）。工作流只能手动触发，额度红线见 scripts/release/README.md。
- **启动/重启用户的源代码版**：`Start-Process -FilePath D:/MentorFlow/.venv/Scripts/pythonw.exe -ArgumentList 'D:/MentorFlow/refactor/desktop/source_start.pyw' -WorkingDirectory D:/MentorFlow -WindowStyle Hidden`。重启前一定先问用户（可能在上课）。
- **查用户软件的问题**：装好的软件日志 D:\MentorFlowApp\MentorFlow\Data\logs\mentorflow.log，源代码版日志 D:\MentorFlowData\account-shell\logs\mentorflow.log（JSON 行，只读）；数据库在对应的 learning\accounts\<账号>\*.sqlite3，只读查看用 `sqlite3.connect('file:...?mode=ro', uri=True)`。
- **界面构建**：PATH 加 `/d/MentorFlow/.local/node`，`node .local/web-deps/node_modules/vite/bin/vite.js build --config refactor/learning/web/vite.config.mjs`；dist 不进仓库。
- **写脚本**：含 Windows 路径或反斜杠的内容不要用 bash heredoc 或 python -c（会被吃掉，BUG-10），写成文件再运行。
- **服务器**：`ssh -F "D:/MentorFlow/.local/server/ssh_config" mentorflow`（89.45.45.28，mentorflow-admin，sudo -n）。线上后端 mentorflow-slim-backend:MF-75（切换与回滚脚本 /opt/mentorflow/state/mf75/switch.sh，上线前备份同目录）；入口 nginx 容器 mentorflow-e1-preview-edge-1（配置 /opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf）；更新与下载目录 /opt/mentorflow/update-static/e108/app/；资料白名单 /opt/mentorflow/state/mentorflow-e1-preview/catalog/。
- **磁盘**：测试与临时产物在 .local/test-tmp/ 下；员工的临时目录要清理先问用户。发版走流水线后，本机不再保留打包目录。

## 当前基线

| 项目 | 值 |
| --- | --- |
| main | 以 `git rev-parse --short HEAD` 为准；第九十五批合入 c77aaf07 |
| 已发布 | 2.0.7 = 3624350f（非必须）；强制线 2.0.3 |
| GitHub | main 已推送（私有仓库）；发版需要的推送由架构师做，其余推送要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07）转发任务；工位（library、runtime、client、platform、tools、classroom、server）在任务里指定。同一个员工、同一个工位同时只能有一个任务。任务号写成“批次-员工编号”。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 97-MF01（已起草，待转发） | server |
| MF-02 | 空闲 | client |
| MF-03 | 97-MF03（已起草，待转发） | classroom |
| MF-04 | 空闲 | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 97-MF06（已起草，待转发） | tools |
| MF-07 | 97-MF07（已起草，待转发） | library |

## 待派队列

- 第九十七批 4 个任务：已起草（2026-10-10），待用户转发：97-MF03 直接考（TEACH-07）、97-MF07 资料推荐文字优先与 stars（PLAN-06）、97-MF06 缓存命中率调查（SYS-05，只查不改）、97-MF01 BUG-45 根治方案（上线由架构师做）。

## 第九十七批任务原文（2026-10-10，基线 aa1ee1db）

### [97-MF03] classroom｜直接考：模块里任何时候都能直接考；没题的模块随口问 3 个点（¥0）

~~~text
【MentorFlow 第九十七批｜MF-03（工位 classroom）｜直接考：模块里任何时候都能直接考；没题的模块随口问 3 个点、自己决定跳不跳】

现成项目与许可证（第一节）
  - 不新增依赖，只改本项目现有代码：refactor/learning/app/classroom.py（start_test 的 skip_test 分支、约 611 行；模块检测、记账逻辑沿用）、course/teacher_agent.py（老师随口问用现有的对话方式）、web/src/pages/Classroom.tsx（按钮与选择卡片）。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard aa1ee1db
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥。D:\MentorFlowApp、D:\MentorFlowData\account-shell 只读（要用数据就复制到测试目录）；E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md：中间产物用完即删，回执写明最终占用。测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问。不要结束或重启用户正在用的软件进程。
测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次，同一套测试不重复跑；不做 AI 模拟学生的长流程测试。
同时在跑：97-MF07（资料推荐，course/research*、source_quality、NewPlan.tsx）、97-MF06（缓存调查，只读不改产品）、97-MF01（服务器网关，refactor/server 与部署文件），都不碰你的文件。
产物目录：D:\MentorFlow\.local\test-tmp\direct97\。目标两小时内交回。花费上限 ¥0。
规则（用户 2026-10-10 定，CURRENT_STATE [TEACH-07]、[TEACH-02]）：
  1. 入口：一个模块里任何时候都能点“这个模块我都会了，直接考”（现在只有模块开始前、一个点都没讲时才能点）。正在答题（模块检测、重测、最终检验）时不显示。
  2. 有题的模块：提前做这个模块的模块检测，考本模块全部的题（包括已经讲过的点的题，用户确认“学了一半也全考”）；答对 ≥90% 就算这个模块过、记整模块时长，没讲的点标“直接考通过”（知识地图与报告沿用现有标记）；不到 90% 就回到点“直接考”之前的位置接着学，答错的题照记等级（模块检测答错留 1 级、答对 2 级）。
  3. 没题的模块：老师从还没学的知识点里随机挑 3 个（不足 3 个就从整个模块里挑），按每个点的原文各随口问一句（不记分、不进任何记录，沿用现在随口问的对话方式）；学生每答完一句，显示这个点的原文；3 句问完后给两个按钮〔跳过这个模块〕〔接着学〕，不管答得怎样都由学生自己选。选跳过：这个模块算过、记整模块时长、没讲的点标“直接考通过”；选接着学：回到原来的位置。
  4. 讲课中单个知识点说“我会了”保持现状（直接讲下一点，[TEACH-03]），不要改。
要做
  1. 按上面 4 条改后端和界面；课堂进度文件与数据库的一致性检查、中断恢复要覆盖新流程（例如直接考做到一半关软件、随口问问到第 2 句关软件，重开能接着来或干净地回到原位置）。
  2. 测试：有题模块在开始前 / 讲了一半各一条（≥90% 跳过、<90% 回到原位置且等级正确）；没题模块挑点（未学够 3 个 / 不够 3 个）、选跳过 / 选接着学各一条；中断恢复一条；界面截图（亮暗各一套：按钮位置、随口问显示原文、选择卡片）。
完成标准：上面测试通过；最后全量一次通过；web 构建通过；提交在工位。
验收：报告写到 D:\MentorFlow\.local\test-tmp\direct97\report.md。
回执（尽量短，用表格）：【MF-03｜classroom｜第九十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 入口 ／ 有题模块 ／ 没题模块 ／ 中断恢复 ／ 测试与构建 ／ 截图 ／ 本地最终占用
~~~

### [97-MF07] library｜资料推荐：文字优先、视频最后；GitHub 仓库标 stars（¥≤1）

~~~text
【MentorFlow 第九十七批｜MF-07（工位 library）｜资料推荐：文字优先、视频放最后；GitHub 仓库卡片标 stars】

现成项目与许可证（第一节）
  - stars 用 GitHub 官方 REST API（GET https://api.github.com/repos/{owner}/{repo} 的 stargazers_count，公开接口，不需要 Key；未登录每小时 60 次，够用），本机缓存一天；拿不到就不显示，不报错。
  - 排序改动只在本项目现有代码里：refactor/learning/course/research_pipeline.py、source_quality.py（来源分档与排序）、推荐的提示词（research 用途）；界面 web/src/pages/NewPlan.tsx 的资料卡片。

开工同步
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard aa1ee1db
提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥。D:\MentorFlowApp、D:\MentorFlowData\account-shell 只读；E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md：中间产物用完即删，回执写明最终占用。测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问。不要结束或重启用户正在用的软件进程。
测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次，同一套测试不重复跑。
同时在跑：97-MF03（课堂直接考）、97-MF06（缓存调查，只读）、97-MF01（服务器网关），都不碰你的文件。
产物目录：D:\MentorFlow\.local\test-tmp\recommend97\。目标两小时内交回。花费上限 ¥1（只用于最后一次真实推荐验证，真实模型用管理员现有的 DeepSeek 预设，Key 只在 Windows 凭据里）。
规则（用户 2026-10-10 定，CURRENT_STATE [PLAN-06]）：
  1. 文字优先：推荐时文字资料排前面——教材正文、讲义、官方文档、文字教程，以及权威课程配套的文字笔记；视频类（视频网站、只有视频的网课页面、播放列表）排在最后，基本不推荐（视频没法分析出知识点），只有实在找不到文字资料时才出现，并在卡片上标“视频，难以生成知识点”。原有的来源分档（官方/教材/大学 > 教育平台 > 一般网站 > 社区）在“文字”内部照旧。
  2. GitHub 仓库卡片显示 stars（例如“★ 12.3k”），点卡片照旧打开仓库；stars 只作展示和同档内的参考排序因素，不改变来源分档。
要做
  1. 推荐提示词明确“优先文字资料、找权威课程的配套笔记、不推荐视频”；程序侧识别视频类来源（常见视频站域名、播放页特征）排到最后并打标；GitHub 仓库链接取 stars 并缓存。
  2. 测试：离线用保存的推荐结果（有就用 .local/test-tmp 里之前的推荐记录，没有就自建样例）验证排序与标记；stars 接口成功、失败、限流三种情况各一条；最后真实推荐一次（例如“学 Python 数据分析”），截图资料卡片，写清推荐了什么、顺序、花费。
完成标准：上面测试通过；最后全量一次通过；web 构建通过；提交在工位。
验收：报告写到 D:\MentorFlow\.local\test-tmp\recommend97\report.md。
回执（尽量短，用表格）：【MF-07｜library｜第九十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 文字优先与视频标记 ／ stars ／ 真实推荐结果与花费 ／ 测试与构建 ／ 本地最终占用
~~~

### [97-MF06] tools｜缓存命中率：对照成熟框架查清低在哪（只查不改，¥≤2）

~~~text
【MentorFlow 第九十七批｜MF-06（工位 tools）｜缓存命中率调查：用真实数据查清每类请求命中率低在哪，对照成熟 agent 框架给出改法（本任务只查不改产品）】

现成项目与许可证（第一节）
  - 对照对象：Claude Code 的提示与上下文组织方式（Anthropic 公开文档与官方博客对 prompt caching、上下文管理的说明）、Manus 公开的上下文工程经验（开头永远不变、只往后追加、序列化顺序固定、不在中间插动态内容、工具列表不动态增删）、DeepSeek 上下文硬盘缓存文档（只认从头开始完全一样的前缀，按 64 token 单位存）。
  - 工具：prefixcash（MIT，项目里已用过，工具环境在 .local/test-tmp/cache-prefix/tool-env，脚本在 tests/refactor/cache_prefix）。
  - 只写分析脚本与报告，不改产品代码。

开工同步
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard aa1ee1db
提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...（只提交分析脚本，放 tests/refactor/cache_prefix/ 下，默认跳过）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥。D:\MentorFlowApp、D:\MentorFlowData\account-shell 只读：把用户的 model-usage.sqlite3、course.sqlite3 等复制到测试目录再分析，不改原件。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md：中间产物用完即删，回执写明最终占用。测试用 D:\MentorFlow\.venv\Scripts\python.exe。遇到选择自己决定，回执写明理由，不停下来问。不要结束或重启用户正在用的软件进程。
同时在跑：97-MF03、97-MF07、97-MF01，都不碰你的文件。
产物目录：D:\MentorFlow\.local\test-tmp\cache97\。目标三小时内交回。花费上限 ¥2（只用于第 3 步的真实对照；真实模型用管理员现有的 DeepSeek 预设）。
背景（用户 2026-10-10 定为下一个重点，CURRENT_STATE [SYS-05]、[ARCH-08]）：缓存命中率直接决定用户花费，也是对外拿得出手的指标。以前几次真实测得 56%～85%，但那时还是旧架构（讲稿、判定员）；现在的请求组成变了，要重新查。
要做
  1. 用真实使用数据算现状：复制用户两处的 model-usage.sqlite3（D:\MentorFlowData\account-shell 与 D:\MentorFlowApp\MentorFlow\Data 下各账号目录），按用途（老师对话、备题 point_materials、填知识点 generate、连线 point_links、学习档案 profile 等）统计最近几天的调用次数、输入、缓存命中、未命中、输出、花费、命中率；去掉每类第一次调用后再算一遍。写清哪几类花钱最多、命中最差。
  2. 查清断在哪：对花钱最多的 3 类请求，用产品自己的请求组装代码（course/request_layout.py 与各 agent），喂复制来的真实数据，离线还原连续几次真实调用的请求，逐段比对第几段开始不同、为什么不同（例如开头带了时间或每次变化的内容、资料片段放在对话前面、对话被截断重排、工具或参数顺序变化等）；用 prefixcash 画出断点图。不调用模型。
  3. 对照成熟框架列出改法：每条写清“现在怎么做 → 成熟框架怎么做 → 改什么文件哪里 → 预计这一类的命中率能到多少、按第 1 步的真实用量能省多少钱”；按“省钱多、改动小”排序。挑最有把握的 1～2 条，在测试目录里临时改一份副本，用真实模型连续调用对照一次（改前改后各几次同样的请求），看命中率实际变化。
完成标准：第 1 步有真实数据的现状表；第 2 步前 3 类请求各有断点结论与证据；第 3 步有排好序的改法清单和至少 1 条的真实对照数据；提交分析脚本。
验收：报告写到 D:\MentorFlow\.local\test-tmp\cache97\report.md。
回执（尽量短，用表格）：【MF-06｜tools｜第九十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 现状（按用途命中率与花费） ／ 断点结论 ／ 改法清单前 5 条与预计收益 ／ 真实对照结果与花费 ／ 本地最终占用
~~~

### [97-MF01] server｜BUG-45 根治：服务器重启后网关拿不到固定地址（只出方案和脚本，上线由架构师做，¥0）

~~~text
【MentorFlow 第九十七批｜MF-01（工位 server）｜BUG-45 根治：服务器重启后 https 网关因固定地址被占而起不来——出修法、脚本和本机验证，上线由架构师做】

现成项目与许可证（第一节）
  - 用 Docker 官方的网络做法解决固定地址冲突（Docker 文档：用户自定义网络的 --ip-range / IPAM ip_range 让动态分配只用一段地址，固定地址放在这段之外；或 compose 的 ipv4_address），以及 Keycloak 官方的反向代理配置（KC_PROXY_TRUSTED_ADDRESSES 可以写网段）。不自己写守护脚本绕过。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard aa1ee1db
提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署（服务器上的操作只由架构师在用户同意后做）；不碰正式签名私钥。D:\MentorFlowApp、D:\MentorFlowData\account-shell 只读；E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md：中间产物用完即删，回执写明最终占用。遇到选择自己决定，回执写明理由，不停下来问。本机没有 Docker 也不要安装。
测试要求：只跑与改动相关的测试，全量只在最后跑一次（如果改了 refactor/ 下的代码）。
同时在跑：97-MF03、97-MF07、97-MF06，都不碰你的文件。
产物目录：D:\MentorFlow\.local\test-tmp\bug45\（里面 ref\ 已有架构师从服务器取来的现行 compose 与 override 文件，密码类已打码、只读参考；containers-and-state.txt 是容器与目录清单）。目标两小时内交回。花费上限 ¥0。
背景（CURRENT_STATE [BUG-45]，用户 2026-10-10 定要根治）：
  - 网关容器 mentorflow-e1-preview-edge-1 在两个 docker 网络上用固定地址：edge 网 172.19.0.2、internal 网 172.18.0.5；Keycloak 只信任 172.18.0.5 这个代理（KC_PROXY_TRUSTED_ADDRESSES）。服务器重启后其他容器（更新文件容器 mentorflow-e108-update-static、数据库、mailpit 等）先启动，动态分到了这两个地址，网关再启动报 Address already in use，公网 https 全断。2026-10-02 是手工停掉别的容器、先起网关恢复的。
  - 架构师 2026-10-10 在服务器上看到的现状：edge 网 172.19.0.0/16（网关 172.19.0.2 固定、update-static 172.19.0.3 动态）；internal 网 172.18.0.0/16（mailpit .2、backend .3、keycloak .4、网关 .5 固定、postgres .6，其余动态）；smtp-egress 网 172.20.0.0/16；两个 internal/edge 网络都没设 ip_range；所有容器 restart=unless-stopped；update-static 与 mailpit 不是 compose 起的（单独 docker run）；还有一个停着的旧网关 mentorflow-e1-preview-edge-1-r1-before-1db6c0ca。
要做
  1. 读 ref\ 下的 compose 与 override，查清：网关的两个固定地址分别是怎么设的（compose 的 ipv4_address 还是事后 docker network connect --ip）；网关为什么需要 edge 网的固定地址（谁按 172.19.0.2 认它）；Keycloak 和其他配置里还有哪些地方写死了这些地址。
  2. 选一种根治方案并写清理由（按“停机最短、改动最少、以后重启永远不会再撞”排序），候选：A 给两个网络设 ip_range（动态只分高段，例如 172.18.128.0/17、172.19.128.0/17，固定地址留在低段）——需要重建网络，写清停机步骤；B 让网关不再依赖固定地址（Keycloak 信任 internal 整个网段、谁按 172.19.0.2 认网关也改成按名字），以后不再有固定地址可撞；C 其他你查到的更好做法。
  3. 写出架构师在服务器上执行的完整步骤脚本：备份（现有配置、docker inspect 结果）→ 改动 → 按什么顺序重建/重启哪些容器 → 验证（公网 https://89.45.45.28/health/live、/mentorflow/updates/app/stable/stable.json、https://auth.clannad.win、https://aiteacher.clannad.win 都 200，客户端能登录）→ 回退步骤。估计停机时间。脚本里每一步都能单独执行、出错即停。
  4. 写一个“模拟重启”的验证办法：改完后架构师怎么在服务器上安全地验证“重启后其他容器先起来也不会再占网关的地址”（例如按最坏顺序重启容器的步骤）。
  5. 如果需要改仓库里的部署文件（refactor/server/ 下的 compose 或 DEPLOY.md），改好提交；只放服务器的文件放在产物目录。
完成标准：1 的三个问题有结论和依据（引用 ref 里的文件行）；2 有选定方案与理由；3、4 的脚本和步骤完整、可直接照做；提交在工位。
验收：报告写到 D:\MentorFlow\.local\test-tmp\bug45\report.md。
回执（尽量短，用表格）：【MF-01｜server｜第九十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 固定地址怎么设的 ／ 谁依赖它 ／ 选定方案与理由 ／ 预计停机 ／ 上线与回退脚本位置 ／ 模拟重启验证办法 ／ 本地最终占用
~~~


## 回执格式

~~~text
【员工｜任务号｜完成/部分完成/受阻】
基线：开工时切到的提交编号。
提交：分支 office/<工位名> 上的提交编号。
改动：修改/新增的文件。
验证：实际运行的命令与结果（通过/失败/跳过）；用了替身的写明。
未完成：没做完或拿不准的地方。
~~~

每条任务还会在末尾列出这次要回报的具体项目。

## 任务模板

新任务照 git 历史里最近一批任务原文的格式写（第一节现成项目与许可证、开工同步、禁止事项与磁盘规则、证据来源、同时在跑、花费、背景、要做、完成标准、验收、回执项目）。
