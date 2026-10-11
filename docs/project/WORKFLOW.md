# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读开头的「文档维护规则」和「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 开头三节和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-12）：线上 2.0.13，用户 2026-10-11 已用安装程序覆盖（HEALTHY，垃圾已清）。103-MF03 修课堂随口聊聊回答不保留和三个对话页的滚动（[BUG-78]），已起草，待转发。合入后架构师编网页、开源代码版给用户对照看（用户先关掉装好的软件，同一账号同时只能一台在线）；用户确认后发 2.0.14（只发更新），用户在软件里点更新。全量里 loading86、self_test81、status88 三个浏览器测试满载时偶发失败（单独跑通过），记为已知不稳定测试。

## 协作方式

- **先讨论、后派活（最优先）**：问题先和用户讨论清楚（现象、原因、牵连范围、前置依赖）再写任务；收到回执只讲审查结果、问题和要用户定的事，用户同意后再把整批任务原文放在回复最后，然后停下等转发。不出看着笃定但有漏洞的方案。
- **不懂就问**：有不懂或感觉有偏差，直接问用户。
- **一批派完、并行优先**：能并行、互不冲突的一次派完，人数不设上限；改同一文件同一部分的排先后；有依赖的写明先后与原因。
- **不重复造轮子（硬规定）**：每份任务第一节写“用的现成项目与许可证”，参照行业标准；要自己写的写明为什么没有现成的。
- **方案由架构师出**：架构师查证据、读相关代码、找开源做法，在任务里写清“怎么做”；员工照做。
- **派任务前的检查**：每条完成标准都是这个员工凭自己的权限能做到的（取决于外部的写成“查清后二选一”）；互相依赖的工作不拆成并行任务各自要求全量通过；写之前读相关代码、查环境。
- **员工实测不占用户的鼠标键盘（2026-10-11）**：真打包实测用 Windows 的 UI Automation（InvokePattern）点原生按钮、读文字；网页里的按钮和输入框用 Chrome DevTools 协议（测试环境设 QTWEBENGINE_REMOTE_DEBUGGING），因为 Qt 6.11 网页的无障碍接口只能聚焦、点不动；不用 computer-use、模拟输入或抢前台（102-MF04 用电脑界面操作时被用户按 Esc 中止）；测试窗口会出现在屏幕上，派任务时先告诉用户。
- **安装与更新先自己走通再叫用户测（用户 2026-10-11 定）**：凡是改到安装、更新、启动器、Updater 的，任务里必须要求用真打包程序走一遍完整流程（旧版 → 新版：软件内更新的两个入口、下次启动安装、安装程序覆盖升级，按改动取用），架构师看过证据才发版；没走通不叫用户测。替身测试不算（[BUG-71]）。
- **员工的测试（用户 2026-10-10 定）**：工作中只跑与改动相关的测试，全量只在最后跑一次，同一套测试和演练不重复跑；不做 AI 模拟学生的长流程测试。
- **员工自主**：任务写清决策原则，员工自己决定、在回执写理由；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。
- **两轮规则**：同一个问题第一轮按现方法修，没解决第二轮换方法（先查开源做法）；还不行带数据找用户定。
- **花钱的测试**：只为具体改动花钱，事先写下预期；能离线的先离线；预算分段，第一段没达到预期就停。
- **发版**：修好就用流水线发版，让用户在装好的软件上点更新亲测（操作手册“发布新版”）。**本机先验、GitHub 一版只跑一次（用户 2026-10-11 提出）**：先在本机用同一个打包脚本打预览版，跑一遍 scripts/release 的发版前总验收（102-MF04c 整理），过了才触发 mf-build / mf-publish；没在本机验过的版本不上 GitHub。正式版不在本机打：本机环境会把别的 DLL 混进包里（102-MF04 实测混入过 ICU/OpenSSL），而且同一版本两边打出来的文件不同，会扰乱以后的增量更新。
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
- **发布新版**（[SYS-06]）：①合入并推送 main；②手动运行 GitHub 工作流 mf-build（版本号、更新说明、是否必须及原因、基线版本，build_only=false），约 5 分钟；③下载它的 unsigned 产物，本机 `.venv/Scripts/python.exe -B scripts/release/sign_and_stage.py --artifact <zip> --private-key .local/release-keys/mentorflow-app-2026.private.key --work .local/test-tmp/release-<版本>/sign --ssh-identity E:/Codex/Secrets/MentorFlow/ssh/mentorflow-e1-ops-ed25519`；④手动运行 mf-publish（版本号；build_installer 默认 false 只发更新约 1～3 分钟，要新安装程序时设 true 约 10 分钟，或事后单独跑 mf-installer）；⑤用客户端代码对公网 stable.json 验签，看 aiteacher.clannad.win/download。触发和查看运行可用 GitHub 网页，或用本机 git 凭据调 GitHub API（.local/tmp/watch_run.sh 轮询到结束）。工作流只能手动触发，额度红线见 scripts/release/README.md。
- **启动/重启用户的源代码版**：`Start-Process -FilePath D:/MentorFlow/.venv/Scripts/pythonw.exe -ArgumentList 'D:/MentorFlow/refactor/desktop/source_start.pyw' -WorkingDirectory D:/MentorFlow -WindowStyle Hidden`。重启前一定先问用户（可能在上课）。
- **查用户软件的问题**：装好的软件日志 D:\MentorFlowApp\MentorFlow\Data\logs\mentorflow.log，源代码版日志 D:\MentorFlowData\account-shell\logs\mentorflow.log（JSON 行，只读）；数据库在对应的 learning\accounts\<账号>\*.sqlite3，只读查看用 `sqlite3.connect('file:...?mode=ro', uri=True)`。
- **界面构建**：PATH 加 `/d/MentorFlow/.local/node`，`node .local/web-deps/node_modules/vite/bin/vite.js build --config refactor/learning/web/vite.config.mjs`；dist 不进仓库。
- **写脚本**：含 Windows 路径或反斜杠的内容不要用 bash heredoc 或 python -c（会被吃掉，BUG-10），写成文件再运行。
- **服务器**：`ssh -F "D:/MentorFlow/.local/server/ssh_config" mentorflow`（89.45.45.28，mentorflow-admin，sudo -n）。线上后端 mentorflow-slim-backend:MF-75（切换与回滚脚本 /opt/mentorflow/state/mf75/switch.sh，上线前备份同目录）；入口 nginx 容器 mentorflow-e1-preview-edge-1（配置 /opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf）；更新与下载目录 /opt/mentorflow/update-static/e108/app/；资料白名单 /opt/mentorflow/state/mentorflow-e1-preview/catalog/。 2026-10-10 起两个 docker 网络设了 ip_range（BUG-45）：以后用 Compose 操作这些容器，命令末尾必须追加 -f /opt/mentorflow/state/bug45-20261010/network45.override.yml。
- **磁盘（用户 2026-10-10 定）**：测试与临时产物在 .local/test-tmp/ 下；冒烟测试、构建、中间文件用完就自动删，不留着等以后分类。员工删除被系统拦住时，架构师合入后直接删（只留 report.md 和截图），不用再问用户。

