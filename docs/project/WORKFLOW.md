# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读「当前阶段」），这里只写编号引用。规则只在 [AGENTS.md](../../AGENTS.md)，这里只写固定做法。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **当前维护320**（2026-11-07）：用户现授权[BUG-215] [BUG-216] [BUG-217]本地修复及测试残留收尾；MF01/MF04/MF07已分别收到修复任务，MF05已收到清理脚本任务。等待完成回执后派独立核验并整合；不推送、不发版、不改正式服务器。319工具常驻、真实数据及历史保留。删除曾被自动审批拒绝，准备可直接执行且限定路径的人工命令，不绕过。

- **谁在做什么**：用户定需求、做决定、亲自测试；当班架构师直接向员工聊天派工、收完成回执、审查整合并持续调度；员工在各自工位干活，彼此禁止通信。用户 2026-11-06 授权直接消息往返并要求积极使用子代理并行独立工作，同一时间只有一位架构师动主目录。
- **接班**：读 AGENTS.md、CURRENT_STATE 的「当前阶段」和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **既有交付**：面经库 [DOC-03] 313、314、315 已交并复核；当前人员以员工表为准。
  - 回执处理：员工聊天报告完成后才读回执；未完成的只用聊天状态等待，不扫描半成品。临时文件按既有清理规则处理。

## 固定做法（规则见 AGENTS.md）

- **给员工写任务的措辞**（用户 2026-10-29 定）：不写红队、蓝队、攻、越权、令牌重放这类说法，不让员工去读这类旧报告，复现步骤要用就自己改写进任务；写成普通测试用语。员工被内容限制拦下时，换新聊天、拆小任务重派；普通的输出截断不是停下的理由，任务里写明分段读接着做（用户 2026-10-29，232 因此白跑一次）。

- **员工实测不占用户的鼠标键盘（2026-10-11）**：真打包实测用 Windows 的 UI Automation（InvokePattern）点原生按钮、读文字；网页里的按钮和输入框用 Chrome DevTools 协议（测试环境设 QTWEBENGINE_REMOTE_DEBUGGING），因为 Qt 6.11 网页的无障碍接口只能聚焦、点不动；不用 computer-use、模拟输入或抢前台（102-MF04 用电脑界面操作时被用户按 Esc 中止）；测试窗口会出现在屏幕上，派任务时先告诉用户。
- **发版**（何时发布、发布哪个版本由用户决定；[SYS-06]）：按“发布新版”操作手册使用流水线。**本机先验、GitHub 一版只跑一次（用户 2026-10-11 定）**：先用同一打包脚本在本机打预览版，跑过 `scripts/release` 的发版前总验收，再触发 mf-build / mf-publish；没在本机验过的版本不上 GitHub。正式包只由流水线构建，避免本机 DLL 混入和同版本内容不同。**版本号只在实际发布时占用（用户 2026-10-12 定）**：下一版发哪个号由用户决定，发布前修 bug 仍用这个号；本机排练测试号不占正式号。本机预览版和员工测试不替代用户亲测。
- **任务文件与短通知**：规则见 AGENTS 第 9、15 条（只给现在就能开工的通知，每位一个代码块）；本文件待派队列只写任务号、一句话、文件路径和先后。通知格式例：“新任务 134-MF04：读 D:\MentorFlow\.local\tasks\134-MF04.md 照做，回执写到 D:\MentorFlow\.local\receipts\134-MF04.md。”
- **任务状态**：任务写好未发送标“已起草，待发送”；架构师直接消息发送成功后标“进行中”，员工完成消息到达后审查回执；合入后从员工表和待派队列清除，保留必要报告。
- **署名**：主目录提交用 `git -c user.name="tonyswift868" -c user.email="205869700+tonyswift868@users.noreply.github.com" commit ...`，结尾加 Co-Authored-By 行；不写进共享 git 配置。员工用各自工位名提交。

## 架构师操作手册

