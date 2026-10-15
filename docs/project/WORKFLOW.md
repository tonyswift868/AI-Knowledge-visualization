# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读「当前阶段」），这里只写编号引用。规则只在 [AGENTS.md](../../AGENTS.md)，这里只写固定做法。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 的「当前阶段」和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-15）：用户在用 2.0.15。下一版 2.0.16 范围用户已确认：134-MF04（模型异常处理，[BUG-99]）+ 135-MF02（同步 413 拆批、去 20 秒时限，[BUG-97]），只放这两项，用软件内更新发（不让用户覆盖安装）。两份任务已起草，待转发。合入后：一次全量 → 本机用真打包程序从 2.0.15 软件内更新到预览版走一遍（规则 11）→ 用户同意后发 2.0.16（基线 2.0.15，不带安装程序也可，按需）。教训（2026-10-14）：发版前把“这一版包含什么、不包含什么”列给用户确认再推送。

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
| MF-02 | 135-MF02（同步 413 拆批、去 20 秒时限，[BUG-97]）已起草，待转发 | client |
| MF-03 | 空闲 | classroom |
| MF-04 | 134-MF04（模型异常处理，[BUG-99]）已起草，待转发 | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 空闲 | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 第一百三十～一百三十三批全部已合入，随 2.0.15 发布（2026-10-14；主线 d0ead842）。
- 2.0.16（用户 2026-10-15 确认范围）：134-MF04 与 135-MF02 可并行（不改同一处）。两份合入后跑一次全量，再派发版前验收（真打包程序 2.0.15 → 预览版软件内更新），过了问用户再发。原文如下。

### [134-MF04] runtime｜模型异常处理

~~~text
【MentorFlow 第一百三十四批｜MF-04（工位 runtime）｜模型异常处理：DeepSeek 接了请求却不出字，30 秒就判“暂不可用”；各类异常给用户看得懂的话和醒目的红色提示；右上角加模型状态（BUG-99）】

现成项目与许可证（第一节）
  - 不引新库。流式读取用 httpx（BSD-3）；图标用 lucide-react（ISC，项目已在用）的 TriangleAlert 和 X；界面沿用 web/src/ui 的 Toasts、loading/Loading.tsx，以及 App.tsx 右上角的 chip。
  - 协议以官方文档为准：
    · DeepSeek 错误码 https://api-docs.deepseek.com/zh-cn/quick_start/error_codes（400/401/402/422/429/500/503）。
    · DeepSeek 请求保活 https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit：排队期间，流式请求只回 “: keep-alive” 注释，非流式请求回空行；10 分钟还没开始推理才断开；并发超限回 429。
    · Anthropic Messages 流式事件：message_start、ping，以及 error 事件里的 overloaded_error。
    · OpenAI 兼容协议流式返回的 data 行。
    回执写明查了哪几页。
  - 本项目已有：connections/providers/_http.py 的厂商错误分类（已能识别 402 余额不足）、connections/transport.py 的 http_error、gateway.py 的重试循环和 model-failures.log、model-usage.sqlite3。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard c1142b0a
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读（要用的先复制到产物目录）；不要启动、结束或重启用户的软件和源代码版。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。
产物放在 D:\MentorFlow\.local\test-tmp\fault134\，用完即删，只留 report.md 和截图。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（跑浏览器测试前把 D:\MentorFlow\.local\node 加进 PATH）。
花费：真实模型只用于第 8 条（量正常情况下第一个事件要几秒），上限 ¥2。用管理员的 DeepSeek 预设，Key 只经现有代码读取。其余全部用本机假服务器离线测。
同时在跑：MF-02 在改同步传输（135-MF02，sync_transport.py、sync_course.py）。你不碰同步代码。

