# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读开头的「文档维护规则」和「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 开头三节和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-11）：线上 2.0.12。最要紧的是把安装与更新整条流程在真打包程序上走通（还没给外部用户发过任何版本，就卡在这里）：102-MF05b（登录窗口不闪）已合入；102-MF04（软件内更新自己退出、下次启动安装、目录清理）代码已合入但真打包没跑完，102-MF04b 补做，102-MF05c 并行实测真安装程序；两条都过才发 2.0.13。全量里 loading86、self_test81、status88 三个浏览器测试满载时偶发失败（单独跑通过），记为已知不稳定测试。101 合入后全量 3445 通过、status88 偶发 1 项。

## 协作方式

- **先讨论、后派活（最优先）**：问题先和用户讨论清楚（现象、原因、牵连范围、前置依赖）再写任务；收到回执只讲审查结果、问题和要用户定的事，用户同意后再把整批任务原文放在回复最后，然后停下等转发。不出看着笃定但有漏洞的方案。
- **不懂就问**：有不懂或感觉有偏差，直接问用户。
- **一批派完、并行优先**：能并行、互不冲突的一次派完，人数不设上限；改同一文件同一部分的排先后；有依赖的写明先后与原因。
- **不重复造轮子（硬规定）**：每份任务第一节写“用的现成项目与许可证”，参照行业标准；要自己写的写明为什么没有现成的。
- **方案由架构师出**：架构师查证据、读相关代码、找开源做法，在任务里写清“怎么做”；员工照做。
- **派任务前的检查**：每条完成标准都是这个员工凭自己的权限能做到的（取决于外部的写成“查清后二选一”）；互相依赖的工作不拆成并行任务各自要求全量通过；写之前读相关代码、查环境。
- **员工实测不占用户的鼠标键盘（2026-10-11）**：真打包实测用 Windows 的 UI Automation（InvokePattern）点按钮、读文字，不用 computer-use、模拟输入或抢前台（102-MF04 用电脑界面操作时被用户按 Esc 中止）；测试窗口会出现在屏幕上，派任务时先告诉用户。
- **安装与更新先自己走通再叫用户测（用户 2026-10-11 定）**：凡是改到安装、更新、启动器、Updater 的，任务里必须要求用真打包程序走一遍完整流程（旧版 → 新版：软件内更新的两个入口、下次启动安装、安装程序覆盖升级，按改动取用），架构师看过证据才发版；没走通不叫用户测。替身测试不算（[BUG-71]）。
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
| 已发布 | 2.0.12 = 5d7c211e（非必须；启动提速、BUG-59；下载站安装程序 2.0.12）；强制线 2.0.3。main 已含未发布的 102-MF04（未经真打包验证），102-MF04b 通过前不发版 |
| GitHub | main 已推送（私有仓库）；发版需要的推送由架构师做，其余推送要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07）转发任务；工位（library、runtime、client、platform、tools、classroom、server）在任务里指定。同一个员工、同一个工位同时只能有一个任务。任务号写成“批次-员工编号”。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 空闲 | server |
| MF-02 | 空闲 | client |
| MF-03 | 空闲 | classroom |
| MF-04 | 102-MF04b 真打包走通软件内更新（已起草，待转发） | runtime |
| MF-05 | 102-MF05c 真安装程序全新安装与覆盖（已起草，待转发） | platform |
| MF-06 | 空闲 | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 102-MF04b（runtime）：102-MF04 代码已合入（13032a9f），真打包的软件内更新、退不掉、下次启动、用户那台的升级路径没跑完，补做（[BUG-71] [BUG-73]）。已起草，待转发。
- 102-MF05c（platform）：真安装程序（预览版通道）全新安装与覆盖升级实测，可与 102-MF04b 并行（只改 packaging/ 与打包脚本，delivery 的问题交架构师）。已起草，待转发。
- 两条都过 → 发 2.0.13（含安装程序，基线版本 2.0.9 2.0.11 2.0.12）→ 验证公网清单与老客户端能下载 → 用户用安装程序覆盖一次 → 再发给外部用户。

## 第一百零二批补做任务原文（2026-10-11，基线 26cff723）

### [102-MF04b] runtime｜用真打包程序把软件内更新完整走通（¥0）

