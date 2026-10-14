# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读「当前阶段」），这里只写编号引用。规则只在 [AGENTS.md](../../AGENTS.md)，这里只写固定做法。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 的「当前阶段」和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-14）：线上 2.0.14，服务器 MF-76。已合入待发：[BUG-84]、[BUG-85]、[BUG-86]、[BUG-88]、[BUG-89]、[BUG-90]、[BUG-91]、[PLAN-06] 直接采用。125-MF06（网页书顺着目录读）进行中；126-MF07 已合入（[BUG-91]）；127-MF03（跳过按钮拉手）已起草待转发；其余员工空闲。用户先在源代码版测（换入顺序见 [BUG-86]），满意再发 2.0.15。

## 固定做法（规则见 AGENTS.md）

- **员工实测不占用户的鼠标键盘（2026-10-11）**：真打包实测用 Windows 的 UI Automation（InvokePattern）点原生按钮、读文字；网页里的按钮和输入框用 Chrome DevTools 协议（测试环境设 QTWEBENGINE_REMOTE_DEBUGGING），因为 Qt 6.11 网页的无障碍接口只能聚焦、点不动；不用 computer-use、模拟输入或抢前台（102-MF04 用电脑界面操作时被用户按 Esc 中止）；测试窗口会出现在屏幕上，派任务时先告诉用户。
- **发版**（什么时候发由用户定）：用流水线发版，让用户在装好的软件上点更新亲测（操作手册“发布新版”）。**本机先验、GitHub 一版只跑一次（用户 2026-10-11 提出）**：先在本机用同一个打包脚本打预览版，跑一遍 scripts/release 的发版前总验收（102-MF04c 整理），过了才触发 mf-build / mf-publish；没在本机验过的版本不上 GitHub。正式版不在本机打：本机环境会把别的 DLL 混进包里（102-MF04 实测混入过 ICU/OpenSSL），而且同一版本两边打出来的文件不同，会扰乱以后的增量更新。**版本号只在真正发布时占用（用户 2026-10-12 定）**：定下要发的下一版（现在是 2.0.15），在发布前查出的 bug 修完仍发这个版本号，不跳到下一个号；本机排练用的测试版本号（如 9.103.14）不算。
- **任务状态**：任务写好标“已起草，待转发”；用户说发了才改“进行中”；合入后从员工表和待派队列删掉，任务原文也删。
- **署名**：主目录提交用 `git -c user.name="tonyswift868" -c user.email="205869700+tonyswift868@users.noreply.github.com" commit ...`，结尾加 Co-Authored-By 行；不写进共享 git 配置。员工用各自工位名提交。

## 架构师操作手册