背景（CURRENT_STATE [BUG-99]）
  - 用户 2026-10-15 12:54 在装好的 2.0.15 课堂里点“懂了，继续”，界面一直是“老师正在想…”，然后只显示“比平时慢，可能是网络问题”；120 秒后左上角才出一行小字“模型请求超时，请稍后重试”。设置页的“验证模型连接”同样一直在转。
  - 架构师查明的原因：
    · DeepSeek 1.3 秒就回了 HTTP 200，之后只每 12 秒发一行 “: keep-alive”，没有 message_start，一个字也没有（服务端在排队）。
    · keep-alive 每到一次就重置 httpx 的读超时，所以读超时永远不触发，只能等预设的总时限 120 秒（gateway.py 的 config['timeout']）。
    · 等待时的提示写死成“可能是网络问题”，不区分“连不上”和“连上了但厂商不出字”。
  - 用户 2026-10-15 定：
    ① 请求送达后 30 秒内连第一个真正的事件都没收到，就判定模型暂不可用（用户担心 15 秒会误判）。
    ② 各类异常都给用户看得懂的话，不显示原始报错，按下表。
    ③ 右上角“账号已连接”旁边加模型状态：出问题时变红，下一次请求成功自动恢复。
    ④ 出异常时，原位置照常提示，同时弹出醒目提示：红色底，左边是三角形感叹号警告图标，右边是 × 关闭。用户没看到小字、又点了一次还是失败时，要再弹出来。
    ⑤ 修好后发 2.0.16，用软件内更新发。
  - 用户认可的提示表：

    | 情况 | 怎么判断 | 用户看到的话 | 按钮 |
    |---|---|---|---|
    | 模型繁忙或卡住 | 送达后 30 秒没有第一个真正事件；或 500/503；或流里的 overloaded_error | 模型暂时无法使用（DeepSeek 服务繁忙），请稍后再试 | 重试 |
    | 余额不足 | 402，以及 _http.py 已识别的各厂商余额码 | DeepSeek 余额不足，充值后点重试 | 去充值（打开厂商充值页）、重试 |
    | Key 不对 | 401/403 | API Key 无效，请到设置里检查 | 去设置 |
    | 请求太频繁 | 429 | 请求太频繁，请稍等再试 | 重试 |
    | 连不上 | 连接失败、DNS 失败、连接超时 | 连不上模型服务，请检查网络 | 重试 |
    | 回复写到一半断了 | 已经出字之后断开或超时 | 回复中断了，可以重试 | 重试 |
    | 软件自己的错 | 400/422、协议校验不过、内置重试后仍被截断等 | 软件出了点问题，没拿到回复（不是网络，也不是模型服务的问题），已自动记录。请点重试 | 重试；同一处连续第二次失败时改说“还是不行，请把这个情况告诉开发者。” |

  - 表里的“DeepSeek”按当前预设的厂商名显示，换了厂商就显示对应的名字。

