# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读「当前阶段」），这里只写编号引用。规则只在 [AGENTS.md](../../AGENTS.md)，这里只写固定做法。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 的「当前阶段」和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-31）：2.0.39 已发布并验签（资料问答四件事），等用户亲测。没有员工在做的任务，待派队列空。
  - 回执处理：用户说做完了才读回执（不挂后台监视）；员工删临时文件常被系统拦住，合入后由架构师删 .local/test-tmp 下的任务目录，只把 report.md（和截图）留到 .local/r<批次>/。

## 固定做法（规则见 AGENTS.md）

- **给员工写任务的措辞**（用户 2026-10-29 定）：不写红队、蓝队、攻、越权、令牌重放这类说法，不让员工去读这类旧报告，复现步骤要用就自己改写进任务；写成普通测试用语。员工被内容限制拦下时，换新聊天、拆小任务重派；普通的输出截断不是停下的理由，任务里写明分段读接着做（用户 2026-10-29，232 因此白跑一次）。

- **员工实测不占用户的鼠标键盘（2026-10-11）**：真打包实测用 Windows 的 UI Automation（InvokePattern）点原生按钮、读文字；网页里的按钮和输入框用 Chrome DevTools 协议（测试环境设 QTWEBENGINE_REMOTE_DEBUGGING），因为 Qt 6.11 网页的无障碍接口只能聚焦、点不动；不用 computer-use、模拟输入或抢前台（102-MF04 用电脑界面操作时被用户按 Esc 中止）；测试窗口会出现在屏幕上，派任务时先告诉用户。
- **发版**（何时发布、发布哪个版本由用户决定；[SYS-06]）：按“发布新版”操作手册使用流水线。**本机先验、GitHub 一版只跑一次（用户 2026-10-11 定）**：先用同一打包脚本在本机打预览版，跑过 `scripts/release` 的发版前总验收，再触发 mf-build / mf-publish；没在本机验过的版本不上 GitHub。正式包只由流水线构建，避免本机 DLL 混入和同版本内容不同。**版本号只在实际发布时占用（用户 2026-10-12 定）**：下一版发哪个号由用户决定，发布前修 bug 仍用这个号；本机排练测试号不占正式号。本机预览版和员工测试不替代用户亲测。
- **任务文件与短通知**：规则见 AGENTS 第 9、15 条（只给现在就能开工的通知，每位一个代码块）；本文件待派队列只写任务号、一句话、文件路径和先后。通知格式例：“新任务 134-MF04：读 D:\MentorFlow\.local\tasks\134-MF04.md 照做，回执写到 D:\MentorFlow\.local\receipts\134-MF04.md。”
- **任务状态**：任务写好标“已起草，待转发”；用户说发了才改“进行中”；合入后从员工表和待派队列删掉，任务原文也删。
- **署名**：主目录提交用 `git -c user.name="tonyswift868" -c user.email="205869700+tonyswift868@users.noreply.github.com" commit ...`，结尾加 Co-Authored-By 行；不写进共享 git 配置。员工用各自工位名提交。

## 架构师操作手册