- **合入员工分支**：主目录 `git merge --no-ff office/<工位>`（或 `git diff --binary <基线> <员工提交> | git apply --index --3way`），逐文件看改动是否在任务范围内；跑本批改动相关的测试（`pytest $(git diff --name-only <基线> HEAD | grep '^tests/.*test_.*\.py$')`），两份以上改动合在一起时再跑一次全量。
- **全量测试**（约 10 分钟，2026-10-10 约 3280 通过、28 跳过）：`TMP=D:/MentorFlow/.local/tmp TEMP=D:/MentorFlow/.local/tmp .venv/Scripts/python.exe -m pytest tests/refactor -q -p no:cacheprovider --basetemp=D:/MentorFlow/.local/test-tmp/<子目录>/pt`，输出重定向到文件、放后台跑，跑完删子目录。
- **基线**：合入并提交后 `git rev-parse --short HEAD` 就是下一批任务的基线。
- **发布新版**（[SYS-06]）：①合入并推送 main；②手动运行 GitHub 工作流 mf-build（版本号、更新说明、是否必须及原因、基线版本，build_only=false），约 5 分钟；③下载它的 unsigned 产物，本机 `.venv/Scripts/python.exe -B scripts/release/sign_and_stage.py --artifact <zip> --private-key .local/release-keys/mentorflow-app-2026.private.key --work .local/test-tmp/release-<版本>/sign --ssh-identity E:/Codex/Secrets/MentorFlow/ssh/mentorflow-e1-ops-ed25519`；④手动运行 mf-publish（版本号；build_installer 默认 false 只发更新约 1～3 分钟，要新安装程序时设 true 约 10 分钟，或事后单独跑 mf-installer）；⑤用客户端代码对公网 stable.json 验签，看 aiteacher.clannad.win/download。触发和查看运行可用 GitHub 网页，或用本机 git 凭据调 GitHub API（.local/tmp/watch_run.sh 轮询到结束）。工作流只能手动触发，额度红线见 scripts/release/README.md。
- **启动/重启用户的源代码版**：`Start-Process -FilePath D:/MentorFlow/.venv/Scripts/pythonw.exe -ArgumentList 'D:/MentorFlow/refactor/desktop/source_start.pyw' -WorkingDirectory D:/MentorFlow -WindowStyle Hidden`。重启前一定先问用户（可能在上课）。
- **查用户软件的问题**：装好的软件日志 D:\MentorFlowApp\MentorFlow\Data\logs\mentorflow.log，源代码版日志 D:\MentorFlowData\account-shell\logs\mentorflow.log（JSON 行，只读）；数据库在对应的 learning\accounts\<账号>\*.sqlite3，只读查看用 `sqlite3.connect('file:...?mode=ro', uri=True)`。
- **界面构建**：PATH 加 `/d/MentorFlow/.local/node`，`node .local/web-deps/node_modules/vite/bin/vite.js build --config refactor/learning/web/vite.config.mjs`；dist 不进仓库。
- **写脚本**：含 Windows 路径或反斜杠的内容不要用 bash heredoc 或 python -c（会被吃掉，BUG-10），写成文件再运行。
- **服务器**：`ssh -F "D:/MentorFlow/.local/server/ssh_config" mentorflow`（89.45.45.28，mentorflow-admin，sudo -n）。线上后端 mentorflow-slim-backend:MF-76（2026-10-12；切换与回滚脚本 /opt/mentorflow/state/mf76/switch.sh：无参数切换、check 只查配置、rollback 回到 MF-75；上一版 mf75 目录保留）；入口 nginx 容器 mentorflow-e1-preview-edge-1（配置 /opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf）；更新与下载目录 /opt/mentorflow/update-static/e108/app/；资料白名单 /opt/mentorflow/state/mentorflow-e1-preview/catalog/。 2026-10-10 起两个 docker 网络设了 ip_range（BUG-45）：以后用 Compose 操作这些容器，命令末尾必须追加 -f /opt/mentorflow/state/bug45-20261010/network45.override.yml。
- **自动等回执（用户 2026-10-14 定）**：只挂一个后台等待：`.local/tmp/receipt_watch.sh wait`（每 30 秒查整个 .local/receipts，和 .local/tmp/receipts.seen 比，有新的或改过的回执就列出并结束），收到就直接读回执、审查合入、清理、更新文档，处理完用 `receipt_watch.sh ack <任务号>` 记为已处理，再挂一次 wait；不用等用户转告，不按任务分别挂多个。
- **磁盘（用户 2026-10-10 定）**：测试与临时产物在 .local/test-tmp/ 下；冒烟测试、构建、中间文件用完就自动删，不留着等以后分类。员工删除被系统拦住时，架构师合入后直接删（只留 report.md 和截图），不用再问用户。

## 当前基线