- **合入员工分支**：主目录 `git merge --no-ff office/<工位>`（或 `git diff --binary <基线> <员工提交> | git apply --index --3way`），逐文件看改动是否在任务范围内；合入后只做改动文件的编译检查（`py_compile`），相关测试、全量和查失败原因交给下一份任务顺带跑或单独派员工（AGENTS 第 9 条）。
- **全量测试**（员工跑，架构师不跑；2026-10-21 约 4580 项、约 55 分钟）：`TMP=D:/MentorFlow/.local/test-tmp/<子目录> TEMP=同上 .venv/Scripts/python.exe -m pytest tests/refactor -q -p no:cacheprovider --basetemp=D:/MentorFlow/.local/test-tmp/<子目录>/pt`，输出重定向到文件、放后台跑，跑完删子目录。Java 构建缓存在 .local/test-tmp/java-cache（共享、加锁，不要删），不用再设 SHIP156_TMP（[BUG-108]）。
- **基线**：合入并提交后 `git rev-parse --short HEAD` 就是下一批任务的基线。
- **发布新版**（[SYS-06]）：①合入并推送 main；②手动运行 GitHub 工作流 mf-build（版本号、更新说明、是否必须及原因、基线版本，build_only=false），约 5 分钟；③下载它的 unsigned 产物，本机 `.venv/Scripts/python.exe -B scripts/release/sign_and_stage.py --artifact <zip> --private-key .local/release-keys/mentorflow-app-2026.private.key --work .local/test-tmp/release-<版本>/sign --ssh-identity E:/Codex/Secrets/MentorFlow/ssh/mentorflow-e1-ops-ed25519`；④手动运行 mf-publish（版本号；build_installer 默认 false 只发更新约 1～3 分钟，要新安装程序时设 true 约 10 分钟，或事后单独跑 mf-installer）；⑤用客户端代码对公网 stable.json 验签（`refactor.desktop.delivery.update_protocol.verify_release(原始字节, {key_id: base64 解码的公钥}, current=上一版)`，公钥在 .local/release-keys/mentorflow-app-2026.public.json），看 aiteacher.clannad.win/download。触发和查看运行可用 GitHub 网页，或用本机 git 凭据调 GitHub API（.local/tmp/watch_run.sh 轮询到结束）。工作流只能手动触发，额度红线见 scripts/release/README.md。
- **本机打包**（发版先验，[BUG-202]）：只能用专用打包环境，不能用平时测试的环境。在干净的员工工位里跑（如 D:\MentorFlow-office\runtime\repo，先 reset 到要发的提交），不在主目录跑：打包收尾要核对代码版本，打包期间主目录一提交就失败。在工位目录里 PowerShell 照抄：
  `$env:MF_TEMP_ROOT='D:\MentorFlow\.local\test-tmp'; $env:MF_BUILD_TOOLS='D:\MentorFlow\.local\release-tools\build-tools'; $env:MF_NODE_DIR='D:\MentorFlow\.local\node'; & D:\MentorFlow\.venv\Scripts\python.exe -B scripts\release\build_release.py --version <测试号如 9.257.1> --baseline <git rev-parse --short=8 HEAD> --valid-days 1096 --base-versions <线上版本> --notes <说明> --output-dir D:\MentorFlow\.local\test-tmp\<任务目录>\build --previous-release <已验签的线上 stable.json 副本>`
  先只跑环境检查（打印 Exact build pins verified: 73 才往下）。专用打包环境在 .local/release-tools/build-tools，不放进 test-tmp，清理临时文件时别删。
- **发版辅助脚本**（.local/tools/release/，不进仓库）：`dispatch_build.py <版本> <基线版本> <更新说明>` 触发 mf-build（非必须）；`gh_release.py latest <工作流文件>` 看最近一次运行，`gh_release.py download <运行号> unsigned-<版本> <zip路径>` 下载产物（GitHub 跳转到存储地址时不能带认证头），`gh_release.py publish <版本>` 触发 mf-publish。令牌运行时从 Git Credential Manager 取，不落盘。`verify_public.py <版本> <上一版>` 从公网下载 stable.json 并用客户端代码验签（用 curl 加浏览器 UA 下载，Cloudflare 对 Python 默认 UA 回 403），副本存到 .local/test-tmp/release-<版本>/verify/stable.json，下次本机打包的 --previous-release 就用它。
- **启动/重启用户的源代码版**：`Start-Process -FilePath D:/MentorFlow/.venv/Scripts/pythonw.exe -ArgumentList 'D:/MentorFlow/refactor/desktop/source_start.pyw' -WorkingDirectory D:/MentorFlow -WindowStyle Hidden`。重启前一定先问用户（可能在上课）。
- **查用户软件的问题**：装好的软件日志 D:\MentorFlowApp\MentorFlow\Data\logs\mentorflow.log，源代码版日志 D:\MentorFlowData\account-shell\logs\mentorflow.log（JSON 行，只读）；数据库在对应的 learning\accounts\<账号>\*.sqlite3，只读查看用 `sqlite3.connect('file:...?mode=ro', uri=True)`。
- **界面构建**：PATH 加 `/d/MentorFlow/.local/node`，`node .local/web-deps/node_modules/vite/bin/vite.js build --config refactor/learning/web/vite.config.mjs`；dist 不进仓库。
- **写脚本**：含 Windows 路径或反斜杠的内容不要用 bash heredoc 或 python -c（会被吃掉，BUG-10），写成文件再运行。
- **往服务器传源码**：`git -c core.autocrlf=false -c core.eol=lf archive --format=tar -o <tar> <提交> refactor/server refactor/server-java`，scp 上去解到 /opt/mentorflow/releases/java-<提交>/，再核对关键文件 sha256；不加这两个参数 .sh 会带 Windows 换行，跑不了（[BUG-107]）。
- **服务器**：当前服务端入口与数据库状态以 [ARCH-09] 为准，Java 回退到 226 的步骤和限制见 `refactor/server-java/deploy/RELEASE269.md`；每次上线第一步跑 `refactor/server-java/deploy/preflight.sh`（ssh 用 `sh -s < preflight.sh` 喂进去，只读），全部 OK 才往下；服务器现场条件只写在 deploy/README.md 那一节；员工上线时碰到的坑，合入时当场收进脚本或那一节，不只留在回执（[BUG-209]）。不要依照历史部署命令猜测当前配置；服务器操作只按获授权的具体任务执行。
- **等回执（用户 2026-11-06 定）**：用聊天等待工具接收员工完成状态和直接回执消息，收到后读 .local/receipts、审查整合；员工中途半成品不算完成，不扫描文件监控。遇到需用户决定的事停止依赖该决定的派工，等待用户。
- **排练产物（2026-10-19）**：员工的真打包排练每次留下几十 GB（160 约 70 GB、168 约 25 GB），而且员工的递归删除会被系统拦住；收到这类回执后，架构师先把任务目录里除 report.md 和截图以外的都删掉，再做别的。
- **磁盘（用户 2026-10-10 定）**：测试与临时产物在 .local/test-tmp/ 下；冒烟测试、构建、中间文件用完就自动删，不留着等以后分类。员工删除被系统拦住时，架构师合入后直接删（只留 report.md 和截图），不用再问用户。

