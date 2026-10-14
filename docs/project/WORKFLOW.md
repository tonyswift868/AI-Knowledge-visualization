# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读「当前阶段」），这里只写编号引用。规则只在 [AGENTS.md](../../AGENTS.md)，这里只写固定做法。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 的「当前阶段」和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-14）：线上 2.0.14，服务器 MF-76。已合入待发：[BUG-84]、[BUG-85]、[BUG-86]、[BUG-88]、[BUG-89]、[BUG-90]、[BUG-91]、[PLAN-06] 直接采用。125-MF06 已交回未合入，128-MF06（[BUG-92]）进行中；126-MF07 已合入（[BUG-91]）；127-MF03 已合入；129-MF03（聊天区占满高度）已起草待转发；其余员工空闲。用户先在源代码版测（换入顺序见 [BUG-86]），满意再发 2.0.15。

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
| MF-03 | 129-MF03 已起草，待用户转发（聊天区占满高度，[TEACH-05]） | classroom |
| MF-04 | 空闲 | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 128-MF06 进行中（125 第二轮：网页书读全、普通网址不被 robots 挡，[BUG-92]） | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 第一百一十四～一百一十九批全部已合入（最后一份 119-MF03，15afe435；主线 efbb0670）。
- 120-MF07 已合入（ada8e9a0，[BUG-89] 已修）。121-MF06 已合入（5650ba9a，网页已重新编译）。122-MF02 已合入（d24882ef，[BUG-85] 第三轮）。
- 123-MF02 已合入（115c8f48，[BUG-90]）；124-MF06 已合入（fe1b9536，[PLAN-06] 直接采用）。
- 125-MF06 已交回（7e814827，Python 教程 16/16 页读到；小林 42 页只读到 18 页、robots 读不到时普通网址也读不了，[BUG-92]），暂不合入；128-MF06 在它上面接着改：进行中；两份修完一起合入。
- 126-MF07 已合入（6c1203c6，[BUG-91] 已修，网页已重新编译）。
- 127-MF03 已合入（e70c977e，跳过按钮右侧拉手，网页已重新编译）。
- 129-MF03（聊天区占满高度，[TEACH-05]）：已起草，待转发。
- 之后（用户 2026-10-14 定并确认顺序：等 2 号做完、全部合入后一次性在源代码版测，满意再发 2.0.15）：用户计划已在源代码版换入（2026-10-14 15:10，[BUG-86]；到装上 2.0.15 前不开 2.0.14）→ 用户在源代码版体验 → 满意后发版前总验收 → 用户说可以推送 → 发 2.0.15。

## 第一百二十八批任务原文（2026-10-14，接着 125 的分支做）

### [128-MF06] tools｜网页书读全、普通网址不被 robots 挡

~~~text
【MentorFlow 第一百二十八批｜MF-06（工位 tools）｜125 第二轮：网页书每页不再被 15 秒总时限丢掉、临时失败会重试；robots.txt 一时读不到时，普通网址照旧按单页读（BUG-92）】

现成项目与许可证（第一节）
  - 不引新库。改你 125 写的 course/site_book.py、plan_flow._read_links，以及现有 course/research_pipeline.py、addition_links.py 里和用户自选资料读取有关的时限。
  - robots 处理照 RFC 9309：robots.txt 读不到（网络或 5xx）时不抓整站；但这只管多页抓取，不管用户自己给的单个网址（单页本来就不受 robots 约束，125 之前也不检查）。

开工同步
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  # 不要 reset：在你 125 的提交 7e814827 上接着改；125 还没合入主线
提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读；不要启动、结束或重启用户的软件和源代码版。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md（规则 2026-10-14 合并成一份，第 1 条：不自加时限和数字目标）。
产物在 D:\MentorFlow\.local\test-tmp\site128\，抓下来的网页用完即删，只留 report.md。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次，必须全绿（上次 38 项失败后没再跑全量）。
花费：读网页不调模型；真实核对走到资料结构一步的模型花费上限 ¥0.30。
同时在跑：MF-03 改 Classroom.tsx 的跳过控件（127）。你不碰它。