~~~text
【MentorFlow 第一百零二批补做｜MF-04（工位 runtime）｜用真打包程序把软件内更新完整走通：登录窗口和主界面两个入口、退不掉转下次启动、下次启动安装、用户那台的升级路径；全程不占用用户的鼠标键盘】

现成项目与许可证（第一节）
  - 操作界面用 Windows 自带的 UI Automation（System.Windows.Automation 或 UIAutomationCore，系统组件）：按钮用 InvokePattern 点，文字读 Name。Qt 6 在 Windows 上自带 UIA 支持，Inno 向导是标准 Win32 窗口。想用 pywinauto（BSD-3）就在任务目录里建独立 venv 装，不装进 D:\MentorFlow\.venv。
  - 更新流程沿用你 102-MF04 实现的 Squirrel/Velopack ApplyUpdatesAndRestart 做法。本轮只做真打包验证和修 bug，不重新设计。
  - 打包、测试签名、本机离线更新源沿用你上一轮的办法。测试签名密钥自己生成，用完删。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard 26cff723         # 主线：已合入你的 d3a811e1 和 102-MF05b 的登录窗口改动
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥。D:\MentorFlowApp、D:\MentorFlowData\account-shell 只读（需要的状态文件复制到任务目录再用）；E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。遇到选择自己决定，回执写明理由，不停下来问。不要结束或重启用户正在用的软件进程。
不占用户的鼠标键盘（硬规定）：不用 computer-use，不模拟鼠标或键盘，不靠抢前台去操作窗口。上一轮用户按 Esc 中止，就是因为屏幕被占了。测试窗口出现在屏幕上可以；不能关或操作用户自己的任何窗口。
测试程序的 LOCALAPPDATA、TEMP、TMP 一律指到任务目录，不能再落到 C 盘（上一轮误落了 1 GB）。删除被拦住就列出路径交架构师，不换工具绕过。
测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次，同一套测试不重复跑。冻结构建、安装副本、下载源用完立刻自己删，只留 report.md 和截图。
同时在跑：102-MF05c（platform）：用真安装程序做全新安装和覆盖升级，只改 refactor/desktop/packaging/ 和 scripts/build_learning_installer.py。它发现 refactor/desktop/delivery/ 里的问题会经架构师转给你，不改你的文件。两边都会冻结构建、弹测试窗口，互不影响。
产物目录：D:\MentorFlow\.local\test-tmp\inapp102b\。目标三小时内交回。花费上限 ¥0。
背景（CURRENT_STATE [BUG-71] [BUG-73]，「当前阶段」里“发给外部新用户”的放行标准）：你的 102-MF04 代码已合入主线（13032a9f），与 102-MF05b 合并后全量 3463 通过。但真打包只跑了两样：基线上复现老错误，和用官方 2.0.12 恢复用户目录副本。改后的软件内更新、退不掉、下次启动安装都没跑。2.0.13 要等这些全过才发。发出后用户用 2.0.13 安装程序覆盖一次，以后所有更新都走这条路，然后才发给第一个外部用户。到今天还没给他发过任何版本，就卡在这里。