要做
  1. 首字时限（后端）。
     · 适用范围：所有模型请求，流式和非流式都算，包括课堂老师、后台任务、设置页验证。
     · 判定：从请求发出起 30 秒内没收到第一个真正的事件，就主动断开，报“模型繁忙或卡住”。
     · 什么算真正的事件：message_start 或任何内容事件算；keep-alive 注释、空行、Anthropic 的 ping 都不算。
     · 收到第一个真正事件后，这条限制就不再管，后面照现有总时限走。这是 [BUG-90] 的教训：时限不能丢掉已经在出的好结果。
     · 课堂和设置页遇到这种失败不自动重试（排队时重发只会更慢），直接交给界面。后台任务按它现有的重试规则处理这一类，回执写明。
  2. 统一分类。
     · 把模型错误归到上表 7 类，每个错误带上类别、给用户的话和按钮。
     · 原始报错只写进 model-failures.log，脱敏照现有做法。
     · 改掉 transport.py 里 429 显示“请求过于频繁或模型余额不足”这种把两件事混在一起的说法。
     · 内置的自动重试照旧先跑，包括 gateway 的连接重试、大纲截断后翻倍重试、老师回复格式不对时打回重写；都用完了才报给用户。
  3. 右上角模型状态。
     · 在“账号已连接”旁边加一个模型状态 chip。
     · 正常时显示绿点“模型正常”。还没发过请求时用中性样式，文字自定，回执写理由。
     · 最近一次请求属于繁忙、余额不足、Key 不对、连不上这几类时，chip 变红并显示对应短语，如“模型暂不可用”“模型余额不足”。
     · 任何一次请求成功就恢复正常。
     · 后台预取和后台任务的失败也要更新它，这样用户在点按钮之前就能看到。
     · 点这个 chip 能看到完整的提示和按钮，做法自定。
  4. 醒目提示。
     · 任一页面的模型请求失败时，原来位置的提示照常显示，同时弹出醒目提示。
     · 样式：红色底（亮、暗两套主题下都清楚），左侧是三角形感叹号图标（lucide TriangleAlert），中间是上表的话和按钮，右侧是 × 关闭。
     · 不自动消失；用户关掉、或下一次请求成功时才消失。
     · 同类错误再发生（用户又点了一次还是失败）要再弹出；但不要叠成一堆，同一时间只保留最新的一条。
     · 在 ui 的 Toasts 上扩展，不另写一套。
  5. 等待中的提示：Loading.tsx 里写死的“比平时慢，可能是网络问题。”改成不下结论的说法（例如“比平时慢，正在等模型回复…”），不再暗示是用户的网络问题。
  6. 课堂和预取。
     · 点“懂了，继续”时，如果预取的请求还在等，就接着等它，30 秒从它发出时算起。
     · 如果预取已经失败，就当场重新发一次，同样按第 1 条的 30 秒。
     · 失败时，课堂里“老师正在想…”那张卡片换成上表的话和按钮。
     · 重试只重发这一条，不重复记时长，不丢已有的消息。
  7. 设置页“验证模型连接”：同样按第 1 条判定，结果用上表的话显示。
  8. 量数据（只报告，不改时限）。
     · 每次请求把“发出到第一个真正事件”的毫秒数记进 model-usage.sqlite3 对应记录的 usage_json，不改表结构。
     · 用真实 DeepSeek 发 10 次小请求，5 次思考模式、5 次不思考，报告第一个事件的用时分布。
     · DeepSeek 如果还在排队，如实写。
  9. 其他用到模型的页面（新建计划、计划详情、资料、随口聊聊、语音纠正等）显示模型错误时，都用同一份分类和提示。回执列出改了哪些地方。
  10. 测试（文件名带 134）。
     · 本机起一个假模型服务器（127.0.0.1），模拟 Anthropic 和 OpenAI 两种协议。
     · 要覆盖的情况：只回 keep-alive；非流式只回空行；ping 之后没有内容；402；401；403；429；500；503；流里出现 overloaded_error；连不上；出字之后断开；400；422；协议校验不过。
     · 要断言：类别、给用户的话、按钮、模型状态；卡住的请求不自动重试；第 29 秒才出第一个事件的请求能正常完成。
     · 时限做成可注入的参数，测试里用很短的时限，不要真等 30 秒。
     · 真实 React 浏览器测试：醒目提示（两套主题、窄窗口）；右上角状态变红再恢复；课堂卡片换成提示；连续两次失败会再弹出。截图放进报告。
     · 相关测试和最后一次全量都要通过。
  11. 改动范围：只改 connections/；app/ 里显示和转发模型错误的地方；web/src 里和模型错误显示有关的部分（App.tsx 的 chip、ui 的 Toasts、Loading.tsx、Classroom.tsx 等）；tests/refactor/。不碰同步代码，不碰安装和更新代码。

完成标准
  · DeepSeek 不出字时，30 秒内给出“模型暂时无法使用”，不再干等 120 秒。
  · 上表 7 类都给出对的话和按钮。
  · 右上角模型状态和红色醒目提示按上面的要求工作。
  · 已经在出字的回复不会被首字时限丢掉。
  · 相关测试与全量通过。
  · 花费不超过 ¥2。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fault134\report.md（含截图）。
回执：写到 D:\MentorFlow\.local\receipts\134-MF04.md（格式见 WORKFLOW「回执格式」），给用户只回一行“134-MF04 完成/部分完成/受阻，回执已写入”。回执项目：基线 ／ 提交 ／ 查的文档 ／ 首字时限做法（流式、非流式） ／ 7 类的判断与提示 ／ 右上角状态 ／ 醒目提示 ／ 课堂与预取 ／ 设置页验证 ／ 其他页面改了哪些 ／ 首个事件用时实测 ／ 相关测试与全量 ／ 花费 ／ 本地最终占用
~~~

### [135-MF02] client｜同步 413 拆批、去 20 秒时限

~~~text
【MentorFlow 第一百三十五批｜MF-02（工位 client）｜同步：批量上传遇到 413 自动拆小批重发；去掉 sync_transport 里三处 20 秒总时限（BUG-97）】