## 当前基线

| 项目 | 值 |
| --- | --- |
| main | 以 `git rev-parse --short HEAD` 为准；第九十五批合入 c77aaf07 |
| 已发布 | 2.0.13 = 10e625f4（非必须；下载站安装程序 2.0.13）；强制线 2.0.3 |
| GitHub | main 已推送（私有仓库）；发版需要的推送由架构师做，其余推送要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07）转发任务；工位（library、runtime、client、platform、tools、classroom、server）在任务里指定。同一个员工、同一个工位同时只能有一个任务。任务号写成“批次-员工编号”。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 空闲 | server |
| MF-02 | 空闲 | client |
| MF-03 | 103-MF03 随口聊聊保留回答、对话滚动、2.0.13 → 新版预览排练（已起草，待转发） | classroom |
| MF-04 | 空闲 | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 空闲 | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 103-MF03（classroom）：[BUG-78] 课堂随口聊聊保留回答 + 三个对话页滚动规则（[UI-00]）+ 2.0.13 代码 → 新代码的预览版排练。已起草，待转发。
- 之后：架构师合入 → 编网页 → 开源代码版给用户对照 → 用户确认 → 发 2.0.14（只发更新）→ 用户在软件里点更新。
- 下一批候选：[BUG-74] 更新提速、[BUG-77] 安装程序默认找已有安装位置（不和 103 一起发，免得 2.0.14 等）。