| 项目 | 值 |
| --- | --- |
| main | 以 `git rev-parse --short HEAD` 为准；2.0.14 已推送并发布 |
| 已发布 | 2.0.14 = 1a58917c（2026-10-12，非必须；下载站安装程序 2.0.14）；强制线 2.0.3 |
| GitHub | main 已推送（私有仓库），远端只有 main 一个分支（9-26 遗留的 15 个 Dependabot 升级请求与分支 2026-10-12 经用户同意已关闭并删除，它们的配置已随旧代码删除，不会再生成）；发版需要的推送由架构师做，其余推送要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07）转发任务；工位（library、runtime、client、platform、tools、classroom、server）在任务里指定。同一个员工、同一个工位同时只能有一个任务。任务号写成“批次-员工编号”。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 空闲 | server |
| MF-02 | 空闲 | client |
| MF-03 | 127-MF03 已起草，待用户转发（跳过按钮右侧拉手，[TEACH-03]） | classroom |
| MF-04 | 空闲 | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 125-MF06 进行中（网页书顺着目录读，[PLAN-06]） | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 第一百一十四～一百一十九批全部已合入（最后一份 119-MF03，15afe435；主线 efbb0670）。
- 120-MF07 已合入（ada8e9a0，[BUG-89] 已修）。121-MF06 已合入（5650ba9a，网页已重新编译）。122-MF02 已合入（d24882ef，[BUG-85] 第三轮）。
- 123-MF02 已合入（115c8f48，[BUG-90]）；124-MF06 已合入（fe1b9536，[PLAN-06] 直接采用）。
- 125-MF06（网页书顺着目录读，[PLAN-06]）：进行中（用户 2026-10-14 已发）。
- 126-MF07 已合入（6c1203c6，[BUG-91] 已修，网页已重新编译）。
- 127-MF03（跳过按钮右侧拉手，[TEACH-03]）：已起草，待转发。
- 之后（用户 2026-10-14 定并确认顺序：等 2 号做完、全部合入后一次性在源代码版测，满意再发 2.0.15）：用户开源代码版登录同步后关掉 → 用最新数据重新预演给用户看 → 用户点头 → 架构师用 scripts/plan_restructure.py --apply --end-date 2026-10-22 在源代码版数据上换入（自动备份、可回滚；之后到装上 2.0.15 前不开 2.0.14）→ 用户在源代码版体验 → 满意后发版前总验收 → 用户说可以推送 → 发 2.0.15。

## 第一百二十五批任务原文（2026-10-14，基线 a78944fb）

### [125-MF06] tools｜顺着网站目录读整本网页书

~~~text
【MentorFlow 第一百二十五批｜MF-06（工位 tools）｜网页资料顺着网站自己的目录读整本（如小林coding），由程序按目录取页、用户勾选章节，不让 AI 挑链接（PLAN-06）】

现成项目与许可证（第一节）
  - 先评估 trafilatura（Apache-2.0）的 sitemaps 模块与 courlan（Apache-2.0，网址规范化与同站过滤）。
    依赖太多或装进打包程序有问题，就只用 courlan，或用现有 lxml、bs4 加标准库 urllib.robotparser 自己取目录，回执说明为什么。
  - 正文抽取继续用现有读取（course/addition_links.import_links、course/extract.py），不换。
  - 参照常见文档站的目录结构：VuePress/VitePress 侧栏、Docusaurus 菜单、MkDocs 导航、Sphinx toctree、docsify/GitBook 侧栏、sitemap.xml。

开工同步
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard a78944fb          # 主线：已合入你的 124
提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读；不要启动、结束或重启用户的软件和源代码版。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。
产物在 D:\MentorFlow\.local\test-tmp\site125\，抓下来的网页用完即删，只留 report.md。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（跑浏览器测试前把 D:\MentorFlow\.local\node 加进 PATH）。
花费：读网页不调模型；真实核对里走到资料结构一步的模型花费上限 ¥0.50。
同时在跑：无。

背景（CURRENT_STATE [PLAN-06]）：
  - 124 起，推荐资料勾选就能直接读，但网页只读链接指向的那一页。你实测 Python 教程首页只读到 2657 字的目录。
  - 用户 2026-10-14 定：顺着网站目录往下读整本，例如小林coding（xiaolincoding.com）这种面试笔记站。
  - 用户提醒：以前没做成，是因为大网站里跳转很多，AI 分不清该要哪些页。
    所以这次不让 AI 挑链接：页从网站自己的目录里取，取哪些由用户勾。