背景（CURRENT_STATE [BUG-92]）：
  - 你 125 的小林“图解网络”：42 页只读到 18 页。
    - 20 页是“单份资料验证超过时间限制”：ResearchPipeline 对单份资料有 15 秒总时限，到点就丢掉正在下载的页面；
    - 4 页是 SSL UNEXPECTED_EOF，没有重试。
  - 用户 2026-10-14 定的规矩（AGENTS.md 第 1 条，[BUG-90]）：不许为了凑一个秒数把好结果扔掉。用户自己选的资料，下得慢也要等它下完。
  - 另外：site_book.Reader 一开始读 robots.txt，读不到就报错；_read_links 认网页书失败时直接报“目录读取失败”，不再按单页读。
    125 之前能读的普通网址，现在会因为 robots.txt 一时读不到而读不了。

要做
  1. 用户自己选的资料（贴网址、勾选推荐卡片、网页书里勾选的页）读取时，不设总时长上限：
     - 只保留网络层的判断：连接超时、读数据时长时间收不到任何字节，用现有的单次请求超时；
     - 用户可以随时取消；
     - 下载在进行就等它下完，不再丢掉“迟到”的结果。
     - AI 推荐时筛候选链接的那一步（不是用户选的资料）不在这次范围；回执列出那里的时限是多少、超时后果是什么，不改。
  2. 临时失败重试：超时、连接被断开（SSL EOF、连接重置）、429、5xx，按常见的指数退避重试几次（次数和间隔在回执写明理由；有 Retry-After 就照它）。
     网页书读完第一遍后，对失败页再整体补读一轮。最后仍失败的页写明具体原因。
  3. robots.txt：
     - 读取同样按第 2 条重试；
     - 重试后仍读不到：不按网页书多页抓取，卡片说明“网站的 robots.txt 暂时读不到，整本读取请稍后重试”，并且这个网址照旧按单页读（和 125 之前一样）；
     - “认网页书”这一步任何失败（目录页解析失败、sitemap 读不到等），都退回单页读取，不让用户什么都拿不到。
  4. 测试（文件名带 128）：
     - 慢速假服务器：一页要 30 秒才下完，照样读到；
     - 前两次 SSL EOF、第三次成功；
     - 429 带 Retry-After；
     - 补读一轮能补回；
     - robots.txt 超时或 5xx 时，普通网址照旧单页读到、网页书不做多页；
     - 认网页书失败时退回单页；
     - 取消立即生效；
     - 125 的 site125、adopt124 测试照过。
  5. 真实核对：
     - 小林“图解网络”再读一遍，回执写目录页数、读到页数、失败页及原因、总字数、总用时；
     - 再贴一个普通单页网址，确认照常读到；
     - 走到资料结构一步，模型花费 ≤¥0.30。
     预期：除了网站本身打不开的页，42 页基本读全；读不到的每页都有具体原因。
  6. 只改 course/site_book.py、course/research_pipeline.py 与 addition_links.py 中用户自选资料读取的时限与重试、app/plan_flow.py 的 _read_links、NewPlan.tsx 资料卡片上的提示文字，以及 tests/refactor/。

完成标准：第 4 条测试通过；第 5 条有数字；最后全量一次，全绿。
回执：写到 D:\MentorFlow\.local\receipts\128-MF06.md。表格列：提交 ／ 去掉了哪些总时限 ／ 重试规则（次数、间隔、理由）／ robots 读不到时怎么办 ／ 认网页书失败时怎么办 ／ 推荐筛选处的时限清单 ／ 新增测试 ／ 真实核对（小林页数、字数、用时、失败原因；单页网址）／ 全量 ／ 花费 ／ 本地最终占用。
给用户只回一行“128-MF06 完成/部分完成/受阻，回执已写入”。
~~~

## 第一百二十九批任务原文（2026-10-14，基线：127-MF03 合入后由架构师填）

### [129-MF03] classroom｜聊天区占满高度

~~~text
【MentorFlow 第一百二十九批｜MF-03（工位 classroom）｜课堂聊天区上方收成一行：小要点、讲义、资料导航点开浮在上面；后台准备题改成右上角小标签，不给“取消准备”（TEACH-05）】

