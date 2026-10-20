# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读「当前阶段」），这里只写编号引用。规则只在 [AGENTS.md](../../AGENTS.md)，这里只写固定做法。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 的「当前阶段」和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-19）：线上客户端 2.0.28（07:45 发布，[BUG-115]～[BUG-117]，main 0d86e1fd 已推送）；服务器 Java eeb63f78，数据库迁移 0045。2026-10-20 18:21～18:38 服务商网络中断（[BUG-118]，已自行恢复，事后已全库备份）。172、173、174 三份调研已起草待转发，其余员工空闲。用户在软件里点更新后继续新建计划学本项目（[DOC-01]）。收到员工回执后先清理任务目录（只留 report.md 和截图）。

## 固定做法（规则见 AGENTS.md）

- **员工实测不占用户的鼠标键盘（2026-10-11）**：真打包实测用 Windows 的 UI Automation（InvokePattern）点原生按钮、读文字；网页里的按钮和输入框用 Chrome DevTools 协议（测试环境设 QTWEBENGINE_REMOTE_DEBUGGING），因为 Qt 6.11 网页的无障碍接口只能聚焦、点不动；不用 computer-use、模拟输入或抢前台（102-MF04 用电脑界面操作时被用户按 Esc 中止）；测试窗口会出现在屏幕上，派任务时先告诉用户。
- **发版**（什么时候发由用户定）：用流水线发版，让用户在装好的软件上点更新亲测（操作手册“发布新版”）。**本机先验、GitHub 一版只跑一次（用户 2026-10-11 提出）**：先在本机用同一个打包脚本打预览版，跑一遍 scripts/release 的发版前总验收（102-MF04c 整理），过了才触发 mf-build / mf-publish；没在本机验过的版本不上 GitHub。正式版不在本机打：本机环境会把别的 DLL 混进包里（102-MF04 实测混入过 ICU/OpenSSL），而且同一版本两边打出来的文件不同，会扰乱以后的增量更新。**版本号只在真正发布时占用（用户 2026-10-12 定）**：定下要发的下一版（现在是 2.0.28），在发布前查出的 bug 修完仍发这个版本号，不跳到下一个号；本机排练用的测试版本号（如 9.103.14）不算。
- **任务文件与短通知（用户 2026-10-15 定）**：任务全文只写在 D:\MentorFlow\.local\tasks\<任务号>.md（和回执目录 .local\receipts 并列），本文件待派队列只写任务号、一句话和文件路径，不贴全文。回复用户时最后给每位员工一行短通知，例如“MF-04：新任务 134-MF04，读 D:\MentorFlow\.local\tasks\134-MF04.md 照做，回执写到 D:\MentorFlow\.local\receipts\134-MF04.md。”用户只转发这一行。短通知放在代码块里（每位员工一个代码块），用户点一下就能复制（用户 2026-10-17 定）。
- **任务状态**：任务写好标“已起草，待转发”；用户说发了才改“进行中”；合入后从员工表和待派队列删掉，任务原文也删。
- **署名**：主目录提交用 `git -c user.name="tonyswift868" -c user.email="205869700+tonyswift868@users.noreply.github.com" commit ...`，结尾加 Co-Authored-By 行；不写进共享 git 配置。员工用各自工位名提交。

## 架构师操作手册