要做
  全部用基线代码冻结的真打包程序，做两个测试版本 X → Y（测试签名、本机离线更新源）。安装用安装程序调用的同一个安装核心（UserRuntime install）。启动一律走桌面图标实际指向的启动器，不直接起 MentorFlow.exe。启动器的 LOCALAPPDATA 和 App 运行时的 .MentorFlow-runtime 要像真实环境一样不同。
  1. 登录窗口入口：X 启动后弹更新框 → UIA 点“立即更新并重启” → App 自己退出 → 换文件 → Y 的登录窗口出来。检查：版本是 Y；per-user-transaction 为 HEALTHY；pending 已消失；Updater 是 Y；新窗口在最前面，不是最小化。记录分段时间：点击 → 下载完 → App 退出 → 新窗口可见。记录 App 实际自己退出用了几秒，看 30 秒上限合不合适。
  2. 主界面入口：登录进主界面后点更新，检查同第 1 条。
     登录：优先用本机账号服务。MENTORFLOW_ENTERPRISE_API_BASE_URL 指向 localhost 上的替身或本机起的 refactor/server。不碰正式服务器，不注册真实账号。打包版没法指向本机服务的话，只把账号层换成测试注入，更新链路保持真实，回执写明。
     更新前在主界面留一点能核对的学习数据（例如建一个计划或改一项设置），更新后核对还在。重启后按设计应该进主界面还是登录窗口，如实记录。
  3. 中间不能空白：App 退出到新窗口出来之间，屏幕上要有东西告诉用户在更新。用 101 的原生启动窗（startup_window），显示“正在更新 MentorFlow…”，新窗口出来后消失。这段时间里用户再点桌面图标：不能装坏，不能开出两个。结果写进报告。
  4. 退不掉：做一个只在测试构建里生效的故障开关，让 X 的保存或退出卡住超过 30 秒。之后应当：
     - 旧 App 留着；
     - 更新框显示中文“更新已下载好，关闭软件后下次打开时自动安装。”和“关闭软件”按钮；
     - UIA 点“关闭软件” → App 退出 → 从桌面图标启动 → 启动器装上 Y → Y 的窗口出来。
     全程界面上不能出现英文原因。
  5. 下次启动安装（用户机器上失败过的那条）：App 下载好 Y 后正常关掉，再从桌面图标启动 → 装上 Y。第 4 条已经覆盖的话写明即可。
  6. 用户那台的路径：
     - 照 D:\MentorFlowApp\MentorFlow 的状态做一个副本：App、Updater、各 state 文件、指向旧版本的 pending-update.json、ROLLED_BACK 的 per-user-transaction.json、几个只放少量文件的 staging-/retired- 目录，不用复制 11 GB；
     - 用 X 的安装核心覆盖 → HEALTHY，过期 pending 作废；
     - 从桌面图标启动两次 → staging、retired 和旧缓存被清掉，Data 逐字节不变；
     - 接着在这个副本上做第 1 条（X → Y 软件内更新）。
  7. 发现问题当场修，只改 refactor/desktop/delivery/、updates.py、update_dialog.py、startup_window.py 和相关测试。每个问题写进报告：现象、数据、原因、修法。改完重新冻结，把受影响的场景重跑到通过。
完成标准：用修完后的最终代码，1～6 全部在真打包程序上跑通，每条都有日志片段、时间和截图；最后全量跑一次并通过。loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明。达不到就写“部分完成”，不能用离线测试或替身代替。
回执：写到 D:\MentorFlow\.local\receipts\102-MF04b.md。表格列：基线 ／ 提交 ／ 场景 1～6 结果与分段耗时 ／ App 自己退出实际秒数 ／ 新发现的问题与修法 ／ 截图 ／ 全量 ／ 本地最终占用（含 C 盘有无残留）。给用户只回一行“102-MF04b 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\inapp102b\report.md。
~~~

### [102-MF05c] platform｜用真安装程序走一遍全新安装和覆盖升级（¥0）

~~~text
【MentorFlow 第一百零二批补做｜MF-05（工位 platform）｜用真安装程序（预览版通道）在这台电脑上走一遍：全新安装（第一个外部用户的路径）和覆盖升级（用户那台的路径）；全程不占用用户的鼠标键盘】

现成项目与许可证（第一节）
  - 用项目现有的 Inno Setup 7 脚本（refactor/desktop/packaging/inno/）和 scripts/build_learning_installer.py。
  - 预览版通道：APP_ID 含“.UAT.”时，安装程序用另一个 AppId，快捷方式叫“MentorFlow 预览版”，不碰用户装的正式版。测试签名密钥自己生成，用完删。
  - 操作界面用 Windows 自带的 UI Automation（System.Windows.Automation 或 UIAutomationCore）：按钮用 InvokePattern 点，文字读 Name。Inno 向导是标准 Win32 窗口，Qt 6 自带 UIA 支持。想用 pywinauto（BSD-3）就在任务目录里建独立 venv，不装进 .venv。