- **合入员工分支**：主目录 `git merge --no-ff office/<工位>`（或 `git diff --binary <基线> <员工提交> | git apply --index --3way`），逐文件看改动是否在任务范围内；合入后只做改动文件的编译检查（`py_compile`），相关测试、全量和查失败原因交给下一份任务顺带跑或单独派员工（AGENTS 第 9 条）。
- **全量测试**（员工跑，架构师不跑；2026-10-21 约 4580 项、约 55 分钟）：`TMP=D:/MentorFlow/.local/test-tmp/<子目录> TEMP=同上 .venv/Scripts/python.exe -m pytest tests/refactor -q -p no:cacheprovider --basetemp=D:/MentorFlow/.local/test-tmp/<子目录>/pt`，输出重定向到文件、放后台跑，跑完删子目录。Java 构建缓存在 .local/test-tmp/java-cache（共享、加锁，不要删），不用再设 SHIP156_TMP（[BUG-108]）。
- **基线**：合入并提交后 `git rev-parse --short HEAD` 就是下一批任务的基线。
- **发布新版**（[SYS-06]）：①合入并推送 main；②手动运行 GitHub 工作流 mf-build（版本号、更新说明、是否必须及原因、基线版本，build_only=false），约 5 分钟；③下载它的 unsigned 产物，本机 `.venv/Scripts/python.exe -B scripts/release/sign_and_stage.py --artifact <zip> --private-key .local/release-keys/mentorflow-app-2026.private.key --work .local/test-tmp/release-<版本>/sign --ssh-identity E:/Codex/Secrets/MentorFlow/ssh/mentorflow-e1-ops-ed25519`；④手动运行 mf-publish（版本号；build_installer 默认 false 只发更新约 1～3 分钟，要新安装程序时设 true 约 10 分钟，或事后单独跑 mf-installer）；⑤用客户端代码对公网 stable.json 验签（`refactor.desktop.delivery.update_protocol.verify_release(原始字节, {key_id: base64 解码的公钥}, current=上一版)`，公钥在 .local/release-keys/mentorflow-app-2026.public.json），看 aiteacher.clannad.win/download。触发和查看运行可用 GitHub 网页，或用本机 git 凭据调 GitHub API（.local/tmp/watch_run.sh 轮询到结束）。工作流只能手动触发，额度红线见 scripts/release/README.md。
- **本机打包**（发版先验，[BUG-202]）：只能用专用打包环境，不能用平时测试的环境。在干净的员工工位里跑（如 D:\MentorFlow-office\runtime\repo，先 reset 到要发的提交），不在主目录跑：打包收尾要核对代码版本，打包期间主目录一提交就失败。在工位目录里 PowerShell 照抄：
  `$env:MF_TEMP_ROOT='D:\MentorFlow\.local\test-tmp'; $env:MF_BUILD_TOOLS='D:\MentorFlow\.local\release-tools\build-tools'; $env:MF_NODE_DIR='D:\MentorFlow\.local\node'; & D:\MentorFlow\.venv\Scripts\python.exe -B scripts\release\build_release.py --version <测试号如 9.257.1> --baseline <git rev-parse --short=8 HEAD> --valid-days 1096 --base-versions <线上版本> --notes <说明> --output-dir D:\MentorFlow\.local\test-tmp\<任务目录>\build --previous-release <已验签的线上 stable.json 副本>`
  先只跑环境检查（打印 Exact build pins verified: 73 才往下）。专用打包环境在 .local/release-tools/build-tools，不放进 test-tmp，清理临时文件时别删。