## 当前基线

| 项目 | 值 |
| --- | --- |
| main | 以 `git rev-parse --short HEAD` 为准；2.0.45正式源码及本地门禁基线44cb3e22，交班文档提交不改变产品代码 |
| 已发布 | 2.0.45，普通可选软件内更新，参数及证据见[SYS-06]；下载站安装程序未新编，仍按原策略保留；服务器Java269、数据库迁移0047未改动 |
| GitHub | main 已推送（私有仓库），远端只有 main 一个分支（9-26 遗留的 15 个 Dependabot 升级请求与分支 2026-10-12 经用户同意已关闭并删除，它们的配置已随旧代码删除，不会再生成）；发版需要的推送由架构师做，其余推送要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

架构师按员工编号（MF-01～MF-07）直接向已确认的员工聊天发任务；工位（library、runtime、client、platform、tools、classroom、server）在任务里指定。同一个员工、同一个工位同时只能有一个任务。任务号写成“批次-员工编号”。用户 2026-11-05 把七个旧会话归档、换了一批新会话，编号不变，加名字；新会话不知道以前的任务，任务文件要自成一体。

| 员工 | 名字 | 当前任务 | 工位 |
| --- | --- | --- | --- |
| MF-01 | 服务端 | 原修复已合入；320-MF01-followup进行中：挂起交错的错误展示二次异常丢回执 | server |
| MF-02 | 客户端 | 320-MF02-qa完成：3个真实设置页取消/正常场景通过 | client |
| MF-03 | 课堂 | 320-MF03-qa完成：真实混合导入/重试/页面/SQLite一致，未改产品 | classroom |
| MF-04 | 运行时 | 320-MF04-session-review进行中：BUG-215独立代码/锁/恢复审查 | runtime |
| MF-05 | 发版 | 320-MF05-final-cleanup进行中：旧18+本轮小缓存统一命令，待followup结束后盘点 | platform |
| MF-06 | 工具 | 320-MF06-integration完成：de03890c合入组合18项通过；待补丁后限定复核 | tools |
| MF-07 | 资料 | 320-MF07完成：ef7e63ff合入d04ef1ba，12项相关通过；待独立验收 | library |

## 待派队列

- 320：三个修复提交完成并经架构师读回执后，派MF02/MF03/MF06独立核验及合入验证；依赖源码尚未交付，不提前发等待任务。清理保留小型复现证据和持久数据，MF05脚本准备好即给用户执行命令。