要做
  1. 认出“这是一本网页书”：
     - 读一个网页链接时，如果页面有文档站的目录（侧栏、导航、toctree）或站点有 sitemap.xml，就按下面取目录；
     - 普通单页照旧只读这一页。
  2. 程序按规则取目录，不调模型：
     - 先用页面自己的目录（侧栏、导航、toctree），保持目录里的顺序和层级；
     - 没有目录再用 sitemap.xml；
     - 只要同一个网站、而且在用户给的链接那一级路径之下的页（给 xiaolincoding.com/network/ 就只要 /network/ 下面的；给首页就是整个目录）；
     - 排除：站外链接、登录、搜索、标签、分页、评论、下载文件、同一页的锚点（#…）、重复页。
  3. 读之前先让用户看、用户勾：
     - 在资料卡片上显示目录树（章 → 节）、一共多少页，默认全勾；用户可以取消不要的章节，确认后才开始读；
     - 读的过程中显示“已读 x / y 页”，可以取消。
     - 一页读失败就跳过并列出来，不影响其他页。
  4. 读取要守规矩：
     - 遵守 robots.txt；
     - 一次只读一页，按 robots.txt 写的间隔，没写就按常见的礼貌抓取间隔，在回执写明用了多少；
     - 用现有读取的超时和重试；不绕过登录。
  5. 读到的各页合成一份资料，章节层级就是网站目录的层级（section 路径），这样后面的“按原文目录切点”（[PLAN-04]）直接按网站目录切。
     贴网址和勾选推荐卡片两条路都走这一套。
  6. 测试（文件名带 125）：
     - 用本地假网站覆盖这几种目录：VuePress 或 VitePress 侧栏、MkDocs、Sphinx toctree、只有 sitemap、单页；
     - 只取同一路径下的页；排除项；
     - 用户取消章节后只读勾选的；
     - 一页失败跳过；
     - 取消；
     - 合成资料的章节层级正确；
     - 浏览器里目录树勾选和“已读 x / y 页”。
  7. 真实核对：
     - 小林coding 取一个专题（如图解网络），走到资料结构一步，回执写目录页数、实际读到页数、总字数、用时、失败页；
     - 再取一个 Sphinx 站（docs.python.org/zh-cn/3/tutorial/）同样写；
     - 抽 3 页核对正文和原网页一致、不是导航或广告；
     - 模型花费 ≤¥0.50。
  8. 只改读取相关的 course/（addition_links.py 及新加的取目录模块）、app/plan_flow.py 的资料采用部分、NewPlan.tsx 的资料卡片部分、依赖清单（如引入新库，同时改 refactor/desktop/requirements-account-shell.txt 和 scripts/build_learning_installer.py 需要的收集项）和 tests/refactor/。

完成标准：第 6 条测试通过；第 7 条两个真实站点有数字；最后全量一次；模型花费 ≤¥0.50。
回执：写到 D:\MentorFlow\.local\receipts\125-MF06.md。表格列：基线 ／ 提交 ／ 用了哪个现成库及原因 ／ 怎么认出网页书 ／ 怎么取目录 ／ 用户怎么勾选 ／ 抓取间隔与 robots ／ 新增测试 ／ 真实核对（每站页数、字数、用时、失败页、抽查）／ 全量 ／ 花费 ／ 本地最终占用。
给用户只回一行“125-MF06 完成/部分完成/受阻，回执已写入”。
~~~

## 第一百二十七批任务原文（2026-10-14，基线 f2002eef）

### [127-MF03] classroom｜跳过按钮右侧拉手

~~~text
【MentorFlow 第一百二十七批｜MF-03（工位 classroom）｜课堂“跳过该知识点”按钮改成：右侧一块独立的向右拉手，拉开后“知识点”以替换动画换成“模块”（TEACH-03、TEACH-07）】