- **发版辅助脚本**（.local/tools/release/，不进仓库）：`dispatch_build.py <版本> <基线版本> <更新说明>` 触发 mf-build（非必须）；`gh_release.py latest <工作流文件>` 看最近一次运行，`gh_release.py download <运行号> unsigned-<版本> <zip路径>` 下载产物（GitHub 跳转到存储地址时不能带认证头），`gh_release.py publish <版本>` 触发 mf-publish。令牌运行时从 Git Credential Manager 取，不落盘。核对 stable.json 用 `curl -A Mozilla/5.0`（Cloudflare 对 Python 默认 UA 回 403）。
- **启动/重启用户的源代码版**：`Start-Process -FilePath D:/MentorFlow/.venv/Scripts/pythonw.exe -ArgumentList 'D:/MentorFlow/refactor/desktop/source_start.pyw' -WorkingDirectory D:/MentorFlow -WindowStyle Hidden`。重启前一定先问用户（可能在上课）。
- **查用户软件的问题**：装好的软件日志 D:\MentorFlowApp\MentorFlow\Data\logs\mentorflow.log，源代码版日志 D:\MentorFlowData\account-shell\logs\mentorflow.log（JSON 行，只读）；数据库在对应的 learning\accounts\<账号>\*.sqlite3，只读查看用 `sqlite3.connect('file:...?mode=ro', uri=True)`。
- **界面构建**：PATH 加 `/d/MentorFlow/.local/node`，`node .local/web-deps/node_modules/vite/bin/vite.js build --config refactor/learning/web/vite.config.mjs`；dist 不进仓库。
- **写脚本**：含 Windows 路径或反斜杠的内容不要用 bash heredoc 或 python -c（会被吃掉，BUG-10），写成文件再运行。
- **往服务器传源码**：`git -c core.autocrlf=false -c core.eol=lf archive --format=tar -o <tar> <提交> refactor/server refactor/server-java`，scp 上去解到 /opt/mentorflow/releases/java-<提交>/，再核对关键文件 sha256；不加这两个参数 .sh 会带 Windows 换行，跑不了（[BUG-107]）。
- **服务器**：当前服务端入口与数据库状态以 [ARCH-09] 为准，Python 226 备用的授权恢复步骤见 `refactor/server-java/deploy/RELEASE226.md`。不要依照历史部署命令猜测当前配置；服务器操作只按获授权的具体任务执行。
- **等回执（用户 2026-10-28 定，取代 10-14 的自动等）**：不挂后台监视。员工做完由用户告诉架构师，架构师再去读 .local/receipts 里的回执、审查合入、清理、更新文档；用户没说之前不去看，员工中途写的半成品回执不算。
- **排练产物（2026-10-19）**：员工的真打包排练每次留下几十 GB（160 约 70 GB、168 约 25 GB），而且员工的递归删除会被系统拦住；收到这类回执后，架构师先把任务目录里除 report.md 和截图以外的都删掉，再做别的。
- **磁盘（用户 2026-10-10 定）**：测试与临时产物在 .local/test-tmp/ 下；冒烟测试、构建、中间文件用完就自动删，不留着等以后分类。员工删除被系统拦住时，架构师合入后直接删（只留 report.md 和截图），不用再问用户。

## 当前基线

| 项目 | 值 |
| --- | --- |
| main | 以 `git rev-parse --short HEAD` 为准；2.0.39 发版时推送到 3c106e74（2026-10-31），之后的提交随下次发版推送 |
| 已发布 | 2.0.38（2026-10-29，非必须，只发软件内更新，基线 2.0.37，mf-build 40810814819、mf-publish 40811427277，公网 stable.json 已验签，[SYS-06]）；下载站安装程序 2.0.15；强制线 2.0.3；服务器 Java mentorflow-java-server:226（2026-10-29，237-MF01），数据库迁移 0046 |
| GitHub | main 已推送（私有仓库），远端只有 main 一个分支（9-26 遗留的 15 个 Dependabot 升级请求与分支 2026-10-12 经用户同意已关闭并删除，它们的配置已随旧代码删除，不会再生成）；发版需要的推送由架构师做，其余推送要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07）转发任务；工位（library、runtime、client、platform、tools、classroom、server）在任务里指定。同一个员工、同一个工位同时只能有一个任务。任务号写成“批次-员工编号”。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 空闲（247-MF01 已完成，线上网关已改） | server |
| MF-02 | 空闲（248-MF02 已合入） | client |
| MF-03 | 空闲（253-MF03 已合入） | classroom |
| MF-04 | 空闲（257-MF04 受阻，本机打包改由架构师做，[BUG-202]） | runtime |
| MF-05 | 空闲（254-MF05 已合入） | platform |
| MF-06 | 空闲（256-MF06 已完成） | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 2.0.39 已发布（2026-10-31，非必须，基线 2.0.38）：248～254 全部在内；本机先验和 GitHub 打包各一次成功，公网验签通过（[SYS-06] [BUG-202]）。
- 候选（等用户提）：用户学项目资料时的反馈 → 改 docs/learn，获授权后导出并发布 mentorflow-guide（AGENTS 第 12 条）；发给朋友前重做安装程序并走一遍全新安装和注册（[SYS-06] [ACC-02]）。模拟面试提示词已交付，学习规则见 [DOC-01]。

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

新任务照 .local\tasks 里最近一份任务文件的格式写（第一节现成项目与许可证、开工同步、禁止事项与磁盘规则、证据来源、同时在跑、花费、背景、要做、完成标准、验收、回执项目）。