## 第一百零三批任务原文（2026-10-12，基线见任务）

### [103-MF03] classroom｜随口聊聊保留回答、对话滚动、2.0.13 → 新版预览排练（¥0）

~~~text
【MentorFlow 第一百零三批｜MF-03（工位 classroom）｜课堂“随口聊聊”答完后保留学生自己的回答，题目 → 我的回答 → 原文 对照着看；课堂、闲聊、建计划三个对话页改成“这一轮的开头顶到最上面”；最后用预览版走一遍 2.0.13 代码 → 新代码的软件内更新，给发 2.0.14 把关】

现成项目与许可证（第一节）
  - 滚动：用浏览器自带的 Element.scrollIntoView({block: "start"}) 和 CSS scroll-margin-top（Web 标准），不引第三方库。use-stick-to-bottom（MIT）一类库做的是“贴住底部”，和这次要的相反，不用。做法参照 ChatGPT、Claude 网页版：发出一句后，把这句顶到对话区顶端，回复在下面出。
  - 卡片写法：照抄项目里已有的问答题卡片 refactor/learning/web/src/classroom/RecallQuestion.tsx（“你的回答”在上，参考要点在下）的结构和样式，不另起一套。
  - 界面测试：照 tests/refactor/loading86、sources79 已有的浏览器测试写法（假桥接 + 真 Chromium）。
  - 预览版排练：用 scripts/release/rehearsal102/ 现成的一套（看它的 README.md），产品代码不为排练改动。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 446a3bf7         # 主线：2.0.13 已发布
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不访问正式账号服务器，不注册真实账号，不调用真实模型。D:\MentorFlowApp、D:\MentorFlowData\account-shell 只读；E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。遇到选择自己决定，回执写明理由，不停下来问。不要结束或重启用户正在用的软件进程。
不占用户的鼠标键盘（硬规定）：不用 computer-use，不模拟鼠标或键盘，不靠抢前台去操作窗口。网页里用 Chrome DevTools 协议，原生按钮用 UIA InvokePattern（rehearsal102 里都有）。测试窗口出现在屏幕上可以，不能关或操作用户自己的任何窗口。排练的所有进程 LOCALAPPDATA、TEMP、TMP 都指到任务目录。
测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。冻结构建、安装副本、下载源用完立刻自己删，只留 report.md、截图和小脚本。删除被拦住就列出路径交架构师，不换工具绕过。
同时在跑：无。
产物目录：D:\MentorFlow\.local\test-tmp\class103\。目标三小时内交回。花费上限 ¥0（老师用假模型）。
背景（CURRENT_STATE [BUG-78] [TEACH-07] [UI-00]）：用户在 2.0.13 上学习，没题的模块点“这个模块我都会了，直接考”，老师随口问三句。答完一句，自己的回答哪里都不显示了，只出原文，用户记不住自己刚才怎么说的，没法对照；原文出来后对话区还自动滚到最底，题目和原文开头被顶出屏幕。原因：app/classroom.py 的 reply() 在 direct_review 这一步刻意不保存回答；Classroom.tsx 的 direct-review 卡片只有题目和原文；三个对话页（Classroom.tsx 约 221 行、Chat.tsx 约 62 行、NewPlan.tsx 约 151 行）一律滚到最底。