- **合入员工分支**：主目录 `git merge --no-ff office/<工位>`（或 `git diff --binary <基线> <员工提交> | git apply --index --3way`），逐文件看改动是否在任务范围内；跑本批改动相关的测试（`pytest $(git diff --name-only <基线> HEAD | grep '^tests/.*test_.*\.py$')`），两份以上改动合在一起时再跑一次全量。
- **全量测试**（2026-10-17 约 4230 项、40～45 分钟）：`TMP=D:/MentorFlow/.local/test-tmp/<子目录> TEMP=同上 .venv/Scripts/python.exe -m pytest tests/refactor -q -p no:cacheprovider --basetemp=D:/MentorFlow/.local/test-tmp/<子目录>/pt`，输出重定向到文件、放后台跑，跑完删子目录。Java 构建缓存在 .local/test-tmp/java-cache（共享、加锁，不要删），不用再设 SHIP156_TMP（[BUG-108]）。
- **基线**：合入并提交后 `git rev-parse --short HEAD` 就是下一批任务的基线。
- **发布新版**（[SYS-06]）：①合入并推送 main；②手动运行 GitHub 工作流 mf-build（版本号、更新说明、是否必须及原因、基线版本，build_only=false），约 5 分钟；③下载它的 unsigned 产物，本机 `.venv/Scripts/python.exe -B scripts/release/sign_and_stage.py --artifact <zip> --private-key .local/release-keys/mentorflow-app-2026.private.key --work .local/test-tmp/release-<版本>/sign --ssh-identity E:/Codex/Secrets/MentorFlow/ssh/mentorflow-e1-ops-ed25519`；④手动运行 mf-publish（版本号；build_installer 默认 false 只发更新约 1～3 分钟，要新安装程序时设 true 约 10 分钟，或事后单独跑 mf-installer）；⑤用客户端代码对公网 stable.json 验签，看 aiteacher.clannad.win/download。触发和查看运行可用 GitHub 网页，或用本机 git 凭据调 GitHub API（.local/tmp/watch_run.sh 轮询到结束）。工作流只能手动触发，额度红线见 scripts/release/README.md。
- **启动/重启用户的源代码版**：`Start-Process -FilePath D:/MentorFlow/.venv/Scripts/pythonw.exe -ArgumentList 'D:/MentorFlow/refactor/desktop/source_start.pyw' -WorkingDirectory D:/MentorFlow -WindowStyle Hidden`。重启前一定先问用户（可能在上课）。
- **查用户软件的问题**：装好的软件日志 D:\MentorFlowApp\MentorFlow\Data\logs\mentorflow.log，源代码版日志 D:\MentorFlowData\account-shell\logs\mentorflow.log（JSON 行，只读）；数据库在对应的 learning\accounts\<账号>\*.sqlite3，只读查看用 `sqlite3.connect('file:...?mode=ro', uri=True)`。
- **界面构建**：PATH 加 `/d/MentorFlow/.local/node`，`node .local/web-deps/node_modules/vite/bin/vite.js build --config refactor/learning/web/vite.config.mjs`；dist 不进仓库。
- **写脚本**：含 Windows 路径或反斜杠的内容不要用 bash heredoc 或 python -c（会被吃掉，BUG-10），写成文件再运行。
- **往服务器传源码**：`git -c core.autocrlf=false -c core.eol=lf archive --format=tar -o <tar> <提交> refactor/server refactor/server-java`，scp 上去解到 /opt/mentorflow/releases/java-<提交>/，再核对关键文件 sha256；不加这两个参数 .sh 会带 Windows 换行，跑不了（[BUG-107]）。
- **服务器**：`ssh -F "D:/MentorFlow/.local/server/ssh_config" mentorflow`（89.45.45.28，mentorflow-admin，sudo -n）。Python 后端 mentorflow-slim-backend:MF-76（已停，留作回退；切换与回滚脚本 /opt/mentorflow/state/mf76/switch.sh：无参数切换、check 只查配置、rollback 回到 MF-75；上一版 mf75 目录保留）；入口 nginx 容器 mentorflow-e1-preview-edge-1（配置 /opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf）；更新与下载目录 /opt/mentorflow/update-static/e108/app/；资料白名单 /opt/mentorflow/state/mentorflow-e1-preview/catalog/。 后端是 Java（[ARCH-09]，2026-10-16 起）：容器 mentorflow-java-backend、mentorflow-java-oidc，部署与切换、回滚、停用和恢复 Python 的脚本在 /opt/mentorflow/releases/java-539ab610/refactor/server-java/deploy（README 有步骤）；compose 需 BACKEND_ENV=/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env；Python 后端已停，镜像 mentorflow-slim-backend:MF-76 保留。 数据库容器镜像为 mentorflow-postgres-pgvector:17（[RAG-01]，2026-10-16）。它的 compose 标签指向已不存在的旧目录，以后用 compose 操作 postgres，要用后端容器标签里的文件组，加上 --profile account-core-mvp、-f /opt/mentorflow/backups/pgvector-20261016/pgvector.override.yml，最后再加 BUG-45 override。 2026-10-10 起两个 docker 网络设了 ip_range（BUG-45）：以后用 Compose 操作这些容器，命令末尾必须追加 -f /opt/mentorflow/state/bug45-20261010/network45.override.yml。
- **自动等回执（用户 2026-10-14 定）**：只挂一个后台等待：`.local/tmp/receipt_watch.sh wait`（每 30 秒查整个 .local/receipts，和 .local/tmp/receipts.seen 比，有新的或改过的回执就列出并结束），收到就直接读回执、审查合入、清理、更新文档，处理完用 `receipt_watch.sh ack <任务号>` 记为已处理，再挂一次 wait；不用等用户转告，不按任务分别挂多个。
- **排练产物（2026-10-19）**：员工的真打包排练每次留下几十 GB（160 约 70 GB、168 约 25 GB），而且员工的递归删除会被系统拦住；收到这类回执后，架构师先把任务目录里除 report.md 和截图以外的都删掉，再做别的。
- **磁盘（用户 2026-10-10 定）**：测试与临时产物在 .local/test-tmp/ 下；冒烟测试、构建、中间文件用完就自动删，不留着等以后分类。员工删除被系统拦住时，架构师合入后直接删（只留 report.md 和截图），不用再问用户。