开工同步
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard 26cff723
提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥。D:\MentorFlowApp、D:\MentorFlowData\account-shell 只读；E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。遇到选择自己决定，回执写明理由，不停下来问。不要结束或重启用户正在用的软件进程。
不占用户的鼠标键盘（硬规定）：不用 computer-use，不模拟鼠标或键盘，不靠抢前台去操作窗口。测试窗口出现在屏幕上可以；不能关或操作用户自己的任何窗口。
不碰正式版：不运行正式通道（正式 APP_ID）的安装程序；不改用户的 MentorFlow 快捷方式、开始菜单和注册表项；不读写 Windows 凭据里正式版的条目。开工前和结束后各记一份对比：HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall 下与 MentorFlow 相关的项、桌面和开始菜单里的 MentorFlow 快捷方式，以及这次在 C 盘新出现的目录。结束时预览版的东西全部清干净，正式版的一项没变。
测试要求：没改代码就不跑全量；改了代码，工作中只跑相关测试，最后全量一次。冻结构建、安装包、安装目录用完立刻自己删，只留 report.md 和截图。删除被拦住就列出路径交架构师，不换工具绕过。
同时在跑：102-MF04b（runtime）：真打包软件内更新，会改 refactor/desktop/delivery/、updates.py、update_dialog.py、startup_window.py。你发现这些文件里的问题，写清现象、日志、复现步骤交架构师，不要自己改。Inno 脚本和打包脚本里的问题你自己修。
产物目录：D:\MentorFlow\.local\test-tmp\setup102c\。目标两个半小时内交回。花费上限 ¥0。
背景（CURRENT_STATE「当前阶段」里“发给外部新用户”的放行标准，[SYS-06] [BUG-59] [BUG-71] [BUG-72]）：到今天还没给第一个外部用户发过任何版本，因为安装与更新没走通。安装程序整条路上一次真跑，是用户自己用 2.0.9 原地升级，当时出了 BUG-59（假报失败）。101-MF04 修了，但只用安装核心离线验证过。101、102 又改了启动、安装核心和登录窗口，所以新安装程序从向导到登录窗口这整条路还没人真跑过。你的登录窗口改动（102-MF05b）已合入基线。
要做
  1. 用代码编两个预览版安装程序：
     - 旧版 O：用 2.0.12 发布时的代码 5d7c211e；编不出来就用本批之前任何能编的提交，写明是哪个；
     - 新版 N：用基线代码。
  2. 全新安装（朋友的路径）：
     - 安装位置指定到任务目录；
     - 用 UIA 走完向导的每一页，包括选位置，以及完成页的“创建桌面快捷方式”和“立即启动”；
     - 全程不报错；预览版桌面图标存在，指向正确；
     - 登录窗口出来、在最前面、不闪（mentorflow.log 里 LOGIN_MAIN 只 SHOWN 一次）；记录从点“完成”到登录窗口可见的时间；
     - 打开注册页能输入；
     - 关掉后从桌面图标再启动一次，记录时间。
  3. 覆盖升级（用户那台的路径）：
     - 先用 O 装好并启动一次；
     - 参照 D:\MentorFlowApp\MentorFlow 的只读状态，把这个安装目录做成像用户那台的样子：指向旧版本的 pending-update.json、ROLLED_BACK 的 per-user-transaction.json、几个 staging-/retired- 目录；在 Data 里留几份文件；
     - 运行 N 的安装程序覆盖。不能假报“安装未完成”（BUG-59）；结果应为版本 N、HEALTHY、Updater 已换、桌面图标还在；
     - 登录窗口出来；
     - 两次启动后垃圾目录被清掉，Data 逐字节不变。
  4. O 的软件正开着时运行 N 的安装程序：看提示是不是中文、用户能不能关掉软件后继续装。如实记录；不要求修，除非是 Inno 脚本里的明显问题。
  5. 卸载：用预览版的卸载程序卸掉。看 Inno 脚本的设计，如实记录 Data 是保留还是删除。核对开头的对比：预览版的快捷方式、注册表项、C 盘目录清干净，正式版的没变。
  6. 问题：Inno 脚本和打包脚本里的当场修，修完把受影响的场景重跑到通过；delivery 等文件里的交架构师（见上）。
完成标准：第 2、3 条在真安装程序上通过，截图包括向导每一页、桌面图标、登录窗口；第 5 条清干净、正式版不变。改了代码的话，最后全量一次通过：loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明。达不到就写“部分完成”。
回执：写到 D:\MentorFlow\.local\receipts\102-MF05c.md。表格列：基线 ／ 提交（没改代码写“无”） ／ 全新安装结果与耗时 ／ 覆盖升级结果 ／ 开着软件时安装 ／ 卸载与清理（前后对比） ／ 交给架构师的问题 ／ 截图 ／ 全量 ／ 本地最终占用。给用户只回一行“102-MF05c 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\setup102c\report.md。
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