现成项目与许可证（第一节）
  - 不引新库。用浏览器原生 Pointer Events（setPointerCapture）做拖拽，CSS 过渡做文字替换动画；无障碍按 WAI-ARIA 的 switch 模式（role=switch、键盘可切换）。
  - 图标用现有 lucide-react（ISC）。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard f2002eef          # 主线：已合入你的 116、119
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号；不调用真实模型。
D:\MentorFlowApp、D:\MentorFlowData 只读；不要启动、结束或重启用户的软件和源代码版。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。
产物在 D:\MentorFlow\.local\test-tmp\skip127\，用完即删，只留 report.md 和两三张截图。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（跑浏览器测试前把 D:\MentorFlow\.local\node 加进 PATH）。花费 ¥0。
同时在跑：MF-06 改 NewPlan.tsx 资料卡片与 plan_flow.py（125）；MF-07 改 plan_flow.py 计划详情与 PlanDetail（126）。你只改 Classroom.tsx 的跳过控件（和它的样式）。

背景（CURRENT_STATE [TEACH-03] [TEACH-07]）：
  - 现在是两个分开的控件：[⏭ 跳过该知识点] 加一个 [● 模块] 开关（Classroom.tsx 约 435～445 行，状态 skipModule）。
  - 用户 2026-10-14 定，改成一个控件：
    - 主体“跳过该知识点”，点了直接跳，不弹窗；
    - 右侧一块看得出是独立的拉手：单独一个向右的箭头（如 lucide 的 ChevronRight／ChevronsRight），不用快进符号，免得和“跳过”看混；
    - 鼠标移上去拉手有不同的显示（高亮、轻微右移，提示“向右拉切换为跳过模块”）；
    - 把拉手向右拉开，按钮里“知识点”三个字用替换动画换成“模块”，成为“跳过该模块”，点了先弹窗确认（现有 skipConfirm 弹窗）；
    - 跳过一次（不论知识点还是模块）、或下课再进课堂，自动变回“跳过该知识点”。

要做
  1. 实现上面的控件：
     - 拖拽超过一半就切到“模块”，不到就弹回；往左拉回切回“知识点”；
     - 点击拉手或用键盘（Enter／空格）也能切换，方便不会拖的人。这只是切换，不执行跳过；执行要点按钮主体，跳模块还要再确认弹窗。
  2. 文字替换：只有“知识点 ↔ 模块”这几个字有翻转或滑入淡出的动画，“跳过该”不动。按钮宽度切换前后不跳动。
     打开“减少动态效果”或系统要求减少动画时，直接换字、没有动画。
  3. 忙碌时（busy）整个控件不可用；没有当前知识点时“跳过该知识点”不可用，但拉手仍可切到“模块”。现有行为不变：跳知识点按“我会了”记，跳模块的流程见 [TEACH-07]。
  4. 两套主题（樱花、极光夜）和窄窗口下都看得清：拉手和按钮主体之间有明显的分隔。
  5. 测试（文件名带 127）：真实 React 浏览器测试覆盖
     - 拖过一半切换、不到弹回、拉回；
     - 点击和键盘切换；
     - 跳知识点不弹窗、跳模块弹窗；
     - 跳过一次后和重新进课堂后回到“知识点”；
     - 减少动态效果时没有动画；
     - 忙碌时不可用。
     截两三张图放进报告：默认、鼠标悬停、拉开后。
  6. 只改 web/src/pages/Classroom.tsx 的跳过控件（可拆成同目录下的小组件）、对应样式和 tests/refactor/。

完成标准：第 5 条测试通过、有截图；最后全量一次。
回执：写到 D:\MentorFlow\.local\receipts\127-MF03.md。表格列：基线 ／ 提交 ／ 控件怎么做的 ／ 拉手图标与悬停效果 ／ 切换方式（拖、点、键盘）／ 什么时候变回知识点 ／ 减少动态效果 ／ 新增测试 ／ 截图 ／ 全量 ／ 本地最终占用。
给用户只回一行“127-MF03 完成/部分完成/受阻，回执已写入”。
~~~

## 回执格式

- **回执写文件（用户 2026-10-10 定）**：员工把回执表格写到 `D:\MentorFlow\.local\receipts\<批次>-<员工>.md`（例如 98-MF03.md），给用户只回一行“98-MF03 完成/部分完成/受阻，回执已写入”。用户只告诉架构师哪些做完了，架构师去读回执文件。每份任务的“回执”一节都写明这条。


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
