# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读开头的「文档维护规则」和「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 开头三节和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-10）：线上 2.0.9（直接考、资料推荐、缓存改法①～④、问答题自己判与错题集）。BUG-45 已根治上线。第九十八、九十九批已随 2.0.9 发布（合并后全量 3386 通过；新流程发布 46 秒）。全量里 loading86、self_test81 两个浏览器测试满载时偶发失败（单独跑通过），记为已知不稳定测试。

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
| 已发布 | 2.0.9 = 4279276b（非必须；下载站安装程序也是 2.0.9）；强制线 2.0.3 |
| GitHub | main 已推送（私有仓库）；发版需要的推送由架构师做，其余推送要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07）转发任务；工位（library、runtime、client、platform、tools、classroom、server）在任务里指定。同一个员工、同一个工位同时只能有一个任务。任务号写成“批次-员工编号”。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 空闲 | server |
| MF-02 | 空闲 | client |
| MF-03 | 空闲 | classroom |
| MF-04 | 100-MF04（已起草，待转发） | runtime |
| MF-05 | 100-MF05（已起草，待转发） | platform |
| MF-06 | 空闲 | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 第一百批 2 个任务：已起草（2026-10-10），待用户转发：100-MF04 检查更新提速（走域名、gzip、指针文件）、100-MF05 标题栏按钮效果与点 × 没反应。

## 第一百批任务原文（2026-10-10，基线 099451de）

### [100-MF04] runtime｜检查更新提速：走域名、清单压缩、先看小指针文件（¥0）

~~~text
【MentorFlow 第一百批｜MF-04（工位 runtime）｜检查更新提速：更新地址改走 aiteacher.clannad.win（IP 直连作备用）；版本清单压缩传输；先下几百字节的“最新版本指针”，有新版才下完整清单】

现成项目与许可证（第一节）
  - 做法参照成熟更新框架：Squirrel/Velopack（MIT）先读很小的 RELEASES / releases.json 判断有没有新版，再下大文件；HTTP 内容压缩用标准 Content-Encoding: gzip（httpx 自带解压）。不新增依赖。
  - 改 refactor/desktop/delivery/（update_service.py、update_download.py、update_protocol.py 中读清单的部分）、scripts/release/（build_release.py 的 MF_FEED 默认值、server_ops.py 发布时写指针文件、README.md）。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard 099451de
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥。D:\MentorFlowApp、D:\MentorFlowData\account-shell 只读；E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。可以只读访问公网下载站做测速。遇到选择自己决定，回执写明理由，不停下来问。不要结束或重启用户正在用的软件进程。
测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次，同一套测试不重复跑。中间文件用完立刻自己删，只留 report.md。
同时在跑：100-MF05（登录窗口标题栏按钮，refactor/desktop/login_shell.py 一带），不碰你的文件。
产物目录：D:\MentorFlow\.local\test-tmp\feed100\。目标两小时内交回。花费上限 ¥0。
背景（用户 2026-10-10 实测，CURRENT_STATE [SYS-06]）：每次检查更新都要从 https://89.45.45.28/mentorflow/updates/app/stable/stable.json 下载约 2.1 MB 的签名清单，用户线路 0.15～0.6 MB/s，一次检查 9～61 秒，有时等三分钟还没出结果。架构师已在服务器上加好新地址（2026-10-10）：https://aiteacher.clannad.win/updates/app/ 下与旧地址同样的文件（stable/stable.json、objects/<sha256>、releases/），经 Cloudflare，清单支持 gzip（约 2.1 MB → 0.46 MB）。服务器出口带宽本身也慢，所以光换地址不够，要让“检查”本身只下很少的字节。
要做
  1. 更新地址：新打包的版本默认用 https://aiteacher.clannad.win/updates/app/（清单、文件、指针都走这里），连不上或证书/内容不对时自动退回旧的 IP 地址（https://89.45.45.28/mentorflow/updates/app/），回执写清退回的条件。域名证书用系统信任根（Cloudflare 的公开证书）；IP 地址照旧用现有的固定证书。签名校验一项不减。
  2. 清单压缩：下载 stable.json 时接受 gzip，解压后的原始字节照旧验签；下载更新文件（objects）仍按现有方式（identity、按字节续传、逐个 SHA256 核对）。
  3. 小指针文件：发布时在 stable/ 下多写一个 latest.json（几百字节：版本号、metadata_version、stable.json 的 SHA256 和大小），由 server_ops.py 发布 stable.json 的同一步原子写入（mf-publish 两种模式都要写）。客户端检查更新时先下 latest.json：和本机已缓存的、已验签的清单对比，版本和指纹都没变就直接判“已是最新”，不下完整清单；有变化才下 stable.json 并照旧验签，验签后再核对指纹和指针一致。指针本身不签名，只用来决定要不要下清单，任何最终判断都以验签后的清单为准；指针拿不到时退回直接下清单。
  4. 旧版本兼容：老客户端还会读旧 IP 地址和 stable.json，不受影响；服务器两条地址都保留。
  6. （补充，用户 2026-10-10）从任何老版本更新都只下缺的文件：逐个比对 SHA256，本机现有 App 里相同的文件核对后本地复制，只下载缺的（2.0.5→2.0.9 应约 24.5 MiB，不是 1025.97 MiB）；本机文件指纹不符时改为下载。
  5. 测试：离线覆盖——域名成功、域名失败退回 IP、gzip 清单验签、指针相同不下清单、指针变化下清单、指针与清单不一致时以清单为准并记录、指针缺失退回；mf-publish 本机模拟两种模式都写出 latest.json。实测（只读）：从本机对新地址测“指针”和“压缩清单”各 3 次耗时，写进报告。