- 2.0.41 已发布（2026-11-01，非必须，基线 2.0.40）：263（[BUG-204]）、266（[BUG-206]）；267-MF01 公网端到端上传 1006 段通过、267-MF06 全量全过；架构师本机打包、推送、mf-build、签名、mf-publish、验签各一次成功。待用户更新后亲测。
- 269-MF03 已合入 7d1aebcc、269-MF01 已合入 1070685b（2026-11-01，[RAG-01]）。
- 270-MF06 已完成（2026-11-01，基线 1070685b）：5259 过、1 失败（测试过时，架构师 323971fd 修好、单项通过）、49 跳过，Java 一项没跳。报告 .local/test-tmp/full270/report.md。
- 项目讲义 [DOC-02]：讲义 .local/handout/1181da9d（改前备份 test-tmp/handout-backup-1181da9d）。抽查报告在 test-tmp/review298、review299（问题清单 issues.json），写作者报告在 write294～write297，分工表在 handout293/report.md 第 151～180 行，Graphify 安装包在 analyzer283/keep。
  - 结论文件：303 在 test-tmp/check303-MF0x，304 复查在 check304-MF06/07，305、306 核对在 verify305-MF0x、verify306-MF0x（checked.tsv、issues.json、report.md）；返工交付在 rework304-A/B/D、rework305-C/D、rework306-D（changes.json、report.md）；302 已合入 a84c95a6。306 回来（2026-11-03）：C 份 85 条核完，79 条对，剩 6 条（错 2、小错 4，其中 3 条是返工改出的新毛病）；D 份 24 个文件夹说明核完，16 条对，剩 8 条（错 5、小错 3），MF-05 另自报 3 条出处问题；MF-05 补改的 D 份 4 条已改好，架构师抽 2 条对照快照无误；讲义结构检查通过。全讲义 1206 条现在还剩 17 条要补（C 6、D 11），考试还没开始。
  - 考试 307-MF07（2026-11-03）：只看讲义答 exam285 的 30 道题，7/60；讲义写错 0，没写到 16，讲义里有但没找到 13，题目过期 1。原因在架构师：给写作者定的标准是每个文件两三句“负责什么”，没要求写“怎么做”（几种情况、数据存哪、返回什么、出错怎么办）和“跟谁配合”；之后五轮只查对错、改错，没加内容；考试本该写完就考。考生约两分钟交卷，也答得粗（评分在 test-tmp/exam307-MF07/score.md）。
  - 309-MF06 重考回来（2026-11-03）：同样只看讲义，29/60（307 是 7/60）；讲义写错 0，没写到 11，讲义里有但没找到 16，题目过期 1。用户 2026-11-03 指出照考题补空位对随便出的题没用，310（补 13 个空位）未发、撤回。用户 2026-11-03 定讲义的用途：给 dots 当字典，问哪个文件是干什么的，dots 直接查讲义快速答出、不说错，省掉每次读源代码的时间；源代码只在用户要求时才读。架构师已写 .local/interview/prompt-handout.md，单独一份，不再要求先读 prompt.md：用户问就查讲义答；用户说“开始/继续”就按讲义从全貌往下一个文件夹一个文件夹带学，每块问一两个问题，进度记在 .local/interview/讲义学习记录.md。快照 1181da9d 之后产品代码（refactor/）没有改过。用户 2026-11-04 用 dots 测：问文件干什么，dots 常要先去查，答得慢。原因是讲义 42 MB、600 多页，dots 读不完，每次都得找页、找文件。310-MF04 完成（2026-11-04）：速查表 .local/handout/1181da9d-速查表/ 的代码.md（654 个文件，每个一行）和测试.md（987 个文件，按 236 组），从讲义说明压成一句话，独立复核改了 293 句，架构师另用脚本核过 1641 个文件一个不漏、不重。prompt-handout.md 已改：dots 开场先整张读两张表，问文件干什么直接从表里答，细讲时再按编号打开讲义。下一步：用户用 dots 测。
- 面经库 [DOC-03]：311 七份已交（2026-11-05），交付在 .local/interview/面经库/，目录是 00-总目录与带学说明.md。312 补全已交（2026-11-05），架构师已复核、更新总目录、删临时目录；带学和拷打说明已写进 00 文件，用户 2026-11-05 认可。
- 273-MF04 已完成、合入 c82e8e7c（[BUG-208]，打包固定官方 SQLite 3.54.1，真包旧版 → 新版走通）：随 2.0.42 发。
- 候选（等用户提）：用户学项目资料时的反馈 → 改 docs/learn，获授权后导出并发布 mentorflow-guide（AGENTS 第 12 条）；发给朋友前重做安装程序并走一遍全新安装和注册（[SYS-06] [ACC-02]）。模拟面试提示词已交付，学习规则见 [DOC-01]。

## 回执格式

- **回执写文件与直接报告（用户 2026-11-06 定）**：员工把回执表格写到 `D:\MentorFlow\.local\receipts\<批次>-<员工>.md`，完成后直接向架构师聊天报告状态、回执路径和提交号；架构师收到后读回执。员工之间不得发送任务或回执。每份任务写明可核实的用户授权和架构师聊天ID。


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

新任务照 .local\tasks 里最近一份任务文件的格式写（第一节现成项目与许可证、开工同步、禁止事项与磁盘规则、证据来源、同时在跑、花费、背景、要做、完成标准、验收、回执项目）。