现成项目与许可证（第一节）
  - 不引新库。超时用 httpx（BSD-3）自带的 connect/read/write/pool 超时（https://www.python-httpx.org/advanced/timeouts/）。
  - 本项目已有：sync_transport.py 的 write_many、read_changes、_mutate；sync_course.py 的分批规则（每批最多 100 条或 2 MB，UPLOAD_BATCH_BYTES）；106-MF02 做的“遇到 404/405 退回逐条上传”。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard c1142b0a
提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号；不调用真实模型；不连真实同步服务器，一律用本机假服务器。
D:\MentorFlowApp、D:\MentorFlowData 只读；不要启动、结束或重启用户的软件和源代码版。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。
产物放在 D:\MentorFlow\.local\test-tmp\sync135\，用完即删，只留 report.md。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。花费 ¥0。
同时在跑：MF-04 在改模型异常处理（134-MF04，connections/、web/src 里模型错误的显示）。你不碰这些。

背景（CURRENT_STATE [BUG-97]）
  - 2.0.14 本地攒了 107 条待上传。一批 100 条，超过了服务器批量接口当时 1 MB 的限制，服务器回 413。客户端只在 404/405 时才退回逐条上传，所以遇到 413 整轮失败，之后每轮都重复。
  - 服务器已放宽到 9 MB（2026-10-14）。但以后一批超过 9 MB，或者经过的代理限制更小时，还会这样失败。
  - 服务器放宽后还有第二个原因：sync_transport.py 给整批（约 105 行）、翻页（约 204 行）、单条（约 351 行）各设了 20 秒总时限。经用户的代理上传约 1 MB、加上服务器处理，超过了 20 秒，客户端就放弃并显示“同步失败”，其实服务器已经保存了。这是和 [BUG-90] 同类的自加时限。
  - 另外，httpx.Client(timeout=20) 的读超时也是 20 秒；服务器批量接口在 nginx（edge.conf）里的 proxy_read_timeout 是 60 秒。
  - 2.0.16 的发版范围（用户 2026-10-15 确认）：本任务和 134-MF04（模型异常处理），用软件内更新发。

要做
  1. 413 拆批。
     · 批量上传回 413 时，把这一批对半拆开分别重发，直到成功。
     · 拆到单条还是 413，就走现有的单条写入（服务器单条上限也是 9 MB）。
     · 单条写入仍被拒的，只把这一条记为失败（照现有单条失败的处理，界面上能看到原因），不挡住其他记录，不丢、不重复。
     · 拆批过程中保持现有的 CAS 语义（expected_revision、request_id），重发时用同一个 request_id。
  2. 去掉三处 20 秒总时限：write_many、read_changes、_mutate 里 `time.monotonic()-started > 20` 那几行。
     · 之后只靠 httpx 的超时：连接超时保持现状；读超时改成和服务器批量接口的 proxy_read_timeout 一致，即 60 秒，回执写理由。
     · 下载原文、历史块等其他请求的超时不动；如果它们也有同类的总时限，列出来，一并去掉并写理由。
  3. 状态显示：拆批重发期间，右上角照常显示“正在同步”，中途不要显示“同步失败”；全部失败了才显示失败。
  4. 测试（文件名带 135），用本机假同步服务器：
     · 批量体积超过阈值时回 413：能自动拆到成功，所有记录各上传一次、不重复。
     · 单条也回 413：只那一条失败，其余成功。
     · 服务器响应比原来的 20 秒时限还慢、但最后回 200：不再判失败（测试里可以缩短时间尺度，别真等太久）。
     · 404/405 退回逐条照旧；认证 401/403 照旧。
     · 相关测试和最后一次全量都要通过。
  5. 改动范围：只改 refactor/learning/sync_transport.py、sync_course.py 里和分批上传有关的部分，以及 tests/refactor/。不碰 connections/ 和 web/src（MF-04 在改）。

完成标准
  · 一批太大时能自动拆成小批同步成功，不再整轮失败。
  · 服务器处理慢但最后回 200 时，不再显示“同步失败”。
  · 记录不丢、不重复。
  · 相关测试与全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\sync135\report.md。
回执：写到 D:\MentorFlow\.local\receipts\135-MF02.md（格式见 WORKFLOW「回执格式」），给用户只回一行“135-MF02 完成/部分完成/受阻，回执已写入”。回执项目：基线 ／ 提交 ／ 413 拆批做法 ／ 单条仍 413 时怎么处理 ／ 去掉的时限和新的超时设置 ／ 状态显示 ／ 相关测试与全量 ／ 本地最终占用
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