要做
  1. 随口聊聊保留回答（app/classroom.py、Classroom.tsx）
     - 学生发的回答存进这次随口聊聊的课堂状态。仍然不记分，不写作答记录、档案、判定，也不进老师的上下文。
     - 卡片按“题目 → 我的回答 → 这个点的原文”排。三句都留在这一段里，往上翻能看到前面的；到学生选“跳过这个模块 / 接着学”为止。之后照现在一样收起，不进对话历史。
     - 中途关掉课堂再进来，已答的几句和回答都还在。
     - 老的课堂状态文件里没有新字段时照常打开（缺省为空），不用迁移，但要有一条测试覆盖。
     - 界面文字去掉“回答不保存”这类说法。例如说明改成“对照原文，自己判断理解得对不对；不判分、不记录。”，输入框提示改成“随口回答，不记分”。
  2. 对话滚动规则（[UI-00]；Classroom.tsx、Chat.tsx、NewPlan.tsx，抽一个共用的小 hook）
     - 一轮开始时，把这一轮的开头滚到对话区顶端：学生说了话（发送、点选项、点“懂了，继续”这类会显示成学生气泡的）就是学生那句；学生没说话（点“继续”“开始上课”）就是老师新内容的开头。
     - 随口聊聊答完后，顶到上面的是“我的回答”，原文在它下面。
     - 老师回复流式出来时不跟着滚到底。这一轮不满一屏时停在底部即可（浏览器会自然夹住，整轮都看得见），不加空白占位。
     - 用户自己往上翻了，就不再抢滚动条。
     - 打开页面、切换计划时，照旧显示到最新的位置。
     - 尊重“减少动态效果”：现有的 reduced 判断照用，开着就不做平滑动画。
  3. 测试
     - tests/refactor/direct97/ 按新行为更新：回答保留在状态里，但仍然没有作答记录、判定、档案写入；再加一条老状态文件兼容。
     - 新增一个浏览器测试（文件名带 103，别和已有的重名），截图并断言：
       a) 随口聊聊答完后，“我的回答”的顶端贴着对话区顶端，原文在它下面；三句答完三段都在。
       b) 课堂普通一轮里，学生那句顶在上面，老师的回复在下面。
       c) 闲聊和建计划各一轮，规则同上。
       d) 回复很短时停在底部，不出现大片空白。
  4. 预览版排练（给发 2.0.14 把关：用户那台是 2.0.13，下一版是他第一次真走软件内更新）
     用 rehearsal102，旧版 O 用 10e625f4（2.0.13 的代码）编预览版，新版用你的最终代码编预览版，测试签名、本机更新源、HTTPS 账号替身。按它的 README，这一轮把 MF_UAT_ROOT 设成 D:\MentorFlow\.local\test-tmp\class103，common.py 里换一个独立的 APP_ID（例如 MentorFlow.UAT.class103）。
     - 走一遍：O 真安装 → 登录 → 主界面网页按钮“立即更新并重启”→ 自己关掉 → “正在更新”→ 新版自己打开。
     - 核对：版本是新版；HEALTHY；pending 消失；Updater 已换；设置和 Key 还在。
     - 用 CDP 核对新版打包里的网页确实是新界面，例如在加载的脚本里找到新的说明文字。
     脚本要小改（比如让 O 和新版来自不同源码）就改在 rehearsal102 里，一起提交。结束用排练的卸载器清掉本轮预览版的注册和快捷方式，前后快照对比正式版不变。
  5. 只改 refactor/learning/app/classroom.py、refactor/learning/web/src/（课堂、闲聊、建计划页和新 hook）、tests/refactor/ 和 scripts/release/rehearsal102/。要动别处的先写清原因，再自己决定，回执里说明。
完成标准：
  - 第 1、2 条按上面的规则做完；第 3 条测试通过，有截图。
  - 第 4 条 O → 新版的主界面入口更新走通，有截图和日志片段。
  - 最后全量跑一次并通过。loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明。
  - 达不到就写“部分完成”，排练不能用替身点击或离线测试代替。
回执：写到 D:\MentorFlow\.local\receipts\103-MF03.md。表格列：基线 ／ 提交 ／ 随口聊聊改动与截图 ／ 三个页面滚动截图 ／ 老状态兼容 ／ 预览排练结果与耗时 ／ 新问题与修法 ／ 全量 ／ 本地最终占用。给用户只回一行“103-MF03 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\class103\report.md。
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