## 当前基线

| 项目 | 值 |
| --- | --- |
| main | 以 `git rev-parse --short HEAD` 为准；2.0.27 = 8b19ae46 已推送并发布 |
| 已发布 | 2.0.27 = 8b19ae46（2026-10-19，非必须，只发软件内更新）；下载站安装程序 2.0.15；强制线 2.0.3 |
| GitHub | main 已推送（私有仓库），远端只有 main 一个分支（9-26 遗留的 15 个 Dependabot 升级请求与分支 2026-10-12 经用户同意已关闭并删除，它们的配置已随旧代码删除，不会再生成）；发版需要的推送由架构师做，其余推送要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07）转发任务；工位（library、runtime、client、platform、tools、classroom、server）在任务里指定。同一个员工、同一个工位同时只能有一个任务。任务号写成“批次-员工编号”。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 173-MF01 调研：账号间数据隔离（已起草，待转发） | server |
| MF-02 | 空闲 | client |
| MF-03 | 空闲 | classroom |
| MF-04 | 174-MF04 调研：服务器挂掉时的表现与恢复（已起草，待转发） | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 空闲 | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 169-MF06、170-MF02、171-MF03 已合入并随 2.0.28 发布（2026-10-19；合入后全量 4443 通过、0 失败）。
- 172-MF02 调研完成（[UI-02]，报告 .local/test-tmp/chat172/report.md），等用户定三个问题后派开工。
- 173-MF01（server，基线 3a43d39b）：调研账号间数据隔离（[ACC-03]），只在本地测试环境查，不碰线上，¥0。任务文件 D:\MentorFlow\.local\tasks\173-MF01.md，已起草，待转发。两份互不相干，可并行；调研回来给用户看过再派开工。
- 174-MF04（runtime，基线 99526a93）：调研服务器不可用时的表现与恢复（[SYS-08]、[BUG-118]），只查不改、不碰线上，¥0。任务文件 D:\MentorFlow\.local\tasks\174-MF04.md，已起草，待转发。和 172、173 可并行。
- 其余员工空闲（2026-10-19）。第一百五十八～一百六十八批都已合入并随 2.0.22～2.0.27 发布，过程在 git 和 CURRENT_STATE 的 BUG 条目里。
- 候选（等用户提）：用户学项目资料时的反馈 → 改 docs/learn 并重推 mentorflow-guide；发给朋友前重做安装程序并走一遍全新安装和注册（[ACC-02]）。

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
