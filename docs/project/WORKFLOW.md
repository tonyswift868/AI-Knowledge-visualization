# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读开头的「文档维护规则」和「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 开头三节和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-12）：2.0.14 已发布（非必须，含安装程序），服务器 MF-76。用户 13:36 在登录窗口点更新成功（约 43 秒，Updater 换新，清理 2.16 GB，[BUG-79]），第一轮同步拉 574 条 14 秒（[BUG-82] 每页偏慢，待查）。下一步：用户走通注册（[ACC-02]）→ 发给朋友。无在途任务，员工全部空闲。全量里 loading86、self_test81、status88 三个浏览器测试满载时偶发失败（单独跑通过），记为已知不稳定测试。

## 协作方式

- **先讨论、后派活（最优先）**：问题先和用户讨论清楚（现象、原因、牵连范围、前置依赖）再写任务；收到回执只讲审查结果、问题和要用户定的事，用户同意后再把整批任务原文放在回复最后，然后停下等转发。不出看着笃定但有漏洞的方案。
- **不懂就问**：有不懂或感觉有偏差，直接问用户。
- **确认是 bug 的直接派修（用户 2026-10-12 定）**：已经确定是 bug、原因查清的，直接写任务派出去，发现几个派几个，在回复里告诉用户；只有拿不准是不是 bug、或拿不准改了会牵连什么的，才先和用户讨论。
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
- **发版**：修好就用流水线发版，让用户在装好的软件上点更新亲测（操作手册“发布新版”）。**本机先验、GitHub 一版只跑一次（用户 2026-10-11 提出）**：先在本机用同一个打包脚本打预览版，跑一遍 scripts/release 的发版前总验收（102-MF04c 整理），过了才触发 mf-build / mf-publish；没在本机验过的版本不上 GitHub。正式版不在本机打：本机环境会把别的 DLL 混进包里（102-MF04 实测混入过 ICU/OpenSSL），而且同一版本两边打出来的文件不同，会扰乱以后的增量更新。**版本号只在真正发布时占用（用户 2026-10-12 定）**：定下要发的下一版（现在是 2.0.14），在发布前查出的 bug 修完仍发这个版本号，不跳到 2.0.15；本机排练用的测试版本号（如 9.103.14）不算。
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
- **服务器**：`ssh -F "D:/MentorFlow/.local/server/ssh_config" mentorflow`（89.45.45.28，mentorflow-admin，sudo -n）。线上后端 mentorflow-slim-backend:MF-76（2026-10-12；切换与回滚脚本 /opt/mentorflow/state/mf76/switch.sh：无参数切换、check 只查配置、rollback 回到 MF-75；上一版 mf75 目录保留）；入口 nginx 容器 mentorflow-e1-preview-edge-1（配置 /opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf）；更新与下载目录 /opt/mentorflow/update-static/e108/app/；资料白名单 /opt/mentorflow/state/mentorflow-e1-preview/catalog/。 2026-10-10 起两个 docker 网络设了 ip_range（BUG-45）：以后用 Compose 操作这些容器，命令末尾必须追加 -f /opt/mentorflow/state/bug45-20261010/network45.override.yml。
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
| MF-01 | 空闲（107-MF01 作废：服务器已由架构师直接上线） | server |
| MF-02 | 108-MF02 已起草，待用户转发（同步与清单压缩、分段计时） | client |
| MF-03 | 空闲 | classroom |
| MF-04 | 空闲 | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 空闲 | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 无在途任务。用户已更新到 2.0.14 → 用户走通注册（[ACC-02]）→ 发给朋友。
- 108-MF02（client）：[BUG-82] 真实拉取每 500 条约 11 秒，查慢在哪并给同步与更新清单加 gzip（服务器与 nginx 改动上线前问用户）。已起草，待转发。

## 第一百零八批任务原文（2026-10-12，基线 fb649358）

### [108-MF02] client｜同步每 500 条 11 秒：查原因、加压缩（[BUG-82]）

~~~text
【MentorFlow 第一百零八批｜MF-02（工位 client）｜真实同步每 500 条要 11 秒：查清慢在哪（网络、服务器还是本机写入），并给同步和更新清单的 JSON 加压缩传输（BUG-82 后续）】

现成项目与许可证（第一节）
  - 压缩用现成的：
    - Starlette 的 GZipMiddleware（BSD-3，随 FastAPI 已在服务器依赖里）；
    - 客户端所用 HTTP 库自带的 gzip 解码（先查清用的是哪个库）；
    - nginx 自带的 gzip 模块（BSD-2）。
  - 行业通行做法：文本 JSON 走 HTTP 压缩（Content-Encoding: gzip），客户端用 Accept-Encoding 声明。
  - 不引新库，不改同步协议本身。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard fb649358         # 主线：2.0.14 已发布（含你上一轮的批量同步）