完成标准：上面测试通过；最后全量一次通过；提交在工位。
回执：写到 D:\MentorFlow\.local\receipts\100-MF04.md（表格：基线 ／ 提交 ／ 地址与退回条件 ／ 压缩 ／ 指针文件 ／ 实测耗时 ／ 全量 ／ 本地最终占用）；给用户只回一行“100-MF04 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\feed100\report.md。
~~~

### [100-MF05] platform｜登录窗口标题栏按钮：鼠标悬停效果、点了立刻有反应（¥0）

~~~text
【MentorFlow 第一百批｜MF-05（工位 platform）｜登录窗口（及同样无边框的账号窗口）右上角“最小化 / 关闭”按钮：要有正常软件那样的悬停和按下效果；点关闭要立刻有反应】

现成项目与许可证（第一节）
  - 不新增依赖，只改本项目现有代码：refactor/desktop/login_shell.py（无边框自绘标题栏）及同一套无边框窗口用到的样式（dialog_theme.py 等）。效果参照 Windows 11 原生标题栏：悬停时按钮背景变浅灰（关闭按钮变红底白叉），按下时再深一点，有约 100 毫秒的过渡；键盘焦点可见。

开工同步
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard 099451de
提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥。D:\MentorFlowApp、D:\MentorFlowData\account-shell 只读；E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。遇到选择自己决定，回执写明理由，不停下来问。不要结束或重启用户正在用的软件进程。
测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次，同一套测试不重复跑。中间文件用完立刻自己删，只留 report.md 和截图。
同时在跑：100-MF04（更新检查，refactor/desktop/delivery/ 与 scripts/release/），不碰你的文件。
产物目录：D:\MentorFlow\.local\test-tmp\titlebar100\。目标一个半小时内交回。花费上限 ¥0。
背景（用户 2026-10-10 在 2.0.5 上实测）：登录窗口右上角的“—”和“×”鼠标移上去没有任何变化，“没有操作感、没有掌控感”；有一次点“×”没反应，要点下面的按钮才能关，后来又有反应了。
要做
  1. 两个按钮加悬停、按下效果（见上），两套主题（樱花、极光夜）都要协调；开了“减少动态效果”时只保留颜色变化、去掉过渡动画。
  2. 查清“点 × 没反应”的原因：例如启动或登录过程中窗口被设成不可用、遮罩/加载层盖住了标题栏、点击区域太小、或正在后台检查更新时关闭被延后。修成任何时候点 × 都立刻有反应（正在写文件等关键步骤不能关时，也要立刻显示“正在处理，请稍候”，而不是没反应）。
  3. 同一套无边框标题栏在别的账号窗口（注册/找回、账号设置、注销等）也用到的话一并改。
  4. 测试与截图：悬停、按下、关闭在启动中/登录中/空闲三种时机都能立刻响应（离线测试 + 本机实测）；截图亮暗各一套（普通、悬停、关闭按钮悬停）。
完成标准：上面测试通过；最后全量一次通过；提交在工位。
回执：写到 D:\MentorFlow\.local\receipts\100-MF05.md（表格：基线 ／ 提交 ／ 悬停与按下效果 ／ 点 × 没反应的原因与修法 ／ 涉及的窗口 ／ 测试 ／ 截图 ／ 本地最终占用）；给用户只回一行“100-MF05 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\titlebar100\report.md。
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