现成项目与许可证（第一节）
  - 不引新库。浮层用现有组件和原生 <dialog>／Popover 写法（点外面收起、Esc 关闭、焦点回到按钮），参照 WAI-ARIA 的 disclosure／dialog 模式；图标用现有 lucide-react（ISC）。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard BASE129          # 主线：已合入你的 127（跳过按钮拉手）
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号；不调用真实模型。
D:\MentorFlowApp、D:\MentorFlowData 只读；不要启动、结束或重启用户的软件和源代码版。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。
产物在 D:\MentorFlow\.local\test-tmp\layout129\，用完即删，只留 report.md 和改前改后截图。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（跑浏览器测试前把 D:\MentorFlow\.local\node 加进 PATH）。花费 ¥0。
同时在跑：MF-06 改建计划资料读取（128）。你不碰 NewPlan.tsx 和 plan_flow.py。

背景（CURRENT_STATE [TEACH-05]）：
  - 用户 2026-10-14 在源代码版上新结构的课：聊天区上方从上到下叠着模块进度、准备题提示条（MaterialsPreparing，带“取消准备”）、资料导航（ModuleNavigation）、小要点讲义（ConceptMaterials），聊天区被挤得看不全。
  - 用户定：
    - 小要点、讲义、资料导航收成一行，点开时浮在上面、不往下推聊天区；
    - 准备题的进度放到上面、做小，不给“取消准备”（题早晚都要备）；
    - 只有用户的操作必须等题备好时（step=preparing_materials，例如开始模块检测），显示“先回去接着学”：回到原来的步骤，题照样在后台继续准备。

要做
  1. 聊天区上方只留一行：
     - 左边“小要点 x/y · 当前小要点标题”（新式点才有）；
     - 右边两个按钮“讲义（n）”“资料导航（n）”，没有内容就不显示；
     - 点按钮弹出浮层（讲义：掌握目标、每条讲义、看原文；资料导航：现有内容），浮在聊天区上面，不改变聊天区高度；点外面或 Esc 收起；一次只开一个；
     - 窄窗口下这一行可以换成图标按钮，但不换成多行块。
  2. 准备题进度：
     - 在顶栏“今天 x/y 分”旁边显示小标签，如“题目准备中 3/22”（带小转圈，开了“减少动态效果”就不转）；
     - 出错时小标签变成“准备失败，重试”，点了照现有 classroom.retry；
     - 去掉“取消准备”按钮。
  3. 必须等待时（step=preparing_materials）：
     - 聊天区里显示一行“正在准备这个模块的题（x/y），备好后自动继续”，以及按钮“先回去接着学”：调用现有 classroom.cancel_preparation（stop_preparation），回到 previous_step；
     - 确认后台备题没有被停掉——如果现有 stop_preparation 会停掉后台准备，改成只退出等待、不停准备（改 app/classroom.py 里这一处，回执写明）。
  4. 迟到提醒（“已经很晚了”）、失败提示照旧显示，但不要每条占一整块：能并进顶栏小标签的并进去；放不进的保持一行。
  5. 测试（文件名带 129）：真实 React 浏览器测试覆盖
     - 上方只有一行、聊天区高度不随讲义或导航展开变化；
     - 浮层开关（点外面、Esc、一次一个）；
     - 小标签显示进度与失败重试；
     - 没有“取消准备”；
     - 等待时“先回去接着学”回到原步骤，且后台准备仍在进行；
     - 旧式点（没有小要点）不显示那一行左边；
     - 两套主题、窄窗口。
     截改前改后的图放进报告。
  6. 只改 web/src/pages/Classroom.tsx、web/src/classroom/Conversation.tsx 里这几个组件和样式、必要时 app/classroom.py 的 stop_preparation，以及 tests/refactor/。

完成标准：第 5 条测试通过、有改前改后截图；最后全量一次。
回执：写到 D:\MentorFlow\.local\receipts\129-MF03.md。表格列：基线 ／ 提交 ／ 上方一行怎么排 ／ 浮层怎么开关 ／ 准备进度小标签 ／ 等待时“先回去接着学”与后台准备 ／ 其他提示怎么并 ／ 新增测试 ／ 截图 ／ 全量 ／ 本地最终占用。
给用户只回一行“129-MF03 完成/部分完成/受阻，回执已写入”。
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