提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署（服务器代码可以改、在本机测，上线由架构师征得用户同意后做）；不碰正式签名私钥；不调用真实模型，不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读：可以把 D:\MentorFlowApp\MentorFlow\Data\learning\accounts\<账号>\course.sqlite3 用 sqlite3 mode=ro 的 backup 复制一份到任务目录，用作真实形状的数据；可以只读看 D:\MentorFlowApp\MentorFlow\Data\logs\mentorflow.log。不要启动、结束或重启用户的软件。
E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。产物在 D:\MentorFlow\.local\test-tmp\sync108\，用完即删，只留 report.md；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问。花费上限 ¥0。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。同时在跑：无。

背景（CURRENT_STATE [BUG-82] [BUG-68] [ARCH-05]）：
  - 用户 2026-10-12 13:38 更新到 2.0.14 后第一轮同步（日志 PULL_PAGE_*）：第一页 500 条 11.25 秒，第二页 74 条 2.70 秒，共 574 条 14.1 秒。你上一轮在本机加 250 毫秒延迟时，2200 条只要 3.8 秒。按真实速度，新设备拉全部约 2200 条要约 50 秒。
  - 架构师查到的线索（未验证是不是全部原因）：
    - 用户上网走本机代理 127.0.0.1:7897，到服务器 89.45.45.28 的带宽不稳：同一个 2 MB 的更新清单 stable.json，一次 5.8 秒（约 370 KB/s），一次 30 秒只下了 1.96 MB（约 65 KB/s）。
    - 服务器返回没有压缩：响应头没有 Content-Encoding。
    - 用户库里 points 平均 3.2 KB、source_chunks 2.1 KB、modules 7.1 KB、point_links 8.1 KB，一页 500 条约 1.5～2 MB。
    - 更新清单 stable.json 约 2 MB，由服务器的 nginx 网关静态提供，同样没压缩，检查更新慢也可能是这个原因（[BUG-68]）。

要做
  1. 先量清楚慢在哪。
     - 用库副本把真实形状的数据导入本机 refactor/server。
     - 量一页 500 条：服务器处理用时、响应字节数、客户端解析加写库用时。
     - 再用限速模拟用户的网络（250 毫秒延迟，带宽分别 300 KB/s 和 70 KB/s），量同样一页。
     - 回执写清：这 11 秒里网络、服务器、本机各占多少。
  2. 日志：同步每页的日志再拆细，写出等待服务器、下载（字节数、秒）、写库各用多久。以后在用户机器上一看就知道是哪段慢。
  3. 加压缩：
     - 服务器同步接口的响应加 gzip（GZipMiddleware，设合适的最小大小）；
     - 客户端确认请求带 Accept-Encoding: gzip 并能正确解开。
     - 查清 2.0.13、2.0.14 的客户端是否已经自动带这个头：带的话，只上线服务器就能让已发出去的版本也变快。
     - 批量上传的请求体要不要压缩，自己判断（服务器要能收，旧客户端不受影响）。
  4. 更新清单 stable.json：
     - 查客户端拉清单时是否带 Accept-Encoding: gzip、能否解开（验签用解压后的原始字节，必须和现在逐字节一致）；
     - 写一段给架构师加到 nginx 网关的 gzip 配置（只对 application/json 和 .json，写清放在哪个 server/location、怎么检查生效、怎么撤回）。
     - 你不能连服务器，配置交架构师上。
  5. 改后在同样的限速条件下再量一遍：一页 500 条，以及新设备拉全部约 2200 条。目标（300 KB/s、250 毫秒）：一页 ≤3 秒，全部 ≤15 秒；达不到写清剩下的大头。
  6. 测试（文件名带 108，别和已有的重名）：
     - 压缩开和关两种客户端都能同步；
     - 旧客户端（10e625f4 的同步代码）对压缩后的服务器照常工作；
     - 清单压缩传输后验签照过；
     - 新日志字段在。
  7. 只改客户端同步与下载相关文件、refactor/server 的 app 装配与同步接口、tests/refactor/。要动别处的先写清原因，再自己决定，回执说明。

完成标准：
  - 第 1 条有分段数据；第 3、4、5 条做完并有改前改后计时；
  - 第 6 条测试通过；最后全量跑一次并通过。loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明。
  - 达不到就写“部分完成”或“受阻”。

回执：写到 D:\MentorFlow\.local\receipts\108-MF02.md。表格列：
  基线 ／ 提交 ／ 11 秒拆分（网络、服务器、本机）／ 压缩做法（服务器、客户端、旧客户端是否自动受益）／ nginx 配置（给架构师）／ 计时改前改后 ／ 新增测试 ／ 服务器要上线的改动清单 ／ 全量 ／ 本地最终占用。
给用户只回一行“108-MF02 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\sync108\report.md。
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
