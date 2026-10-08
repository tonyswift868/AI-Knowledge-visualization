# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读它开头的「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务；两位架构师（都是 Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。架构师之间不直接对话，交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：先读 AGENTS.md、CURRENT_STATE「当前阶段」和索引、本文件；看 `git log` 里上次基线之后的提交；确认 7 个工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。与用户讨论到一半的事，看「当前阶段」的“等用户决定的事”和各条目里标了“讨论中”的待修。
- **交班**（每次停手前都做，不等用户说换人）：用户定下的事当场写进对应编号条目并标日期；自己查到但还没讨论完的问题写进对应条目的“待修”，标明“讨论中”；更新「当前阶段」、任务索引、待派队列；本地提交。不把事情只留在对话或个人记忆里。
- **现在在做的事**（2026-10-08）：第八十六批已全部合入（主线 56251799，全量 3133 通过、0 失败；未重启，用户软件已关）。进行中：87-MF04 语音引擎内存（朗读默认关、开着有明显开关、关掉即释放）。待转发：88-MF05 原生登录加载过场、88-MF06 后台任务初始状态。从源代码启动：Start-Process -FilePath D:/MentorFlow/.venv/Scripts/pythonw.exe -ArgumentList 'D:/MentorFlow/refactor/desktop/source_start.pyw' -WorkingDirectory D:/MentorFlow -WindowStyle Hidden；重启前先问用户。用户机器上另有 Kristina 的语音引擎（D:\Kristina_Runtime，kristina.bridge.launcher 启动）约 8 GB，不是本项目的，不碰。番茄钟、自习室暂不做。81-MF06 排除规则留到以后测新建计划时再看；81-MF04 镜像保留。

## 协作方式

- **不懂就问（用户 2026-10-05 定）**：有任何不懂或感觉有偏差，直接问用户。要读特别大的东西先告诉用户，机械性的读取整理交给员工读好带回结论。
- **先讨论、不堆屎山（用户 2026-10-05 定，最优先）**：问题先和用户讨论清楚（现象、原因、牵连范围、前置依赖），再写任务；不出看着笃定但有漏洞的方案，宁可慢。用户催时也只针对当前测出的问题，把所有能并行、不冲突的改动一次派完；有前置依赖的写明先后与原因。CURRENT_STATE 开头「工作准则」同此。
- **验收测试不和修复并行（用户 2026-10-05 定）**：修复任务回执合入后，再在新基线上发验收/测试任务；不让测试跑在已知要改的旧代码上。
- **不重复造轮子（用户 2026-10-04 定，硬规定）**：每份任务开头第一节固定写“用的是哪个现成项目、许可证是什么”；要自己写的，写明为什么没有现成的可用。没有这一节或理由站不住的任务，用户直接打回，不转给员工。
- **架构师省额度（用户 2026-10-04 定）**：架构师只读员工交上来的结论，自己不跑测试、不翻大日志；合入后的全量测试与离线完整场景也交给员工跑；回用户的话尽量短。
- **流程**：架构师（Claude）写任务 → 用户转发给员工（GPT/Codex，可能是较弱的模型）→ 员工在自己工位完成 → 用户把回执贴回 → 架构师审查、合入主目录、本地提交。员工不改主目录、不推送、不 ssh、不部署、不碰正式签名私钥。推送 GitHub 要等用户说。
- **分工**：前端、老师/判定/检验等核心编排由架构师自己做；员工做边界清楚、耗时的单模块任务。架构师自己动手的标准：GPT 员工做不好、而架构师做起来便宜的事（例如 BUG-34 这种小修）。员工的结论要用真实数据核对后才采信。
- **先讨论后派活**（2026-10-02 用户再次强调）：每次收到回执，回复里只讲审查结果与发现的问题、给建议和要用户定的事，不附下一批任务文本；用户讨论完、同意了，再一次写出整批任务文本放在回复最后，然后停下等用户转发。不要“拿到回执就直接根据回执把下一批发出来”——那样效率低、大部分时间浪费。用户说“先说问题”时同理只讲问题。
- **一批派完、不挤牙膏**：每轮把当前适合的任务一次派出；同时派几个员工不设上限，以开发效率为准（2026-10-01 用户定，取代原先最多 3 个），但改同一文件同一部分的任务要排先后，避免合并冲突；不派无意义的活；用户没要求的事（例如更新价格表）先问再做。
- **架构师省着用**：测试、跑数据、复查记录这类耗量大的活尽量派给员工，架构师只做判断、审查和合入（2026-10-01 用户提出：架构师自己做消耗太大）。
- **状态如实**：写好的任务在索引里标“已起草，待用户转发”；用户说“发了”才改“进行中”；回执回来后改“已合入/已完成（部分）”。
- **员工自主**：每条任务写清决策原则，员工遇到选择自己决定、在回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停。
- **防止无穷无尽地改**（2026-10-01 用户担心“一直跑没有进步、改个无穷无尽”）：每个问题最多两轮——第一轮按现方法修，没解决就第二轮换方法（先查开源项目和论文）；第二轮还不行，带数据找用户定（接受现状、调整目标或换方案），不再自行反复调。每条任务只做一轮“实现 → 测 → 报告”，写明完成标准；没达标不在任务里继续微调，回执写清没过的样本、原因判断和建议换的方法。不追求把测试做大，看的是发现的问题有没有解决。
- **内测阶段不打包、不发版**（用户 2026-10-04 定，替换 2026-10-02 的“每合入一批就发新版本”）：功能还没跑通前不打包；测试在源代码上用模拟工具跑（DIR-01“测试”）；等用户说可以发了再打包、签名、上传。员工负责打包（不签名），签名、上传由架构师做（私钥与服务器不给员工）。
- **派任务前的检查**（2026-10-03，复盘“部分完成”太多后定）：①每条完成标准都必须是这个员工凭自己的权限能做到的，不依赖别的并行任务、外部网站或用户操作；取决于外部的写成“查清后二选一”，两种结果都算完成；②互相依赖的工作不拆成并行任务各自要求全量通过——要么一个人做，要么并行任务只要求本块测试通过、全量放到联调任务；③写“怎么做”之前先读相关链路的代码（例如更新程序自身怎么升级），并检查环境（服务器、已装软件、路径）。
- **方案由架构师出**（2026-10-02 用户定）：发现的问题由架构师查证据、找开源项目和论文、想清楚做法，在任务里写明“怎么做”，员工照着做；不写“你去解决”让员工自己想方案（员工对项目的理解没有架构师深，容易偏，效率低）。测试剧本也要想仔细，不能把测试里的人为限制当成用户需求（第 30–34 批的“每模块 3 点”就是例子）。
- **先跑真实流程，再看记录定修什么**（2026-10-02，回顾第二十五到三十批后定）：找问题靠完整的真实流程（小测试脚本）和对真实记录的逐条错误分析，不靠给单个组件自定目标；修复任务只针对真实记录里找到的失败，完成标准写成“这个失败不再出现”的二元判断，用同一个脚本或保存的记录复测。一个任务只做一件事：要么实现修复，要么做评测，不把两者和小预算捆在一起。固定评测集长期保留并在每次相关改动后回放：判定考卷（.local/test-tmp/judge-holdout，MF-06 保管）、出题回归集（tests/refactor 下 28-MF04 的回放）、讲稿埋错集（.local/test-tmp/prep-cost）、完整流程脚本（tests/refactor/acceptance/test_math_complete_flow_real.py）。
- **花钱的测试**：只为具体改动花钱，事先写下预期效果；能离线的先离线（假模型、重放保存的请求）；预算分两段，第一段没达到预期就停；派之前告诉用户每个任务的把握和预算。真实模型用管理员现有的 DeepSeek Flash 预设（Key 只在 Windows 凭据里，从不显示）。
- **功能先行**：功能没做完前任务里一律禁止加安全加固（AGENTS.md）；审查时删掉员工多加的加固。
- **问题留档**：每修好一个问题，同一次提交里在 CURRENT_STATE 记 BUG 条目（AGENTS.md 规则 7）。
- **部署**：每次改服务器都先向用户说明改什么、拿到同意再做。
- **向用户汇报**：合入后开头先给一张“环节 × 状态”表（通过/未通过/没测到），再说问题和建议；数字要和报告一致。
- **署名**：主目录提交一律用 `git -c user.name="tonyswift868" -c user.email="205869700+tonyswift868@users.noreply.github.com" commit ...`，结尾加 Co-Authored-By 行；不要写进共享的 git 配置（工位 worktree 共用它）。员工提交用各自工位名（见任务模板）。

## 架构师操作手册

- **全量测试**（约 4 分钟）：`TMP=D:/MentorFlow/.local/tmp TEMP=D:/MentorFlow/.local/tmp .venv/Scripts/python.exe -m pytest tests/refactor -q -p no:cacheprovider --basetemp=D:/MentorFlow/.local/tmp/pt`。2026-09-30 为 2436 通过、26 跳过。只单独跑 app 与 timeline 两组测试时偶发 pytest 段错误，属测试进程问题，忽略。
- **界面构建**：先把 `/d/MentorFlow/.local/node` 加进 PATH，再 `node .local/web-deps/node_modules/vite/bin/vite.js build --config refactor/learning/web/vite.config.mjs`；dist 不进仓库（.gitignore），打包时现构建；合入改过界面的任务后要构建一次确认能过。
- **合入员工分支**：`git diff --binary <基线> <员工提交> | git apply --index --3way`，逐文件看改动是否在任务范围内，跑全量测试和界面构建，再用上面的署名提交。多人同时改 app/classroom.py 时手工合并冲突。
- **基线**：合入并提交后，主目录 `git rev-parse --short HEAD` 就是下一批任务的基线，写进每条任务的“开工同步”。
- **写脚本**：含 Windows 路径或反斜杠的内容不要用 bash heredoc（会被吃掉，BUG-10），写成文件再运行，或用编辑工具。
- **服务器**：`ssh -F "D:/MentorFlow/.local/server/ssh_config" mentorflow`（89.45.45.28，用户 mentorflow-admin，用 sudo -n）。线上后端是 mentorflow-slim-backend:MF-75（2026-10-07 上线，永久登录，数据库 0043），切换脚本 /opt/mentorflow/state/mf75/switch.sh；回到 MF-14 用 `sudo bash /opt/mentorflow/state/mf75/switch.sh rollback`；上线前数据库备份 /opt/mentorflow/state/mf75/mentorflow-before-mf75.dump。更早的 MF-14 切换脚本与备份仍在 /opt/mentorflow/state/mf14/。业务库 mentorflow 所有者是 mentorflow_migrator，迁移用超级用户连接加 options=-c role=mentorflow_migrator。架构师上服务器需要用户把会话切到“每条命令都询问”并逐条批准（自动模式会拦生产读写）。服务器上只有管理员一个账号。
- **资料白名单目录**：服务器 /opt/mentorflow/state/mentorflow-e1-preview/catalog/（source-whitelist.json 129 条、model-prices.json；旧版备份 *.bak-20260929）；本机待发布副本在 .local/catalog-release/。发布前用客户端同一套格式检查，下发的文件不能带 "candidates" 键。
- **发布新版**（SYS-06）：打包用 D:\MentorFlow\.local\release-tools\build_release.py（README.txt 有用法，员工或架构师都能跑，不碰私钥）；签名后上传可参考 .local\test-tmp\release-2.0.2\ 里的 stage_missing.py（只传服务器缺的对象）、upload_each.sh（逐个传、逐个校验）、verify_public.py（公网验签）。原流程：用 scripts/build_learning_installer.py 的 freeze → release（签名清单，--valid-days）→ sign（私钥 .local/release-keys/mentorflow-app-2026.private.key，说明见同目录 README.txt）→ installer。上传到服务器 /opt/mentorflow/update-static/e108/app/ 的 .staging，逐个按 SHA256 校验后放入，最后原子替换 stable/stable.json；公网地址 https://89.45.45.28/mentorflow/updates/app/。给已装的 2.0.0 推的第一版必须 --valid-days 30；线上 stable.json 2026-10-28 过期。发布要用户同意。
- **磁盘**：测试与验收产物都在 .local/test-tmp/ 下。员工的临时目录架构师不删（用户 2026-10-05 定，员工可能还要用）；要清理先问用户。必须保留：installer/build-tools（打包工具）、release-2.0.1/payload（发版脚本的对照包）、已发布版本的构建目录（现为 release-2.0.2）、acceptance/（cache_hit/acceptance_replay.py 读它）。

## 当前基线

| 项目 | 值 |
| --- | --- |
| main | c74ac725（含第五十八批，未发布）；派新任务前 `git rev-parse --short HEAD` 取 |
| 已发布 | 2.0.2 = 4430aae（3 年签名）；打包脚本 D:\MentorFlow\.local\release-tools\build_release.py |
| 未推送 | main 比 origin/main 多 200 多个提交；用户说了才推送 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07，七个人）转发任务；工位（library、runtime、client、platform、tools、classroom、server 七个目录）每次在任务里另行指定，不和员工固定绑定。派活前先看这张表：同一个员工、同一个工位同时只能有一个任务（2026-10-04 用户纠正：不能给正在跑的员工再派）。任务号写成“批次-员工编号”，例如 46-MF02 就是派给 MF-02。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 空闲（81-MF01 已合入） | server |
| MF-02 | 空闲（86-MF02 已合入） | client |
| MF-03 | 空闲（86-MF03 已合入） | classroom |
| MF-04 | 87-MF04 进行中 | runtime |
| 88-MF05 | MF-05 platform | 原生登录与启动窗口加载过场，与网页一致（¥0） | UI-00 | 56251799 | 已起草，待用户转发 |
| 88-MF06 | MF-06 tools | 后台任务初始状态：detail 补分钟重算等状态字段，打开页面即显示过场（¥0） | UI-00、MAP-01 | 56251799 | 已起草，待用户转发 |
| MF-05 | 88-MF05 已起草，待用户转发 | platform |
| 84-MF06 | MF-06 tools | 知识点分钟数按内容分量重算：程序按原文长度和题量算、AI 只标难度，老计划自动重算（¥1） | PLAN-04、PLAN-05 | 95fa4107 | 已合入（f3fed92d，完成；守住原总量 4328，不受约束估算 3118，用户选按内容算→85-MF06） |
| 84-MF03 | MF-03 classroom | 学习进度状态：逾期待还/已还、今天进度、超前，一次一种、按严重程度配色（¥0） | PLAN-05、UI-00 | 95fa4107 | 已合入（04264b1b，完成；用户副本上应显示逾期待还 331 分、今天已还 24 分） |
| 85-MF06 | MF-06 tools | 计划总时长改按内容算、老计划自动重排日程（¥0.5） | PLAN-04、PLAN-05 | bf18799b | 已合入（91b636fc，主线 197bc861；副本总量 4967→3750，10-07 未学的 331 分摊进后面每天，顶部变“今天 24 / 289”） |
| 86-MF02 | MF-02 client | 统一加载过场：盘点所有等待处、三档（不显示/骨架/过场＋文字）、老师正在想（¥0.3） | UI-00 | 197bc861 | 已合入（ebb1d0d0，主线 56251799；39 类等待 38 类统一，原生登录与分钟重算初始状态未覆盖→88） |
| 86-MF03 | MF-03 classroom | 模型连接一断自动重试：未出字前最多重试 2 次，用户无感（¥0.3） | UI-00、BUG-58 | 197bc861 | 已合入（007ea156，主线 ec2ffe7e；未发出的连接失败最多重试 2 次，间隔 1、3 秒） |
| 87-MF04 | MF-04 runtime | 内存：朗读关着不启动语音引擎、关朗读和空闲释放；查清主程序线程（¥0.3） | VOICE-01、BUG-58 | ec2ffe7e | 已转发，进行中 |
| MF-06 | 88-MF06 已起草，待用户转发 | tools |
| MF-07 | 空闲（81-MF07 已合入） | library |

空闲工位：server、client、classroom、library。

## 待派队列

- 无（第五十九批两份已起草，见上）。

## 第八十八批任务原文（2026-10-08，基线 56251799）

### [88-MF05] platform｜原生登录与启动窗口加载过场，与网页一致（¥0）

~~~text
【MentorFlow 第八十八批｜MF-05（工位 platform）｜原生登录与启动窗口也用同样的加载过场：不让用户对着空白或干转圈】

现成项目与许可证（第一节）
  - 只改本项目现有代码，不新增依赖：refactor/desktop/ 下的登录与启动界面（login_shell.py、account_shell_main.py 里显示等待的地方）；用 PySide6 自带能力（QPropertyAnimation、QTimer、样式表）做动画，配色和文案跟 86-MF02 网页里的过场一致（读它的 CSS 取色值和文案风格）。不嵌网页、不引动画库。

开工同步
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard 56251799
提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。不要结束或重启用户正在用的那个软件进程（从 D:\MentorFlow 主目录启动的）；你自己的测试进程从工位目录启动、用复制的数据。
同时在跑（各改各的，冲突由架构师合并）：87-MF04 语音引擎内存（speech.py、voice/tts、useTeacherVoice.ts、老师栏朗读开关）；88-MF05 原生登录窗口加载过场（desktop/ 下登录与启动界面）；88-MF06 后台任务初始状态（plan_flow.py 的 detail 字段、PlanDetail/KnowledgeMap 读取）。
主线已含 86-MF02 的统一加载组件（web/src，三档：＜300ms 不显示、0.3～3s 骨架、＞3s 过场＋文字、＞15s 慢提示与重试/取消），报告见 D:\MentorFlow\.local\test-tmp\fix86-loading\report.md，先读它的盘点表和组件说明。
产物目录：D:\MentorFlow\.local\test-tmp\fix88-login\。目标两小时内交回。花费上限 ¥0。
背景（用户 2026-10-08 定，CURRENT_STATE [UI-00]“加载规则”）：86-MF02 把网页里 38 类等待统一成加载过场，但启动时的登录窗口是 Qt 原生界面，不在网页里，没覆盖。用户要求补上。
要做
  1. 盘点原生界面里所有等待：软件启动到登录窗出现、点登录到进入主界面、自动登录与恢复会话、切换账号、退出登录、登录失败重试、会话恢复失败等；列表写进报告（现在显示什么、实测多久）。
  2. 按 86-MF02 的同一套三档规则做：很短不显示；中等显示轻量动画；较长显示与网页一致的过场（主题色、动效风格）＋一句说清正在做什么（例如“正在连接账号服务…”“正在恢复上次的登录…”）；超过 15 秒提示“比平时慢，可能是网络问题”并给重试（能取消的给取消）。系统开了减少动态效果就只保留静态文字。
  3. 主界面网页加载出来之前的空窗（登录成功到网页第一帧）也不能空白：显示同样的过场直到网页准备好。
  4. 不改登录逻辑、令牌、密码处理；83-MF05 的“只有用户明确退出才退出”和退出诊断保持不变。
  5. 实测：从工位启动（用复制的数据）走启动、登录、进入主界面，记录每段耗时和显示了什么；截图亮暗各一套（启动过场、登录中、慢提示）。离线测试覆盖三档切换和慢提示；全量通过、web 构建通过。
完成标准：原生界面里每处等待都有和网页一致的过场与文字；登录成功到网页第一帧不空白；登录逻辑不变；全量通过。
验收：报告和截图写到 D:\MentorFlow\.local\test-tmp\fix88-login\report.md。
回执（尽量短，用表格）：【MF-05｜platform｜第八十八批｜完成/部分完成/受阻】基线 ／ 提交 ／ 盘点处数 ／ 已覆盖 ／ 实测耗时 ／ 截图 ／ 全量与构建 ／ 本地最终占用
~~~

### [88-MF06] tools｜后台任务初始状态：detail 补分钟重算等状态字段，打开页面即显示过场（¥0）

~~~text
【MentorFlow 第八十八批｜MF-06（工位 tools）｜后台任务初始状态：打开页面时就知道知识点连线、分钟重算、后台备题是否还在跑】

现成项目与许可证（第一节）
  - 只改本项目现有代码，不新增依赖：app/plan_flow.py（detail 返回字段，照 82-MF06 的 point_links_status 写法）、你 84/85 写的分钟重算流程、后台备题（_prepare_worker）现有状态；前端只改读取这些字段的地方（web/src/pages/PlanDetail.tsx、planning/KnowledgeMap.tsx 及 86-MF02 的加载组件调用处）。

开工同步
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard 56251799
提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。不要结束或重启用户正在用的那个软件进程（从 D:\MentorFlow 主目录启动的）；你自己的测试进程从工位目录启动、用复制的数据。
同时在跑（各改各的，冲突由架构师合并）：87-MF04 语音引擎内存（speech.py、voice/tts、useTeacherVoice.ts、老师栏朗读开关）；88-MF05 原生登录窗口加载过场（desktop/ 下登录与启动界面）；88-MF06 后台任务初始状态（plan_flow.py 的 detail 字段、PlanDetail/KnowledgeMap 读取）。
主线已含 86-MF02 的统一加载组件（web/src，三档：＜300ms 不显示、0.3～3s 骨架、＞3s 过场＋文字、＞15s 慢提示与重试/取消），报告见 D:\MentorFlow\.local\test-tmp\fix86-loading\report.md，先读它的盘点表和组件说明。
产物目录：D:\MentorFlow\.local\test-tmp\fix88-status\。目标一个半小时内交回。花费上限 ¥0。
背景（86-MF02 报告“部分完成”第 2 条）：分钟重算只有状态变化事件（plan.point_minutes_changed），没有初始状态字段；页面打开前已经开始的重算，前端不知道它还在跑，没法显示过场。知识点连线已有 point_links_status，可以照着做。
要做
  1. plan.detail 增加 point_minutes_status（none/running/ready/failed，和 point_links_status 同一套值），反映打开页面那一刻分钟重算的真实状态；检查后台备题等其他会在计划页面体现的后台任务，凡是界面需要显示进度而 detail 里没有初始状态的，一并补上同风格字段（列清楚补了哪些、没补的为什么不需要）。
  2. 前端在打开计划详情时就读这些初始状态，用 86-MF02 的加载组件显示（例如“正在按内容重算知识点时长…”），收到现有事件后照常刷新；完成后自然消失。
  3. 不让 detail 变慢（只读内存或已存状态，不调模型、不做重计算）。
  4. 离线测试覆盖：页面打开前已在跑 → 打开即显示；跑完 → 消失并刷新；失败 → 显示重试（有重试动作的）；全量通过、web 构建通过。截图：重算进行中、连线进行中，亮暗各一张。
完成标准：所有需要显示的后台任务打开页面就有正确初始状态并有过场；detail 不变慢；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix88-status\report.md。
回执（尽量短，用表格）：【MF-06｜tools｜第八十八批｜完成/部分完成/受阻】基线 ／ 提交 ／ 新增字段 ／ 前端显示 ／ detail 耗时前后 ／ 截图 ／ 全量与构建 ／ 本地最终占用
~~~

## 第八十七批任务原文（2026-10-08，基线 ec2ffe7e）

### [87-MF04] runtime｜内存：朗读关着不启动语音引擎、关朗读和空闲释放；查清主程序线程（¥0.3）

~~~text
【MentorFlow 第八十七批｜MF-04（工位 runtime）｜内存：朗读关着就不启动语音引擎、关掉朗读就释放；查清主程序 200 个线程】

现成项目与许可证（第一节）
  - 只改本项目现有代码，不新增依赖：refactor/learning/app/speech.py（warm_up、_start_service、_release_service）、refactor/learning/voice/tts/（service.py、engine_server.py、runtime.py）、前端动 web/src/teacher/useTeacherVoice.ts（autoWarm 默认值与调用处传参）和老师栏底部现有的朗读开关（让开关状态一眼看懂），Classroom.tsx 尽量少改。语音引擎是用户机器上 E:\AI_Tools 下的 GPT-SoVITS（只读运行，不改它的文件、不复制）。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard ec2ffe7e
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。不要结束或重启用户正在用的那个软件进程（从 D:\MentorFlow 主目录启动的，及它启动的语音引擎）；你自己的测试进程从工位目录启动、用复制的数据。
同时在跑：86-MF02 统一加载过场（前端 web/src，可能也改 Classroom.tsx）；你的前端改动尽量只在 useTeacherVoice.ts，冲突由架构师合并。
产物目录：D:\MentorFlow\.local\test-tmp\fix87-memory\。目标两小时内交回。花费上限 ¥0.3（只为进课堂验证时老师开场的真实模型调用）。
背景（用户 2026-10-08 亲测，任务管理器看到软件占内存很大）：架构师查到，软件进程树共约 9 GB 私有内存，其中：
  - 语音引擎 GPT-SoVITS（E:\AI_Tools\GPT-SoVITS-Kurisu\...\runtime\python.exe 跑 refactor/learning/voice/tts/engine_server.py）私有内存约 7.2 GB（工作集只有 61 MB，说明大部分已被换出但一直占着提交量）。用户课堂里“朗读已关闭”，引擎却在跑。原因：useTeacherVoice.ts 第 66 行 autoWarm 默认 true，没有任何调用处关掉；只要语音已安装，进课堂就 speech.warm_up 预热引擎，和朗读开关无关，之后也从不释放。架构师已应用户要求手动结束了这个引擎进程。
  - 主程序 pythonw 私有约 1.6 GB，工作集稳定在 130～150 MB（启动时冲到 1.1 GB 后回落，不像泄漏）；但 83-MF05 的诊断每 5 分钟记一次，线程数一直约 200 个，偏多。
要做
  0. 用户定的规则（2026-10-08 原话的意思）：只要用户没有主动打开，朗读就默认不开启；用户一旦打开，界面上要有一个明显的小按钮随时能关，按钮状态让用户一眼看明白（开着/关着）。默认值改为关闭；用户以前自己打开过的保持打开，从没动过的一律按关闭处理。按钮放在课堂里朗读相关的现有位置（老师栏底部“朗读已关闭”那里），不要另起炉灶。
  1. 语音引擎只在需要时运行：朗读关着时，进课堂、开课、老师说话都不启动引擎；用户打开朗读或点某句的播放时才启动（可以先预热、首句稍慢，用加载提示接住）；关掉朗读后，在当前这句说完或立即停止后释放引擎进程；朗读开着但连续一段时间（例如 10 分钟，你定并写理由）没有朗读也释放，下次需要再启动；软件退出时引擎一定退出，不留孤儿进程（包括主程序被强制结束的情况，能做到就做，例如让引擎自己检测父进程消失后退出）。
  2. 引擎占用：查清 7.2 GB 提交量是什么（模型加载方式、是否加载了用不到的模型或精度、是否 CPU 上整份加载等），在不改 E 盘文件、只改我们这边启动参数和加载方式的前提下，能降就降；降不了写清原因。报告写出启动后、朗读一段后、空闲时的私有内存和工作集。
  3. 主程序线程：列出这约 200 个线程分别是谁开的（Python 线程名与数量、线程池、Qt/WebEngine 原生线程），找出不必要的（例如每个服务各开一个大线程池、空闲时不收缩），能合并或改小就改，写清改前改后数量。
  4. 验证：在用户数据副本上从工位启动软件、进课堂：朗读关着时确认没有引擎进程；打开朗读，老师说一句，引擎启动并正常出声（或至少合成成功）；关掉朗读，引擎在你定的时间内退出；空闲超时后退出；退出软件后没有残留进程。记录每一步整个进程树的私有内存总和，前后对比。
  5. 离线测试覆盖上面的启动与释放规则；全量通过、web 构建通过。
完成标准：朗读默认关闭、开着时有明显的小按钮随时关；朗读关着不占引擎内存；关朗读和空闲都会释放；不留孤儿进程；引擎和线程占用查清并能降则降；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix87-memory\report.md。
回执（尽量短，用表格）：【MF-04｜runtime｜第八十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 默认关闭与开关按钮（截图） ／ 引擎启动与释放规则 ／ 引擎内存（原因、改前改后） ／ 线程（来源、改前改后） ／ 实测进程树内存（朗读关、开、关后、退出后） ／ 全量与构建 ／ 花费 ／ 本地最终占用
~~~

## 第八十六批任务原文（2026-10-08，基线 197bc861）

### [86-MF02] client｜统一加载过场：盘点所有等待处、三档（不显示/骨架/过场＋文字）、老师正在想（¥0.3）

~~~text
【MentorFlow 第八十六批｜MF-02（工位 client）｜统一加载过场：所有要等的地方都不让用户对着空白或转圈干等】

现成项目与许可证（第一节）
  - 用项目已有依赖，不新增：React、lucide-react（ISC）图标、CSS 动画；项目已有的骨架屏组件（web/src/ui 里的 Skeleton）和 81-MF02 的 15 秒超时重试、错误边界都在，先看能复用多少。动画只用 CSS（transform/opacity），不引动画库、不加大图片或视频。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard 197bc861
提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。不要结束或重启用户正在用的那个软件进程（从 D:\MentorFlow 主目录启动的）；你自己的测试进程从工位目录启动、用复制的数据。
同时在跑（第八十六批共两份，各改各的，冲突由架构师合并）：86-MF02 统一加载过场（只改前端 web/src）；86-MF03 模型连接一断自动重试（只改后端 connections/gateway.py、providers 与相应测试，不改前端）。两份互不依赖：重试对界面是无感的，界面只会看到等待时间稍长，由 86-MF02 的加载过场接住。
产物目录：D:\MentorFlow\.local\test-tmp\fix86-loading\。目标三小时内交回。花费上限 ¥0.3（只为第 5 条在副本上进一次课堂看真实老师回复的等待）。
背景（用户 2026-10-08 定，CURRENT_STATE [UI-00]“加载规则”）：凡是要等的地方，不能让用户对着空白或转圈干等。慢的原因第八十一批已查过并修（查看路线 41 秒→0.4 秒、进课堂 1.1 秒→0.5 秒）；合理范围内仍要等的，统一做过场动画和文字提示。用户原话的意思：别让用户白等着，弄个过场动画一样的就行。
要做
  1. 先盘点：把前端所有“要等”的地方列成表（页面/操作、现在显示什么、实测大约多久），至少包括：启动与登录、首页、我的计划、查看路线（计划详情）、进课堂、老师回复（流式开始前）、模块检测判题、新建计划的各步（聊需求、找资料、按资料确认结构、填知识点、备题）、加资料、知识点连线和分钟重算的后台状态、设置里的模型连接测试和用量查询、上传题目自测的识别、闲聊回复。表放进报告。
  2. 做一个统一的加载组件（一套，全局复用），按等待长短分三档：
     - 很短（约 300 毫秒内）：什么都不显示，避免闪一下；
     - 中等（约 0.3～3 秒）：页面骨架或轻量动画，形状接近要出来的内容，不跳版；
     - 较长（约 3 秒以上）：过场动画（和现有主题风格一致，例如花瓣、光点、老师栏的配色）＋一句说清“正在做什么”的文字；知道步骤的显示当前步骤（例如“正在找资料 2/4”）；超过 15 秒加一句“比平时慢，可能是网络问题”并给“重试/取消”（能取消的地方）。
     老师回复等待：在老师消息位置显示“老师正在想…”这类有动感的占位，流式文字一到就平滑替换，不闪。
  3. 把第 1 条里所有地方都换成这个组件；不再有单独的转圈或空白页。错误时沿用 81-MF02 的错误边界和重试，不白屏。
  4. 亮暗两套主题、窄窗口（600 像素）都好看；系统开了“减少动态效果”时只保留静态提示和文字。
  5. 实测：在用户数据副本（复制到产物目录，只读原数据）上用真实本地桥接走一遍：启动、查看路线、进课堂、老师回复一轮、加资料入口；每处记录“点下去到看到内容”的时间和中间显示了什么。截图（亮暗）：中等等待骨架、较长等待过场、老师正在想、超过 15 秒的提示。
  6. 全量测试与 web 构建通过。
完成标准：第 1 条表里每个地方都用统一组件、不再空白或光转圈；三档按时间切换；副本上实测与截图齐全；全量通过。
验收：报告和截图写到 D:\MentorFlow\.local\test-tmp\fix86-loading\report.md。
回执（尽量短，用表格）：【MF-02｜client｜第八十六批｜完成/部分完成/受阻】基线 ／ 提交 ／ 盘点处数 ／ 三档规则 ／ 已替换处数（未替换的写原因） ／ 副本实测（各处耗时） ／ 截图 ／ 全量与构建 ／ 花费 ／ 本地最终占用
~~~

### [86-MF03] classroom｜模型连接一断自动重试：未出字前最多重试 2 次，用户无感（¥0.3）

~~~text
【MentorFlow 第八十六批｜MF-03（工位 classroom）｜模型连接一断自动重试：网络抖一下用户感觉不到，真断了才提示】

现成项目与许可证（第一节）
  - 只改本项目现有代码，不新增依赖：refactor/learning/connections/gateway.py 的 _complete（已有 MODEL_CONNECTION_FAILED、MODEL_TIMEOUT 分类和 usage 记账）、connections/providers 里各厂商的请求代码。httpx 自带的 transport 重试（HTTPTransport(retries=...) 只重试连接建立失败）先评估能不能直接用，能用就用，不自己写一套。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 197bc861
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。不要结束或重启用户正在用的那个软件进程（从 D:\MentorFlow 主目录启动的）；你自己的测试进程从工位目录启动、用复制的数据。
同时在跑（第八十六批共两份，各改各的，冲突由架构师合并）：86-MF02 统一加载过场（只改前端 web/src）；86-MF03 模型连接一断自动重试（只改后端 connections/gateway.py、providers 与相应测试，不改前端）。两份互不依赖：重试对界面是无感的，界面只会看到等待时间稍长，由 86-MF02 的加载过场接住。
产物目录：D:\MentorFlow\.local\test-tmp\fix86-retry\。目标两小时内交回。花费上限 ¥0.3（只为第 4 条真实验证）。
背景（用户 2026-10-08 亲测，CURRENT_STATE [BUG-58] 同日日志）：用户在课堂里网络抖了一下，日志一条 MODEL_CONNECTION_FAILED，课堂顶部出黄条“无法连接模型服务，请检查地址和网络”，用户点重试就好了。用户本机连外网经过本地代理，偶尔断一下。要做到：抖一下自动重试、用户无感；真断了才提示。
要做
  1. 规则：只在“连接都没建立 / 请求没发出去 / 还没收到任何回复内容”时自动重试（这时不会产生费用，也不会重复显示文字）；已经开始流式输出（on_text 已被调用过）或已收到计费回执的，一律不重试，照现在的方式报错。最多重试 2 次，间隔短（例如 1 秒、3 秒，你定并写理由）；用户取消（cancel）立即停，不再重试。
  2. 范围：对所有用途生效（课堂老师、判题、闲聊、建计划各步、后台连线和分钟重算等）；连接测试（connection_test）不重试，保持如实反映。
  3. 记账与日志：每次重试在日志里记一行（用途、第几次、原因），usage 记账照现有规则（没发出去的不计费，unknown 的照旧标 unknown）；只有最后一次失败才走现有的失败记录和报错。
  4. 真实验证：在用户数据副本上进课堂（管理员 DeepSeek 预设，Key 只经现有代码读取），在老师请求发出前人为断网 1～2 秒再恢复（例如临时把请求指到一个不通的代理再切回，或在 transport 层注入一次连接失败，方法你定），确认用户看不到黄条、老师正常回复，日志有重试记录；再模拟持续断网，确认重试用完后才出现原来的提示。
  5. 离线测试覆盖：连接失败后成功、流式开始后失败不重试、取消立即停、重试用完报原错误、connection_test 不重试、记账不重复；全量通过。
完成标准：网络抖一下用户无感；已开始输出的不重试；真断了照原样提示；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix86-retry\report.md。
回执（尽量短，用表格）：【MF-03｜classroom｜第八十六批｜完成/部分完成/受阻】基线 ／ 提交 ／ 重试规则（次数、间隔、哪些不重试） ／ 用了现成的什么 ／ 真实验证（抖动、持续断网） ／ 全量 ／ 花费 ／ 本地最终占用
~~~

## 第八十五批任务原文（2026-10-08，基线 bf18799b）

### [85-MF06] tools｜计划总时长改按内容算、老计划自动重排日程（¥0.5）

~~~text
【MentorFlow 第八十五批｜MF-06（工位 tools）｜计划总时长改按内容算：不再守住原总量，新计划直接用内容估算，老计划重算后自动重排日程】

现成项目与许可证（第一节）
  - 只改本项目现有代码，不新增依赖：在你 84-MF06 写的分钟数规则和重算流程上改（plan_flow.py、store.py 及其测试）；重排日程用 course/ledger.py 现有的 replan（不改它的分配规则），照现有“改日期与时长”“加资料”调 replan 的写法来。

开工同步
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard bf18799b
提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。不要结束或重启用户正在用的那个软件进程（从 D:\MentorFlow 主目录启动的）；你自己的测试进程从工位目录启动、用复制的数据。
同时在跑：83-MF05 软件无声退出（account_shell_main.py、runtime_logging.py、老师窗口），和你不冲突。
产物目录：D:\MentorFlow\.local\test-tmp\fix85-total\。目标一个半小时内交回。花费上限 ¥0.5（副本上重算若需重新标难度才用；能复用已标难度就不调模型）。
背景（用户 2026-10-08 定，CURRENT_STATE [PLAN-04]、[PLAN-05]）：84-MF06 按内容重算分钟数时守住了原学习总量 4328 分钟，但你报告里不受约束的估算只有 3118 分钟（少约 28%），说明生成时总时长估多了。用户选择：总时长改按内容算。主线已含 84-MF06 和 84-MF03（课堂顶部“逾期待还/今天已还/超前”三态）。
要做
  1. 规则：每个点的分钟数直接用规则函数算出的值（不再按原总量缩放）；模块学习分钟 = 各点之和；模块检测分钟沿用现有算法（若现有算法依赖学习分钟就随之更新，写清楚）；计划总分钟 = 新的学习＋检测总和。已经学过的点（账本里有 learn:/relearn: 记录的）分钟数不改，已记的时长不变。
  2. 重排日程：老计划重算后，用 ledger 现有 replan 按原日期窗口和每天上限把新总量重新分到剩余日子（已过去的日子和已记的时长照现有 replan 规则处理）；逾期待还、今天目标随之变化。重算只做一次（用新的版本号，84 的版本号已算过的老计划也要按新规则再算一次）；失败保留原值、下次打开再试；完成后推送 84 已有的 plan.point_minutes_changed 事件，并确保课堂顶部状态和计划详情都会刷新。
  3. 新计划：生成时就用内容估算的总量，生成预览里显示的总时长、每天任务量与之一致。
  4. 真实核对：复制用户数据到产物目录（只复制，不改原数据），在副本上跑一次。报告：重算前后计划总分钟、各模块学习/检测分钟；重排前后每天目标（10-07～10-20）；用户在课堂顶部会看到的状态和数（84-MF03 的三态，例如“逾期待还 X 分 · 今天已还 24 分”），前后对比。
  5. 离线测试覆盖：不再守总量、已学的点不改、版本号使老计划再算一次、replan 后日目标与逾期正确、新计划预览一致；全量通过、web 构建通过。
完成标准：新老计划总时长都按内容算；老计划自动重排日程，顶部状态随之正确；副本核对数对得上；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix85-total\report.md。
回执（尽量短，用表格）：【MF-06｜tools｜第八十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 规则改动 ／ 重排日程做法 ／ 副本核对（总量前后、每天目标、顶部状态前后） ／ 全量 ／ 花费 ／ 本地最终占用
~~~

## 第八十四批任务原文（2026-10-08，基线 95fa4107）

### [84-MF06] tools｜知识点分钟数按内容分量重算：程序按原文长度和题量算、AI 只标难度，老计划自动重算（¥1）

~~~text
【MentorFlow 第八十四批｜MF-06（工位 tools）｜知识点分钟数按内容分量重算：程序按原文长度和题量算、AI 只标难度，新计划照此生成，老计划自动重算】

现成项目与许可证（第一节）
  - 只改本项目现有代码，不新增依赖：course/planner_agents.py（难度标注提示）、app/plan_flow.py（生成时的分钟估算与老计划重算）、course/store.py（存分钟与重算版本）、connections/gateway.py（只加后台用途名）。
  - 先看本项目现成写法：82-MF06 的知识点连线（按模块后台调用、短编号映射、老计划打开时自动补、事件推送）就是同一种流程，照它的结构写，能复用就复用。

开工同步
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard 95fa4107
提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。不要结束或重启用户正在用的那个软件进程（从 D:\MentorFlow 主目录启动的）；你自己的测试进程从工位目录启动、用复制的数据。
同时在跑（各改各的，冲突由架构师合并）：83-MF05 软件无声退出（account_shell_main.py、runtime_logging.py、老师窗口）；84-MF06 知识点分钟数重算（planner_agents.py、plan_flow.py 的分钟估算与重算、store.py）；84-MF03 课堂顶部学习进度状态（ledger 状态投影、classroom.py 的 today 字段、Classroom.tsx 顶部、其他显示“今天 X / Y 分”的页面）。84-MF06 不改 ledger 和课堂界面；84-MF03 不改知识点分钟数的来源。
产物目录：D:\MentorFlow\.local\test-tmp\fix84-minutes\。目标两小时内交回。花费上限 ¥1（只为第 5 条真实一次）。
背景（用户 2026-10-08 亲测，CURRENT_STATE [PLAN-04]）：用户的计划“ai-agent学习”541 个知识点几乎全是 8 分钟（第 1 模块 16 个点正好 128 分钟），是生成时 AI 平均估的，没按内容分量区分。用户要“合理分析，而不是每个知识点都一样”，并先把现有计划重算。
要做
  1. 估算规则（程序算，AI 只标难度）：每个点的基础分钟数由程序按它对应原文的长度（出处段落的字数）和它关联的题目数量算出；AI 只给每个点标“易/中/难”（按模块调用，只回短编号和难度，不写别的），程序乘系数（例如易 0.8、中 1.0、难 1.3，具体数值你定并写理由）。每个点限定在合理区间（例如 3～25 分钟，你定并写理由），按 5 分钟或 1 分钟取整你定。规则写成一个纯函数，参数集中放在一处。
  2. 总量：计划总分钟数保持不变（用户的日期、每天上限、账本日目标不用跟着变），按算出的权重在知识点之间重新分配；模块的学习分钟数随之更新，模块检测分钟数不动。另外在报告里写出“不受总量约束时的估算总量”，让架构师知道原来的总量偏多还是偏少。
  3. 已经学过的点（账本里已有 learn:/relearn: 记录的）分钟数不改，已记的时长不受影响；只重新分配还没学的点。
  4. 新计划：知识点定稿时就按这个规则出分钟数。老计划：第一次打开计划详情时在后台自动重算一次（记一个重算版本号，不重复算），算完推送一个事件让界面刷新（事件名你定，写进回执）；重算失败就保留原分钟数，下次打开再试。跟计划一起同步。
  5. 真实一次：把用户数据复制到产物目录（只复制，不改原数据），在副本上跑完整重算（管理员 DeepSeek 预设，Key 只经现有代码读取）。报告：重算前后每个模块的分钟数；分钟数分布（最小、最大、中位数、各区间个数）；随机抽 15 个点列出标题、原文字数、题数、难度、前后分钟数，自己判断是否合理并写理由；耗时与实际花费。
  6. 离线测试覆盖：规则函数各情况、总量不变、已学的点不改、重算只做一次、失败保留原值、新计划生成走新规则；全量通过、web 构建通过。
完成标准：新老计划的分钟数都按内容分量分配；总量不变；已学的点不受影响；真实一次在副本上跑完并抽检；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix84-minutes\report.md。
回执（尽量短，用表格）：【MF-06｜tools｜第八十四批｜完成/部分完成/受阻】基线 ／ 提交 ／ 规则与参数 ／ 新老计划挂接与事件名 ／ 真实一次（分布、抽检合理数、不受约束总量） ／ 全量 ／ 花费 ／ 本地最终占用
~~~

### [84-MF03] classroom｜学习进度状态：逾期待还/已还、今天进度、超前，一次一种、按严重程度配色（¥0）

~~~text
【MentorFlow 第八十四批｜MF-03（工位 classroom）｜学习进度状态：逾期显示待还和已还、正常显示今天进度、超前显示超前多少，一次只显示一种，颜色按严重程度】

现成项目与许可证（第一节）
  - 只改本项目现有代码，不新增依赖：course/ledger.py（只加状态投影需要的数，不改分配规则）、app/classroom.py 的 today 字段、web/src/pages/Classroom.tsx 顶部、以及其他显示“今天 X / Y 分”的地方（自己搜出来，统一成同一个组件）。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 95fa4107
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。不要结束或重启用户正在用的那个软件进程（从 D:\MentorFlow 主目录启动的）；你自己的测试进程从工位目录启动、用复制的数据。
同时在跑（各改各的，冲突由架构师合并）：83-MF05 软件无声退出（account_shell_main.py、runtime_logging.py、老师窗口）；84-MF06 知识点分钟数重算（planner_agents.py、plan_flow.py 的分钟估算与重算、store.py）；84-MF03 课堂顶部学习进度状态（ledger 状态投影、classroom.py 的 today 字段、Classroom.tsx 顶部、其他显示“今天 X / Y 分”的页面）。84-MF06 不改 ledger 和课堂界面；84-MF03 不改知识点分钟数的来源。
产物目录：D:\MentorFlow\.local\test-tmp\fix84-progress\。目标两小时内交回。花费上限 ¥0（不调模型）。
背景（用户 2026-10-08 亲测，CURRENT_STATE [PLAN-05]）：课堂顶部一直显示“今天 0 / 355 分”，用户讲完 3 个点也不涨。原因：每个点讲完记了暂记时长（3 × 8 分钟），但账本从最早没达标的日子开始填，计划 10-07 开始、那天没学，这 24 分钟先补到了 10-07，“今天”一栏还是 0。分配规则本身不改，改的是显示。用户定的显示方式：
  - 一次只显示一种状态，三种状态三种颜色，按严重程度配色（逾期最醒目，正常中性，超前是好消息的颜色）；
  - 逾期时：显示“逾期待还多少分钟”和“已经还了多少分钟”（今天学的时长里补到过去日子的部分），学完一个点，“已还”马上涨、“待还”马上减；
  - 没有逾期时：显示今天的进度“今天 X / Y 分”，学完一个点马上涨；
  - 超额完成时：显示“超前完成多少分钟”。
要做
  1. 状态投影：用 ledger 现有的 status（debt、ahead、today_target、today_confirmed、today_tentative、严重逾期标记等）算出三种状态和要显示的数；“今天已还”= 今天记的时长中被分配到过去日子的部分（按 credit 的 day 与分配结果算，不改分配规则）。暂记（还没过模块检测的）和已确认的时长都算进去，界面上用浅一点的样式或提示区分暂记部分（沿用现在 today_tentative 的意思）。逾期再细分“轻度”和“严重”（ledger 已有严重逾期判断）两档颜色。
  2. 界面：一个状态组件，课堂顶部和其他显示今天进度的地方都换成它；文字短、一眼能看懂；悬停给一句说明（例如“10-07 没学完的 X 分钟会先从今天学的时长里补”）。学完一个点、过模块检测、下课后都要立即刷新，不用重进课堂。
  3. 亮暗两套主题、窄窗口都好看。截图：逾期（轻度、严重）、正常、超前四种，亮暗各一套；用测试数据造出这四种状态。
  4. 在用户数据副本（复制到产物目录，只读原数据）上算一次，报告用户现在看到的应是哪种状态、各数是多少，并核对数对得上（例如今天已讲 3 个点、暂记 24 分钟）。
  5. 离线测试覆盖三种状态切换、学完一个点后数立即变化、暂记与确认、跨日；全量通过、web 构建通过。
完成标准：三种状态按用户定的方式显示、颜色按严重程度；学完一个点立即变化；副本上的数核对正确；全量通过。
验收：报告和截图写到 D:\MentorFlow\.local\test-tmp\fix84-progress\report.md。
回执（尽量短，用表格）：【MF-03｜classroom｜第八十四批｜完成/部分完成/受阻】基线 ／ 提交 ／ 三种状态与颜色 ／ 已还的算法 ／ 用户副本上的状态与数 ／ 截图 ／ 全量与构建 ／ 本地最终占用
~~~

## 第八十三批任务原文（2026-10-08，基线 fdc1d9ea）

### [83-MF05] platform｜软件静置时无声退出：查清原因、修掉、以后退出都留原因（¥0.5）

~~~text
【MentorFlow 第八十三批｜MF-05（工位 platform）｜软件静置时无声退出：查清原因、修掉，并让以后任何退出都留下原因】

现成项目与许可证（第一节）
  - 只用本项目现有代码和 Python 标准库、PySide6 自带能力，不新增依赖：faulthandler、atexit、signal 是标准库；Qt 的 qInstallMessageHandler、QApplication.aboutToQuit/lastWindowClosed、QWebEnginePage.renderProcessTerminated 是 PySide6 自带；Windows 控制台关闭事件用 ctypes 调 SetConsoleCtrlHandler（标准库 ctypes）。
  - 先看本项目现有日志写法 refactor/runtime_logging.py（diagnostic_event、uncaught 钩子），新诊断一律走它，写进同一个 mentorflow.log。

开工同步
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard fdc1d9ea
提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。
不要结束或重启用户正在用的那个软件进程（它从 D:\MentorFlow 主目录启动）；你自己的测试进程从工位目录启动、用复制的数据。
产物目录：D:\MentorFlow\.local\test-tmp\fix83-exit\。目标三小时内交回（含至少 60 分钟的静置复现）。花费上限 ¥0.5（只为进课堂开场的真实模型调用）。
背景（用户 2026-10-08 亲测，CURRENT_STATE [BUG-58]）：用户在课堂里把软件最小化静置，过一会儿软件整个没了。用户说“以后不能再犯”。架构师已查到的事实：
  - 软件 03:31 由架构师用 Start-Process 从源代码启动（python.exe，带控制台窗口），之前稳定跑了 8 小时。11:40 前后用户进课堂、计划详情触发了 82-MF06 的知识点连线（8 个模块调用 11:43:19 全部完成，之后软件仍在写日志）。
  - mentorflow.log 最后一条是 11:46:34 的后台会话刷新成功（login CONNECTED、refresh RECOVERED），此后再无任何记录；下一次同步本该在约 11:49:52。也就是说 11:46:34～11:50 之间进程消失。
  - 日志里没有任何未捕获异常（runtime_logging 已挂 sys.excepthook 和 threading.excepthook）。
  - Windows 应用日志在这段时间没有 python.exe 的崩溃记录（事件 1000/1001）；而本机崩溃是会被记录的（同一天 05:26、05:41 有别的程序的崩溃记录，异常码 0xc0000006）。所以大概率不是原生崩溃，而是被正常退出路径关掉或被外部结束。
  - 架构师那段时间没有运行任何命令，排除了架构师操作。
  - account_shell_main.py 设了 setQuitOnLastWindowClosed(True)；会话刷新成功时 _hide_login_windows 会隐藏登录相关窗口；第七十八批老师栏有 teacher_open/teacher_close 窗口动作；启动时带了一个控制台窗口，关掉它会直接结束进程且不留记录。
  - 现在软件已改为隐藏控制台、stderr 写到 D:\MentorFlow\.local\test-tmp\app-run\ 重新启动（只读参考，别动）。
要做
  1. 永久诊断（产品代码里，默认开启，写进 mentorflow.log）：
     - 启动时打开 faulthandler，输出到日志目录下单独的 crash 文件（文件一直开着），原生崩溃能留下 Python 栈；
     - 每一条退出路径都记一条“退出”事件并写明原因：aboutToQuit、lastWindowClosed（写明是哪个窗口最后关闭）、主窗口 closeEvent（用户点关闭还是程序关闭）、atexit、Windows 控制台关闭/注销/关机事件（SetConsoleCtrlHandler）；
     - Qt 自身的 warning/critical/fatal 消息接到日志（qInstallMessageHandler）；
     - 每个 QWebEngine 页面（主界面、老师窗口）的渲染进程意外结束要记下状态码；
     - 每 5 分钟记一次本进程内存占用和线程数（一行），用来发现泄漏。
  2. 查原因：读 account_shell_main.py、learning/app/window.py（老师窗口）和所有会关窗口、隐藏窗口、换主窗口的路径，判断哪条路径会在“主窗口最小化、在课堂里、后台会话刷新/同步/连线在跑”时让最后一个窗口关闭或让程序退出。重点看 quitOnLastWindowClosed 与老师窗口、登录窗口、主窗口替换（_replace_learning_home_for_scope、_expire_active_session）之间的关系。
  3. 复现：复制用户数据到产物目录，从工位启动软件（正式接线），进同一份计划的课堂，最小化，静置至少 60 分钟；期间把会话刷新间隔调短（测试参数，不改产品默认）让刷新多跑几十次，并模拟网络时断时续（服务器和模型都断一会儿再恢复）。记录进程是否还活着、日志里的诊断记录。能复现就定位到具体代码行；复现不出来也要把第 2 条里判断“可能”的路径用单元测试或脚本逐条触发，写清每条是否会让程序退出。
  4. 修：不管第 3 条是否复现，都要做到“只有用户明确关闭主窗口（或在菜单里退出）时程序才退出”：其他窗口（老师窗口、登录窗口、设置等）关闭或隐藏都不能让程序退出；主窗口在后台换新时先显示新的再关旧的。找到的具体原因一并修掉。
  5. 架构师和用户以后从源代码启动的方式：给一个仓库里的启动脚本（例如 scripts 下或 refactor/desktop 下现有入口旁），无控制台窗口启动、stderr 写到日志目录，并在回执里写出一行启动命令。
  6. 离线测试覆盖：各退出路径都留下退出原因；其他窗口关闭不退出；主窗口最小化时会话刷新、换主窗口不退出；全量通过、web 构建通过。
完成标准：静置复现跑满 60 分钟并如实报告；第 4 条做到；以后任何一次退出日志里都有原因；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix83-exit\report.md，写清：原因（确定的写“确定”并给证据，推断的写“推断”）、复现过程与结果、修了什么、启动命令。
回执（尽量短，用表格）：【MF-05｜platform｜第八十三批｜完成/部分完成/受阻】基线 ／ 提交 ／ 原因（确定或推断＋证据） ／ 复现（时长、结果） ／ 修复 ／ 永久诊断 ／ 启动命令 ／ 全量 ／ 花费 ／ 本地最终占用
~~~

## 第八十二批任务原文（2026-10-08，基线 52138de0）

### [82-MF06] tools｜知识点连线：DeepSeek 配对前置/相关、程序校验、新老计划自动补（¥1.5）

~~~text
【MentorFlow 第八十二批｜MF-06（工位 tools）｜知识点连线：每份计划都让 DeepSeek 把知识点两两配对成“前置/相关”，程序校验后存下来，老计划打开时自动补上】

现成项目与许可证（第一节）
  - 只改本项目现有代码，不新增依赖：course/planner_agents.py（新增连线提示）、app/plan_flow.py（流程挂接与 detail 字段）、course/store.py（新实体）、app/handlers.py（只加注册行）、connections/gateway.py（只把新用途名加进后台用途）。
  - 先看本项目现成做法再动手：编号别名映射参考 teacher_agent 的 citation_map（AI 只回短编号，程序换回真实 ID）；后台调用、记账、同步实体照现有 question_bank 等实体的写法；查有没有现成的图环检测写法（plan_additions.py 已有模块依赖环检测），能复用就复用。

开工同步
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard 52138de0
提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。
同时在跑（第八十二批共两份，各改各的，冲突由架构师合并）：82-MF06 知识点连线（后端：planner 提示、plan_flow、store、handlers 注册行、gateway 用途名）；82-MF02 知识地图图形化（前端：PlanDetail.tsx 的知识地图区、新组件、store.ts 类型）。两边只通过下面的“约定接口”对接，互不等待；前端用假桥接按约定测，后端用真实桥接测。
约定接口（两份任务原文一致，不得私改；确需改动在回执里写明）：
  - plan.detail 返回里新增两个字段：
      point_links: [{"from": 知识点ID, "to": 知识点ID, "kind": "prerequisite" 或 "related"}]
        prerequisite 表示 from 是 to 的前置（先学 from 再学 to）；related 表示相关、无先后，每对只存一条。
      point_links_status: "none"（还没整理）/"running"（正在整理）/"ready"（完成）/"partial"（部分模块失败）/"failed"（全部失败）
  - 后端整理状态变化时推送事件 plan.point_links_changed，内容 {"plan_id": ..., "status": ...}；前端收到后重新调 plan.detail。
  - 新桥接动作 plan.retry_point_links {"plan_id": ...} → {"status": ...}，只重做失败或缺失的模块。
产物目录：D:\MentorFlow\.local\test-tmp\fix82-links\。目标两小时内交回。花费上限 ¥1.5（只为第 6 条真实一次）。
背景（用户 2026-10-08 定，CURRENT_STATE [MAP-01]）：计划生成时已经花钱找出了知识点，但知识点之间的关系从没让 AI 找过，地图上只能按模块列表摆。用户要每份计划都有一张能看出知识点之间关联的图。这是所有计划的固定流程，不是给某一份计划的特例；用户现有的计划（“ai-agent学习”，JavaGuide，8 个模块约 541 个知识点）也要补上。按 DIR-01，AI 只做“配对”，不写内容。
要做
  1. 连线提示：按模块调用。每次给 AI 本模块全部知识点（短编号、标题、一句话说明），以及它前置模块里的知识点（短编号、标题）；AI 只回 [{"from":"P3","to":"P7","kind":"prerequisite"|"related"}]，不写任何解释性内容。提示里写清：前置是“不先懂 A 就听不懂 B”，相关是“一起学更好懂但没有先后”；宁缺毋滥，不确定就不连。
  2. 程序校验：未知编号、自己连自己、重复的丢掉；related 每对只留一条；prerequisite 出现环时去掉闭合环的那条；每个点最多 3 条前置、3 条相关（多的按 AI 给出的顺序截掉）；跨模块的前置只能指向本模块的前置模块。丢掉的条数分类计数写进日志。
  3. 存储与同步：新实体（例如 point_links）按现有结构类数据写法存，跟计划一起跨设备同步。知识点被删除（删点、删模块）时相关连线一起删；新增模块或知识点（plan_additions）后把受影响模块标成要重整，下次自动补上。不能留下指向不存在知识点的连线。
  4. 流程挂接：新计划在知识点定稿后，在后台自动整理连线（不阻塞用户、不让用户等）；老计划第一次调 plan.detail 时若状态是 none 或有要重整的模块，就在后台自动开始，同一计划同时只跑一份。整理中、完成、失败都按约定推送事件。单个模块失败重试一次，仍失败就记为 partial，其余模块照常完成。用途名新加进 gateway 的后台用途，花费照常记账（81-MF05 的花费统计能看到）。
  5. 按“约定接口”给 plan.detail 加两个字段、加 plan.retry_point_links 动作；不改 detail 已有字段，别让 detail 变慢（只读已存的连线，不在 detail 里调模型）。
  6. 真实一次：把用户数据复制到产物目录（只复制，不改原数据），在副本上用正式接线跑完整的一次补连线（管理员 DeepSeek 预设，Key 只经现有代码读取）。报告：各模块连线条数（前置/相关）、丢弃分类计数、耗时、实际花费；随机抽 20 条连线，写出两端标题，自己判断是否合理并写理由；若明显不合理超过 5 条，改提示后可再跑一次（仍在花费上限内），两次结果都写。
  7. 离线测试覆盖：校验规则各情况、删点删模块后的清理、新增模块后的补整理、失败重试与 partial、同一计划不重复启动、事件推送、detail 字段；全量通过。
完成标准：新计划和老计划都能自动得到连线；约定接口按原样实现；真实一次在用户数据副本上跑完并抽检；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix82-links\report.md（含抽检 20 条原文标题）。
回执（尽量短，用表格）：【MF-06｜tools｜第八十二批｜完成/部分完成/受阻】基线 ／ 提交 ／ 校验规则 ／ 挂接（新计划、老计划） ／ 真实一次（条数、耗时、抽检合理数） ／ 全量 ／ 花费 ／ 本地最终占用
~~~

### [82-MF02] client｜知识地图图形化：路线总览、拉近看知识点网、出处排版（¥0）

~~~text
【MentorFlow 第八十二批｜MF-02（工位 client）｜知识地图图形化：路线总览 + 点模块拉近看知识点网，出处原文按格式排版】

现成项目与许可证（第一节）
  - 用项目已有依赖，不新增：@xyflow/react（React Flow，MIT）画图，@dagrejs/dagre（MIT）自动布局，react-markdown + remark-gfm（MIT）排版出处原文。项目里已有用法可参照：web/src/planning/PlanGraph.tsx（新建计划时的可交互结构图）、web/src/classroom/Markdown.tsx（课堂原文卡片）。能复用组件就复用，不另写一套。
  - 动效用 React Flow 自带的视口动画（fitView/setCenter 带 duration）和 CSS 动画，不另引动画库。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard 52138de0
提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。
同时在跑（第八十二批共两份，各改各的，冲突由架构师合并）：82-MF06 知识点连线（后端：planner 提示、plan_flow、store、handlers 注册行、gateway 用途名）；82-MF02 知识地图图形化（前端：PlanDetail.tsx 的知识地图区、新组件、store.ts 类型）。两边只通过下面的“约定接口”对接，互不等待；前端用假桥接按约定测，后端用真实桥接测。
约定接口（两份任务原文一致，不得私改；确需改动在回执里写明）：
  - plan.detail 返回里新增两个字段：
      point_links: [{"from": 知识点ID, "to": 知识点ID, "kind": "prerequisite" 或 "related"}]
        prerequisite 表示 from 是 to 的前置（先学 from 再学 to）；related 表示相关、无先后，每对只存一条。
      point_links_status: "none"（还没整理）/"running"（正在整理）/"ready"（完成）/"partial"（部分模块失败）/"failed"（全部失败）
  - 后端整理状态变化时推送事件 plan.point_links_changed，内容 {"plan_id": ..., "status": ...}；前端收到后重新调 plan.detail。
  - 新桥接动作 plan.retry_point_links {"plan_id": ...} → {"status": ...}，只重做失败或缺失的模块。
产物目录：D:\MentorFlow\.local\test-tmp\fix82-map\。目标两小时内交回。花费上限 ¥0（不调模型）。
背景（用户 2026-10-08 亲测，CURRENT_STATE [MAP-01]）：计划详情的“知识地图”只是模块列表，点开全是文字，出处原文把 Markdown 源码（表格竖线、横线、代码块）原样摊开，像乱码；用户要一张能直观看清自己学到哪、知识点之间怎么关联的图，要好看、清楚、有动态效果。知识点之间的连线由 82-MF06 并行做，按“约定接口”对接。
要做
  1. 路线总览（默认视图）：最左或中心是计划目标，模块按“先学哪个模块”（模块已有 prerequisites 字段）用 dagre 自动排成路线。模块节点显示标题、状态（已通过/学习中/未学/只学不考）、题数与已过数（沿用 81-MF02 的按题统计）、知识点数与分钟数。学完的亮、未学的偏暗，当前学到的模块有呼吸发光；系统开了“减少动态效果”时关掉动画。
  2. 点模块：视口平滑拉近，展开成这个模块的知识点网。知识点按掌握状态标色（沿用现有绿/黄/红/灰），prerequisite 画带箭头实线、related 画虚线；跨模块的前置画成指向别的模块的小标签，点了跳过去。有“返回总览”和“回到我学到哪”按钮。可拖动、缩放。
  3. 知识点：悬停显示一句话说明；点开右侧面板显示标题、说明、分钟数、状态、出处。出处原文用 Markdown 排版（表格成表格、代码成代码块），默认收起，点开再看。这一条同时修好用户说的“乱码”。
  4. 连线还没好时不能空着、不能白屏：point_links_status 为 none/running 时照常画模块和知识点，只是没有连线，角落显示“正在整理知识点之间的关系…”；收到 plan.point_links_changed 事件后重新读 detail 并把连线淡入；partial/failed 显示一个小的“重试”按钮调 plan.retry_point_links。
  5. 保留现有列表视图作为第二个标签“列表”（可访问、兜底），默认是图。81-MF02 的错误边界、15 秒超时重试、老师预设选择都保留不动。
  6. 性能：总览只画模块，知识点只画当前展开的模块。在用户数据副本（复制到产物目录、只读原数据）上用真实本地桥接测：点“查看路线”到总览图可见、点开最大模块（约 255 个点）到知识点网可见，各自耗时写进报告；目标前者不比 81-MF02 测的 408 毫秒慢太多（1 秒内），后者 1 秒内。达不到要写清卡在哪、试过什么。
  7. 亮色、暗色两套主题和窄窗口都要好看、不溢出。用假桥接按约定接口造“有连线/整理中/部分失败”三种数据测试；截图：总览、拉近后的知识点网（有连线）、整理中、出处展开，亮暗各一套。
  8. 全量测试与 web 构建通过。
完成标准：默认就是图；总览、拉近、知识点面板、出处排版、连线三种状态都按上面做到；用户数据副本上的耗时实测写进报告；全量通过。
验收：报告和截图写到 D:\MentorFlow\.local\test-tmp\fix82-map\report.md。
回执（尽量短，用表格）：【MF-02｜client｜第八十二批｜完成/部分完成/受阻】基线 ／ 提交 ／ 总览与拉近 ／ 出处排版 ／ 连线三种状态 ／ 实测耗时（总览、最大模块） ／ 截图 ／ 全量与构建 ／ 本地最终占用
~~~

## 第八十一批任务原文（2026-10-08，基线 5801844c；第八十批 80-MF02 未发出、并入 81-MF02）

### [81-MF02] client｜计划详情白屏与慢、按题知识地图、错误边界、老师预设选择界面（¥0）

~~~text
【MentorFlow 第八十一批｜MF-02（工位 client）｜计划详情：修好“查看路线”白屏和慢，知识地图按题显示，加错误边界；给“每个计划选老师预设”做界面】

现成项目与许可证（第一节）
  - 只改本项目现有代码：web/src/pages/PlanDetail.tsx、Plans.tsx、页面外框（App/路由所在文件）、必要时后端 app/plan_flow.py 的计划详情接口（只改详情读取那一段）。出错兜底用 React 自带错误边界（React 19 已在用），不新增依赖。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard 5801844c
提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。
同时在跑（第八十一批共七份，各改各的，冲突由架构师合并）：81-MF02 计划详情（PlanDetail.tsx、Plans.tsx、页面外框）；81-MF01 老师预设（设置的学习偏好、计划与老师的绑定、老师提示）；81-MF03 课堂（Classroom.tsx、classroom.py、teacher_agent.py 的输入处理）；81-MF05 用量（Settings.tsx 的模型用量区、connections/balance.py）；81-MF04 语音模型镜像（voice/asr）；81-MF06 结构步排除规则（planner_agents.py 的结构提示）；81-MF07 上传题目自测（新页面与新模块）。Settings.tsx 由 81-MF01 与 81-MF05 改不同区块，handlers.py 只加注册行，各自只动自己那块。
产物目录：D:\MentorFlow\.local\test-tmp\fix81-plan-detail\。目标两小时内交回。花费 ¥0。
背景（用户 2026-10-07 亲测）：在“我的计划”点进行中计划“ai-agent学习”的“查看路线”，转圈不说话，然后整个界面空白，再进“我的计划”也空白；之后又能打开了，但加载很慢。后端日志没有错误，全前端没有错误边界。计划详情可能还按第七十批以前“按知识点等级”的数据结构读字段。知识地图就在这页，用户以为被删了。
要做
  1. 复现与原因：用副本数据在浏览器测试里打开计划详情，拿到前端报错原文与位置；再量打开耗时（前端渲染与后端详情接口各多少毫秒），找出慢的原因并修，目标打开到可见不超过 1 秒（副本数据 541 点）。回执写明原因与修前修后耗时。
  2. 按第七十批后的数据显示：阶段/模块树（知识地图）、每个模块的知识点、模块状态（未学、学习中、检测通过、未通过、只学不考）；掌握情况按题：每个模块“题目 x 道，已过 y 道”，没题的模块标“只学不考”；去掉按知识点等级标色。
  3. 加载：显示“正在读取计划…”的小动画；超过 15 秒显示“读取超时”和“重试”。
  4. 错误边界：页面外框加错误边界，任何一页渲染出错只在该页显示“这一页出错了”和“返回”“重试”（附错误简述），侧栏和其他页照常；有现成写本地日志的桥接就写日志，没有就在回执说明。
  5. 老师预设选择（界面，后端由 81-MF01 做，按下面约定用假桥接开发）：计划详情顶部显示“老师：<预设名>”，点开列出已保存的预设（最多 4 个），选中即生效（下一句老师回复开始用），提示“更换老师预设不用重新备课”。约定：plan.detail 返回 teacher_preset {id, name}；settings.teacher_presets 返回 {presets:[{id,name,...}], default_id}；plan.set_teacher_preset {plan_id, preset_id} 返回新的 teacher_preset。
  6. 浏览器测试：副本数据打开正常、耗时达标；人为让某页抛错只影响该页；超时显示重试；老师预设切换调用正确。截图：计划详情亮暗各一、出错兜底一张。
  7. 全量通过、web 构建通过。
完成标准：“查看路线”正常显示按题的知识地图、1 秒内可见；任何一页出错不再白屏全软件；老师预设选择界面按约定可用；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix81-plan-detail\report.md（附截图）。
回执（尽量短，用表格）：【MF-02｜client｜第八十一批｜完成/部分完成/受阻】基线 ／ 提交 ／ 白屏原因（报错原文）／ 慢的原因与修前修后耗时 ／ 按题显示怎么做 ／ 错误边界与日志 ／ 老师预设选择界面 ／ 全量、web 构建 ／ 截图路径 ／ 本地最终占用
~~~

### [81-MF01] server｜老师预设：最多 4 个可手写人设、计划默认用预设一、随时可换、全链路打通（¥0.2）

~~~text
【MentorFlow 第八十一批｜MF-01（工位 server）｜老师预设：设置里最多 4 个可手写人设的老师预设，计划默认用预设一，每个计划可随时换，全链路打通】

现成项目与许可证（第一节）
  - 只改本项目现有代码：账号设置存储与同步（preferences/ 下现有设置读写与 sync，自己查）、web/src/pages/Settings.tsx 的“学习偏好”区、计划存储（course/store.py 计划记录加一个字段）、新建计划时写入默认预设（app/plan_flow.py 只改创建计划那一处）、课堂老师提示组装（course/teacher_agent.py 读取学习偏好的地方）、app/handlers.py 注册两个桥接动作。不新增依赖。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard 5801844c
提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。
同时在跑（第八十一批共七份，各改各的，冲突由架构师合并）：81-MF02 计划详情（PlanDetail.tsx、Plans.tsx、页面外框）；81-MF01 老师预设（设置的学习偏好、计划与老师的绑定、老师提示）；81-MF03 课堂（Classroom.tsx、classroom.py、teacher_agent.py 的输入处理）；81-MF05 用量（Settings.tsx 的模型用量区、connections/balance.py）；81-MF04 语音模型镜像（voice/asr）；81-MF06 结构步排除规则（planner_agents.py 的结构提示）；81-MF07 上传题目自测（新页面与新模块）。Settings.tsx 由 81-MF01 与 81-MF05 改不同区块，handlers.py 只加注册行，各自只动自己那块。
产物目录：D:\MentorFlow\.local\test-tmp\fix81-teacher-preset\。目标两小时内交回。花费上限 ¥0.2（只为第 6 条真实一次）。
背景（CURRENT_STATE [TEACH-01]“人设”，用户 2026-10-08 定，取代 2026-09-29“不让用户手打”）：现在老师风格（温和/严厉/幽默/沉稳）、讲解详略、举例多少、类比领域是账号级、所有计划共用。用户要：在设置里保存老师预设，可以手写老师人设；最多 4 个槽位（预设一～四）；所有计划默认用预设一（或用户设为默认的那个）；每个计划可以单独选一个预设，随时可换。老师措辞只在讲课当时生成，换预设不需要重新备课。用户特别要求：把整条链路想清楚、打通，不能在某个环节出接线 bug（参考 BUG-56：正式接线与测试接线不一致）。
做法（架构师定）
  1. 预设内容：名称、老师人设（自由文字，最多 2000 字，可空）、老师风格、讲解详略、举例多少、类比领域（沿用现有四项选项）。现有设置迁移成“预设一”（人设文字为空），设为默认。最多 4 个，可新建、改名、编辑、删除、设为默认；默认的那个不能删（先改别的为默认）。删除一个有计划在用的预设：先提示“有 N 个计划在用，删除后它们改用默认预设”，确认后执行。
  2. 存储与同步：预设跟账号设置走（两台电脑同步）；计划记录加 teacher_preset_id，跟计划一起同步；新建计划写入当时的默认预设 id；旧计划没有这个字段时按默认预设处理（不迁移数据）。
  3. 桥接约定（81-MF02 的计划详情界面按这个做）：settings.teacher_presets 返回 {presets:[{id,name,persona,style,detail,examples,analogies}], default_id}；settings.save_teacher_presets 保存整组（校验最多 4 个、名称非空不重复、人设长度）；plan.detail 返回 teacher_preset {id, name}（找不到时用默认）；plan.set_teacher_preset {plan_id, preset_id} 立即生效并返回新的 teacher_preset。
  4. 老师提示：课堂老师、随口对话、档案等用到“学习偏好”的地方，一律改为读“这个计划绑定的预设”；人设文字放在老师提示的固定前缀里（缓存友好：同一计划不换预设时前缀不变）；人设里如果和教学规则冲突（例如要求出题、判分、编造资料），教学规则优先，在提示里写明。闲聊（更多工具 → 和老师闲聊）仍用它自己的人设，不改。
  5. 设置界面：“学习偏好”改成“老师预设”：最多 4 张卡片，每张显示名称、人设摘要、四项选项，可编辑；标出默认；新建按钮满 4 个时置灰并提示“最多 4 个预设”。
  6. 测试：迁移（旧设置变预设一）；增删改、默认、满 4 个；删除在用预设的回退；新计划写入默认；换预设后下一次老师请求用新预设、且同一预设下提示前缀不变；两台设备同步（用现有同步测试方式）；走正式 handlers 注册的接线测试。真实一次：同一个点分别用“温和、无人设”和“自写人设：说话像相声演员”的预设各讲一句，报告两句原话。
  7. 全量通过、web 构建通过。
完成标准：预设可在设置里管理（最多 4 个、可手写人设）；计划默认用默认预设、可随时换且下一句生效；全链路有正式接线测试；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix81-teacher-preset\report.md（附设置截图与真实两句原话）。
回执（尽量短，用表格）：【MF-01｜server｜第八十一批｜完成/部分完成/受阻】基线 ／ 提交 ／ 预设内容与迁移 ／ 存储与同步 ／ 桥接动作 ／ 老师提示怎么用预设、缓存前缀 ／ 设置界面 ／ 真实两句原话 ／ 全量、web 构建 ／ 花费 ／ 本地最终占用
~~~

### [81-MF03] classroom｜课堂一个输入框（去提问开关）、知识点收起改进度条、进课堂提速、中文加粗（¥0.5）

~~~text
【MentorFlow 第八十一批｜MF-03（工位 classroom）｜课堂：去掉“提问”开关只留一个聪明的输入框；知识点标签默认收起、改成本模块进度条；进课堂太慢查清修好；加粗紧挨中文标点显示星号】

现成项目与许可证（第一节）
  - 只改本项目现有代码：web/src/pages/Classroom.tsx（输入区、顶部知识点区、加载）、web/src/classroom/ 下相关组件、app/classroom.py（reply/ask 处理与进课堂流程）、course/teacher_agent.py（回应学生的提示）。中文加粗修复优先用现成的 remark-cjk-friendly（MIT，remark 插件，项目已用 react-markdown/remark-gfm）；如果本机无法安装新的前端依赖，就写一个几十行的预处理（在中文标点与 ** 之间按 CommonMark 规则处理），回执写明用了哪种。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 5801844c
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。
同时在跑（第八十一批共七份，各改各的，冲突由架构师合并）：81-MF02 计划详情（PlanDetail.tsx、Plans.tsx、页面外框）；81-MF01 老师预设（设置的学习偏好、计划与老师的绑定、老师提示）；81-MF03 课堂（Classroom.tsx、classroom.py、teacher_agent.py 的输入处理）；81-MF05 用量（Settings.tsx 的模型用量区、connections/balance.py）；81-MF04 语音模型镜像（voice/asr）；81-MF06 结构步排除规则（planner_agents.py 的结构提示）；81-MF07 上传题目自测（新页面与新模块）。Settings.tsx 由 81-MF01 与 81-MF05 改不同区块，handlers.py 只加注册行，各自只动自己那块。
产物目录：D:\MentorFlow\.local\test-tmp\fix81-classroom\。目标两小时内交回。花费上限 ¥0.5（只为第 5 条真实验证）。
背景（用户 2026-10-08 定）：①输入区左边的“提问”开关去掉，用户认为让用户自己切模式“显得 AI 像猪一样”；保留下面三个按钮“懂了，继续”“没太懂”“我想问…”。②顶部 16 个知识点标签常驻挡视野，要默认收起、点开再看；用户看不出自己完成了多少，要显示当前模块的进度（进度条）。③进课堂明显变慢（点进入要等很久），不是加个加载动画能糊弄过去的，要查清原因修好，同时有个小动画。④老师回复里“**先分清两种单位：**线程…”这种加粗紧挨中文标点时显示成星号。
做法（架构师定）
  1. 一个输入框：讲课时学生打的任何话都走同一个处理——老师自己判断是在回答她刚才的问题，还是新问题/没懂的地方：是回答就按“理解对了简单肯定、偏了对照原文说明、口误不纠缠”回应；是问题就依据本点和本计划已采用资料解答（没依据就直说资料里没讲到），答完回到原来位置接着讲；都不打分。合并现有 classroom.reply 与 classroom.ask 在讲课时的处理（一次老师调用完成，不额外调用模型做分类）。
  2. 检测题在屏幕上时：输入框里打的字就是作答（客观题）；问答题照旧用问答卡片。点“我想问…”：输入框上方出现一个小标签“这句是提问 · 取消”，下一句当提问发送（不判分、不算作答），发完自动取消。去掉左边“提问/正在提问”按钮。“懂了，继续”“没太懂”行为不变。
  3. 顶部：知识点标签默认收起；收起时显示“第 N 个模块 · 本模块 x / y 个点已讲”和一条进度条，旁边“展开知识点”；展开后显示原来的标签（当前点高亮、已讲的打勾）。今天分钟数照旧在右上角。
  4. 进课堂速度：用副本数据（计划“ai-agent学习”，541 点、8 模块已备题）量从点“进入课堂”到课堂可见的耗时，拆分前端与后端各步骤（读取计划、课程装载、备题状态检查、掌握情况计算等）的毫秒数，找出慢的原因并修，目标 1 秒内可见；同时加一个简短的加载小动画和文字（“正在进入课堂…”）。回执给修前修后的耗时表。
  5. 真实验证（管理员 DeepSeek 预设，Key 只经现有代码从 Windows 凭据读取；走正式 handlers 接线）：在副本上讲课时分别输入一句“回答老师的问题”、一句“新问题”、一句“口误”，检测题时用“我想问…”提问一次再作答一次，报告老师的原话，确认判断正确、提问不被判分。
  6. 中文加粗：老师回复与原文卡片里“**…：**…”“**…。**”之类都正确显示为加粗，加测试覆盖几种中文标点。
  7. 全量通过、web 构建通过、离线完整场景（--to free）通过。
完成标准：没有“提问”开关、一个输入框老师能分辨回答和提问；知识点默认收起并显示本模块进度；进课堂 1 秒内可见；中文加粗正常；全量、构建、离线完整场景通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix81-classroom\report.md（附截图、耗时表、老师原话）。
回执（尽量短，用表格）：【MF-03｜classroom｜第八十一批｜完成/部分完成/受阻】基线 ／ 提交 ／ 输入框怎么合并、“我想问…”怎么用 ／ 顶部进度怎么显示 ／ 进课堂慢的原因与修前修后耗时 ／ 真实验证老师原话 ／ 中文加粗用了哪种办法 ／ 全量、web 构建、离线完整场景 ／ 花费 ／ 本地最终占用
~~~

### [81-MF05] platform｜模型用量按余额变化显示今天/本周/本月花费（¥0）

~~~text
【MentorFlow 第八十一批｜MF-05（工位 platform）｜“模型用量”显示花了多少钱：按账户余额的变化算出今天、本周、本月各用了多少】

现成项目与许可证（第一节）
  - 只改本项目现有代码：refactor/learning/connections/balance.py（现有查余额，DeepSeek 官方余额接口）、本地存储（照现有 connections 的账号目录存一张小表）、web/src/pages/Settings.tsx 的“模型用量”区与“已保存的预设”里的余额列、app/handlers.py 只加注册行。不新增依赖。

开工同步
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard 5801844c
提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。
同时在跑（第八十一批共七份，各改各的，冲突由架构师合并）：81-MF02 计划详情（PlanDetail.tsx、Plans.tsx、页面外框）；81-MF01 老师预设（设置的学习偏好、计划与老师的绑定、老师提示）；81-MF03 课堂（Classroom.tsx、classroom.py、teacher_agent.py 的输入处理）；81-MF05 用量（Settings.tsx 的模型用量区、connections/balance.py）；81-MF04 语音模型镜像（voice/asr）；81-MF06 结构步排除规则（planner_agents.py 的结构提示）；81-MF07 上传题目自测（新页面与新模块）。Settings.tsx 由 81-MF01 与 81-MF05 改不同区块，handlers.py 只加注册行，各自只动自己那块。
产物目录：D:\MentorFlow\.local\test-tmp\fix81-spend\。目标一个半小时内交回。花费 ¥0（查余额接口不收费，可真实调用核对）。
背景（用户 2026-10-08 定）：用户要在软件里看到“花了多少钱”，不是“余额还有多少”。DeepSeek 只提供查余额的接口、没有查消费的接口，用户定按余额的变化来算。
做法（架构师定）
  1. 记录余额：软件运行时每小时查一次余额，另外在每次课堂结束（下课）、建计划生成结束、备题结束后各查一次；每次把（时间、余额、币种）记进本地小表（按当前模型预设分开记）。
  2. 算花费：相邻两次记录之间余额下降的部分算作花费；余额上升（充值）不算花费、也不抵消；按本地日期汇总出今天、本周、本月花了多少，以及最近 14 天每天各花多少。
  3. 显示：“模型用量”最上面三张数字卡“今天花了 ¥x / 本周 ¥y / 本月 ¥z”，下面一行小字“按账户余额变化计算；如果这把 Key 也在别处使用，那部分也会算进来”；再下面最近 14 天的小柱状图；原有的 token 与缓存命中率统计保留在下方。“已保存的预设”那一栏把“账户余额”改成“今天花了 ¥x”，余额放到悬停提示里。
  4. 边界：刚开始用、只有一次记录时显示“刚开始记录，下次查询后显示花费”；查询失败不影响其他功能，下一次再查。
  5. 测试：用假余额序列验证花费计算（含充值、跨天、跨周、只有一条记录）；真实查一次余额确认记录写入；界面浏览器测试与截图（亮暗各一）。
  6. 全量通过、web 构建通过。
完成标准：用量页显示今天、本周、本月花费与 14 天柱状图；充值不算花费；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix81-spend\report.md（附截图）。
回执（尽量短，用表格）：【MF-05｜platform｜第八十一批｜完成/部分完成/受阻】基线 ／ 提交 ／ 什么时候查余额、怎么存 ／ 花费怎么算（充值、跨天）／ 界面显示 ／ 全量、web 构建 ／ 截图路径 ／ 本地最终占用
~~~

### [81-MF04] runtime｜语音模型国内镜像优先、失败回落 GitHub（¥0）

~~~text
【MentorFlow 第八十一批｜MF-04（工位 runtime）｜语音识别模型改从国内镜像下载：先国内、失败再 GitHub，文件校验不变】

现成项目与许可证（第一节）
  - 只改本项目现有代码：refactor/learning/voice/asr/model_manager.py 与 voice/download.py（已有断点续传）。模型本身是 k2-fsa/sherpa-onnx 发布的 SenseVoice 与 Silero VAD（Apache-2.0）。不新增依赖。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard 5801844c
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。
同时在跑（第八十一批共七份，各改各的，冲突由架构师合并）：81-MF02 计划详情（PlanDetail.tsx、Plans.tsx、页面外框）；81-MF01 老师预设（设置的学习偏好、计划与老师的绑定、老师提示）；81-MF03 课堂（Classroom.tsx、classroom.py、teacher_agent.py 的输入处理）；81-MF05 用量（Settings.tsx 的模型用量区、connections/balance.py）；81-MF04 语音模型镜像（voice/asr）；81-MF06 结构步排除规则（planner_agents.py 的结构提示）；81-MF07 上传题目自测（新页面与新模块）。Settings.tsx 由 81-MF01 与 81-MF05 改不同区块，handlers.py 只加注册行，各自只动自己那块。
产物目录：D:\MentorFlow\.local\test-tmp\fix81-asr-mirror\。目标一小时内交回。花费 ¥0。
背景（用户 2026-10-07）：语音识别模型（约 156 MB）现在从 GitHub 发布页下载，国内很慢；用户要放到国内镜像，认为不用花大精力。
要做
  1. 查找国内能直接下载同一份文件的官方或可信镜像（优先 ModelScope 上 k2-fsa/sherpa-onnx 官方或 SenseVoice 官方发布的同名文件；其次其他可信镜像），用 SHA256 核对与现在 GitHub 那份完全相同才算可用，回执列出地址与核对结果。
  2. 下载顺序改为：国内镜像优先，失败（连不上、校验不对、30 秒无数据）再自动换 GitHub；断点续传照旧；下载窗口显示当前来源（例如“来源：ModelScope”）。
  3. 找不到完全相同的国内文件时，不擅自换模型版本，回执写清楚并给出可选办法（例如放到我们自己服务器的更新静态目录），不动服务器。
  4. 本机实测：用临时目录真实下载一次（不碰用户已下载好的模型），记录两个来源各自的平均速度。
  5. 全量通过。
完成标准：有完全相同的国内来源时默认用它、失败自动回落 GitHub；没有就如实报告；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix81-asr-mirror\report.md。
回执（尽量短，用表格）：【MF-04｜runtime｜第八十一批｜完成/部分完成/受阻】基线 ／ 提交 ／ 国内来源与 SHA256 核对 ／ 下载顺序与回落 ／ 实测速度 ／ 全量 ／ 本地最终占用
~~~

### [81-MF06] tools｜结构步只排除和学习无关的内容（¥0.3）

~~~text
【MentorFlow 第八十一批｜MF-06（工位 tools）｜结构这一步只排除和学习无关的内容：学习路线、求职、面试准备、项目表达都保留为知识模块】

现成项目与许可证（第一节）
  - 只改本项目现有代码：course/planner_agents.py 的结构提示（MATERIAL_STRUCTURE_SYSTEM 里 67-MF01 加的排除规则）与相应测试。不新增依赖。

开工同步
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard 5801844c
提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。
同时在跑（第八十一批共七份，各改各的，冲突由架构师合并）：81-MF02 计划详情（PlanDetail.tsx、Plans.tsx、页面外框）；81-MF01 老师预设（设置的学习偏好、计划与老师的绑定、老师提示）；81-MF03 课堂（Classroom.tsx、classroom.py、teacher_agent.py 的输入处理）；81-MF05 用量（Settings.tsx 的模型用量区、connections/balance.py）；81-MF04 语音模型镜像（voice/asr）；81-MF06 结构步排除规则（planner_agents.py 的结构提示）；81-MF07 上传题目自测（新页面与新模块）。Settings.tsx 由 81-MF01 与 81-MF05 改不同区块，handlers.py 只加注册行，各自只动自己那块。
产物目录：D:\MentorFlow\.local\test-tmp\fix81-exclude\。目标一小时内交回。花费上限 ¥0.3（只为第 3 条真实一次）。
背景（用户 2026-10-07 定，CURRENT_STATE [DIR-01]“资料导航与求职”讨论）：67-MF01 让结构这一步把导航、学习路线、求职、简历、面试心态与流程都排除。用户后来认为求职、面试准备在面试类目标里正是要学的内容，没题的模块只学不考即可；只该排除和学习无关的东西。
要做
  1. 排除规则改为只排除：如何使用本资料、目录说明、关于作者、推广与赞助、资料本身的常见问答（有没有 PDF、能不能转载、怎么贡献）。学习路线、求职、简历、面试准备、项目表达、面试心态与流程都当知识内容保留（资料里没题就只学不考）。
  2. 离线测试覆盖上面两类各几个标题。
  3. 真实一次：复制用户草稿（JavaGuide）跑一次结构这一步，报告模块列表与 excluded 列表，确认求职、面试准备在模块里，“关于作者”“有没有 PDF”之类在排除里。管理员 DeepSeek 预设，Key 只经现有代码读取。
  4. 全量通过。
完成标准：排除规则按上面改；真实一次结果符合；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix81-exclude\report.md。
回执（尽量短，用表格）：【MF-06｜tools｜第八十一批｜完成/部分完成/受阻】基线 ／ 提交 ／ 排除规则 ／ 真实一次的模块与排除列表 ／ 全量 ／ 花费 ／ 本地最终占用
~~~

### [81-MF07] library｜上传题目自测新功能（¥0.5）

~~~text
【MentorFlow 第八十一批｜MF-07（工位 library）｜新功能“上传题目自测”：用户上传自己的题，程序带着测，每道题连续答对 N 遍才过】

现成项目与许可证（第一节）
  - 复用本项目第七十批的做法与代码：拆题（course/material_questions.py：问题式标题与带答案习题识别、AI 只按原文拆字段、程序逐字核对）、要点（71-MF01：程序把参考答案切成单位、AI 只选编号）、判分（course/answer_compare.py）、问答题界面（web/src/classroom/ 下 RecallQuestion 组件）。资料导入沿用现有导入（PDF/Word/网页/文本，course/ 下现有导入）。新建 course/self_test.py 与 web/src/pages/SelfTest.tsx，在“更多工具”加入口；app/handlers.py 只加注册行。不新增依赖。

开工同步
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard 5801844c
提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。
同时在跑（第八十一批共七份，各改各的，冲突由架构师合并）：81-MF02 计划详情（PlanDetail.tsx、Plans.tsx、页面外框）；81-MF01 老师预设（设置的学习偏好、计划与老师的绑定、老师提示）；81-MF03 课堂（Classroom.tsx、classroom.py、teacher_agent.py 的输入处理）；81-MF05 用量（Settings.tsx 的模型用量区、connections/balance.py）；81-MF04 语音模型镜像（voice/asr）；81-MF06 结构步排除规则（planner_agents.py 的结构提示）；81-MF07 上传题目自测（新页面与新模块）。Settings.tsx 由 81-MF01 与 81-MF05 改不同区块，handlers.py 只加注册行，各自只动自己那块。
产物目录：D:\MentorFlow\.local\test-tmp\fix81-selftest\。目标三小时内交回。花费上限 ¥0.5（只为第 6 条真实一次）。
背景（CURRENT_STATE [DIR-01]“题目来源与考核”细则⑥，用户 2026-10-06 定）：独立小功能，和计划无关。用户上传自己的题（格式不限，AI 能读就行），AI 只按原文把题、选项、答案拆成我们的格式，不改写、不补答案；程序带着测；客观题程序判，问答题先写回答、再看参考答案、逐条勾要点、全部勾到算对；没有模块检测和最终检验；每道题要连续答对几遍由用户选（默认 2 遍，可选 3 遍），没过的题滚动着再出；没有答案的题列出来告诉用户、不进自测，补上答案再加。入口在“更多工具”。
要做
  1. 导入：上传文件（PDF、Word、图片以外的常见文本格式）或粘贴文字 → 拆题 → 显示“识别出 N 道题（客观 a、问答 b），另有 c 道没有答案未加入”，可展开查看每道题与没答案的清单；用户可删掉个别题、给题集命名。
  2. 题集：可保存多个题集（存在本机账号目录，跟随现有同步机制则一并同步，回执说明是否同步），可重命名、删除。
  3. 自测：开始前选“每道题连续答对 2 遍 / 3 遍”；出题顺序打乱，同一道题不紧挨着连考（中间至少隔 2 道别的，题少时尽量拉开）；答错的题连续计数清零、之后再出；全部达标显示完成与用时、每题答对答错次数；中途退出可接着做。
  4. 判分：客观题用 answer_compare；问答题用现有问答卡片流程（写回答→看答案→勾要点，全勾算对）。
  5. 离线测试：拆题（含没有答案的题）、连续 N 遍规则、同题隔开、中途退出续做；浏览器测试与截图（导入结果、自测中、完成，亮暗各一）。
  6. 真实一次：用一份带答案的练习（自己从公开教材习题里找一份短的，或用 JavaGuide 里的几道问答）导入并做完一轮，报告识别结果与花费。管理员 DeepSeek 预设，Key 只经现有代码读取。
  7. 全量通过、web 构建通过。
完成标准：能上传或粘贴题目、按原文拆题、没答案的题单列；能按 2/3 遍规则带着测完；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix81-selftest\report.md（附截图）。
回执（尽量短，用表格）：【MF-07｜library｜第八十一批｜完成/部分完成/受阻】基线 ／ 提交 ／ 导入与拆题 ／ 题集保存与同步 ／ 自测规则 ／ 真实一次结果 ／ 全量、web 构建 ／ 花费 ／ 截图路径 ／ 本地最终占用
~~~

## 第八十批任务原文（2026-10-07，基线 06c356af）

### [80-MF02] client｜“查看路线”白屏：修好计划详情，加错误边界

~~~text
【MentorFlow 第八十批｜MF-02（工位 client）｜“我的计划 → 查看路线”一点整个软件变空白、回到“我的计划”也什么都不显示：查清修好，并让任何一页出错都不再拖垮整个软件】

现成项目与许可证（第一节）
  - 只改本项目现有代码：web/src/pages/PlanDetail.tsx、Plans.tsx、页面外框（App/路由所在文件，自己查）、必要时后端 app/plan_flow.py 的计划详情接口（plan.detail 一类）。出错兜底用 React 自带的错误边界（componentDidCatch / getDerivedStateFromError，React 19 已在用），不新增依赖。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard 06c356af
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户的 D:\MentorFlowData\account-shell 只读：把账号数据（含计划“ai-agent学习”，已开课、第七十批起按题考核）复制到测试目录，只在副本上复现。先读 AGENTS.md（磁盘规则）：所有产物在 D:\MentorFlow\.local\test-tmp\fix80-plan-detail\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
用户在中测试、这是当前最影响使用的问题，目标一小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建由你跑并如实写结果。花费 ¥0。
背景（用户 2026-10-07 亲测）：在“我的计划”里点进行中计划“ai-agent学习”的“查看路线”，一直转圈不说话，然后整个界面什么都看不见；再进“我的计划”也什么都不显示。后端日志里这段时间没有任何错误（D:\MentorFlowData\account-shell\logs\mentorflow.log，最后一条错误是 14:35 UTC 的 BUG-56），所以多半是前端渲染时抛了异常：整个前端没有任何错误边界，一处出错整页白屏。可能原因：计划详情页（MAP-01 的模块树、掌握状态标色、出错次数）还按第七十批以前“按知识点出题、等级”的数据结构读字段，第七十批改成按题考核后对不上。知识地图就在这个“查看路线”页里，用户以为被删了。
要做
  1. 复现：用副本数据在浏览器测试里打开计划详情，拿到前端报错原文与出错位置，回执写明原因。
  2. 修好计划详情：按第七十批后的数据结构显示——阶段/模块树（知识地图）、每个模块的知识点、模块状态（未学、学习中、检测通过、未通过、只学不考），掌握情况改成按题：每个模块“题目 x 道，已过 y 道”，没题的模块标“只学不考”；原来按知识点等级标色的部分改为按题或去掉，回执写明怎么改的。
  3. 加载：打开计划详情时显示“正在读取计划…”；超过 15 秒还没结果就显示“读取超时”和“重试”按钮，不无声转圈。
  4. 错误边界：在页面外框加错误边界——任何一页渲染出错，只在该页位置显示“这一页出错了”与“返回”“重试”按钮（附错误简述），侧栏和其他页照常可用；出错信息写进本地日志（经现有桥接写 Data\logs，若没有现成写日志的桥接，回执说明，先只在页面显示）。
  5. 浏览器测试：用副本数据打开计划详情正常显示知识地图与按题的掌握情况；人为让某页抛错时只影响该页；加载超时显示重试。截图：计划详情（亮暗各一）、出错兜底一张。
  6. 全量通过、web 构建通过。
完成标准：副本数据上“查看路线”正常显示知识地图；任何一页出错不再白屏整个软件；加载有提示与超时；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix80-plan-detail\report.md（附截图）。
回执（尽量短，用表格）：【MF-02｜client｜第八十批｜完成/部分完成/受阻】基线 ／ 提交 ／ 白屏的原因（报错原文）／ 计划详情怎么改成按题 ／ 加载提示与超时 ／ 错误边界与日志 ／ 全量、web 构建 ／ 截图路径 ／ 本地最终占用
~~~

## 第七十九批任务原文（2026-10-07，基线 d9f920b0）

### [79-MF02] client｜原文卡片默认折叠、同资料合并；细边箭头朝右

~~~text
【MentorFlow 第七十九批｜MF-02（工位 client）｜课堂小修：原文卡片默认折叠、同一资料的多处合成一条；收起细边的箭头改成朝右】

现成项目与许可证（第一节）
  - 只改本项目现有前端：web/src/pages/Classroom.tsx 与 web/src/classroom/ 下显示老师回复与原文卡片的组件及样式、老师收起细边（web/src/teacher/ 或 Classroom.tsx 里 78 批做的那部分）。不新增依赖。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard d9f920b0
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。D:\MentorFlowData\account-shell 只读（用户正在用）。先读 AGENTS.md（磁盘规则）：所有产物在 D:\MentorFlow\.local\test-tmp\fix79-sources\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。
目标一小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建由你跑并如实写结果。花费 ¥0。
背景（用户 2026-10-07 中测试亲测）：课堂整体体验用户认为还行（老师会标重点、会对话式提问，随口问不出选项也更好）。两处要改：①老师每次回复下面都把原文卡片整块展开（截图里同一份 JavaGuide 连着三张卡片，只是位置不同），占地方；用户要默认折叠，想看时再点开；②老师栏收起时的细边上箭头朝左，现在老师是往外（向右）展开的，箭头应朝右。
要做
  1. 原文卡片：每条老师回复下面只显示一行折叠的“原文出处（N 处）”，点开再列出各处原文；同一份资料的多处合并在同一资料名下，按出处位置列出，不重复资料标题；点开后每处可再单独展开/收起全文；展开状态只在当前会话里记住，不影响后续回复（新回复默认仍折叠）。老师正文里的出处标记（如 〔3〕）点一下，自动展开并滚动到对应那一处。
  2. 收起细边：箭头图标改为朝右（表示向右展开），展开后的“收起”按钮图标朝左；悬停提示文字不变。
  3. 浏览器测试：默认折叠、点开列出、同资料合并、点正文出处标记跳到对应处、新回复默认折叠；亮暗各截一张（折叠、展开）。
  4. 全量通过、web 构建通过。
完成标准：原文默认折叠且同资料合并；出处标记可跳转展开；箭头方向正确；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix79-sources\report.md（附截图）。
回执（尽量短，用表格）：【MF-02｜client｜第七十九批｜完成/部分完成/受阻】基线 ／ 提交 ／ 原文卡片怎么折叠与合并 ／ 出处标记跳转 ／ 箭头 ／ 全量、web 构建 ／ 截图路径 ／ 本地最终占用
~~~

## 第七十八批任务原文（2026-10-07，基线 9cf0752c）

### [78-MF02] client｜老师栏往外展开（窗口变宽）、背景做好看

~~~text
【MentorFlow 第七十八批｜MF-02（工位 client）｜老师栏“往外展开”：展开时软件窗口向右变宽、对话区宽度不变；老师栏背景做得好看，不再空荡荡】

现成项目与许可证（第一节）
  - 只改本项目现有代码：窗口 refactor/learning/app/window.py（已有 window_action，经 app.window 桥接调用，handlers.py 第 74–82 行）、前端 web/src/pages/Classroom.tsx、web/src/teacher/TeacherStage.tsx、teacher-stage.css。窗口尺寸用 Qt 自带的 QWidget/QScreen 几何接口（PySide6，LGPL，已在用）。背景用 CSS 渐变与内联 SVG 自己画，可参考登录窗口已有的“夜樱”手绘背景的配色与构图（be04d80a 那次提交的登录背景），不引入外部图片、不新增依赖。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard 9cf0752c
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。D:\MentorFlowData\account-shell 只读（用户正在用）。先读 AGENTS.md（磁盘规则）：所有产物在 D:\MentorFlow\.local\test-tmp\fix78-teacher-out\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。
用户在中测试，目标一个半小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建由你跑并如实写结果。花费 ¥0。
背景（CURRENT_STATE [UI-00]，用户 2026-10-07 亲测）：77-MF02 的老师栏展开时是“向内”占课堂里的位置，对话区又被挤窄了。用户要的是“往外展开”：展开老师时软件窗口本身向右变宽，对话区宽度保持不变；收起时窗口缩回原宽。另外老师栏背景太空，不好看。
做法（架构师定）
  1. 往外展开（窗口层）：给 app.window 加两个动作：teacher_open（窗口宽度加上老师栏宽度）、teacher_close（减回去）。规则：
     - 普通窗口：向右加宽；右边屏幕不够时整体左移，仍保持对话区宽度不变；左移后也放不下（屏幕太小）才退回“向内展开”。
     - 最大化或全屏时窗口不能再变宽：老师栏向内展开（和 77 一样），这是唯一允许挤窄对话区的情况，回执写明。
     - 收起时恢复展开前的窗口位置和宽度（用户中途手动改过窗口大小时，按收起那一刻的宽度减去老师栏宽度）。
     - 老师栏拖宽时窗口同步变宽，对话区不变。软件关掉再开、老师栏是展开状态时，窗口按“对话区宽度＋老师栏宽度”恢复。
  2. 前端：老师栏展开/收起、拖宽时调用上面的动作；收起的细竖条保留 77 的样子。
  3. 背景：老师栏换成有层次的背景，跟主题走——樱花主题：柔和粉白渐变、远处淡淡的樱枝剪影和少量花瓣（静态为主，可很慢地飘几片，遵守“减少动态效果”）；极光夜主题：深紫夜色渐变、淡淡极光带与星点；两种主题都在模型脚下加一圈柔和的地面光晕与阴影，让人物“站”在那里；栏边缘与课堂卡片同样的圆角与描边。不要抢人物：背景饱和度低、对比度低。
  4. 测试：Qt 侧用离线测试验证三种情况（普通窗口向右加宽、右边不够时左移、最大化时向内）与收起恢复；浏览器测试验证展开收起调用了正确的动作。截图：普通窗口收起与展开各一张（看得出窗口变宽、对话区等宽），亮暗两种主题的老师栏背景各一张。
  5. 全量通过、web 构建通过。
完成标准：普通窗口下展开老师时对话区宽度不变、窗口向右变宽，收起复原；最大化时才向内；背景按主题有层次；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix78-teacher-out\report.md（附截图）。
回执（尽量短，用表格）：【MF-02｜client｜第七十八批｜完成/部分完成/受阻】基线 ／ 提交 ／ 往外展开怎么做、三种情况各怎样 ／ 收起与重开的恢复 ／ 背景做了什么 ／ 全量、web 构建 ／ 截图路径 ／ 本地最终占用
~~~

## 第七十七批任务原文（2026-10-07，基线 130dea0d）

### [77-MF03] classroom｜课堂真实走一遍并修好所有卡住的地方

~~~text
【MentorFlow 第七十七批｜MF-03（工位 classroom）｜课堂真实走一遍并修好所有卡住的地方：用户副本上从“开始上课”到第 3 模块的模块检测，全程真实模型、走正式软件的接线】

现成项目与许可证（第一节）
  - 只改本项目现有代码：app/classroom.py、app/handlers.py（课堂接线）、course/teacher_agent.py（老师回复解析与提示）、必要时 web/src/pages/Classroom.tsx 的出错显示。不新增依赖。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 130dea0d
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户的 D:\MentorFlowData\account-shell 只读：把计划“ai-agent学习”（已开课，8 个模块题已备好）连同账号数据复制到测试目录，只在副本上跑。先读 AGENTS.md（磁盘规则、功能先行）：所有产物在 D:\MentorFlow\.local\test-tmp\fix77-classroom\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
同时在跑：77-MF02（client，只改老师形象面板与动作，你不动 web/src/teacher/ 和课堂右侧老师相关代码）。
用户在中测试、正卡在课堂里，目标两小时内交回。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建、离线完整场景由你跑并如实写结果。
背景（用户 2026-10-07 亲测，认为现状“明显在糊弄人”）：第七十批以后课堂改了很多（按题考核、问答题勾要点、随口问改对话），但 71-MF01 联调时课堂讲解用的是假老师回放，正式软件里从没真实走过。用户亲测连续卡住：①进课堂报 TypeError（BUG-56，已修：handlers 给课堂传了要带设置参数的日期函数）；②点“开始上课”报“opening: 需要2～3个开场选项”——course/teacher_agent.py parse_teaching 要求开场回复末尾必须有【选项】…【/选项】，模型没按格式写就整条拒掉、课没法开始。
要做
  1. 先查清②：从用户日志 D:\MentorFlowData\account-shell\logs\mentorflow.log 和副本里找到那次开场调用的提示与模型原文（只读），写明模型实际输出了什么、为什么没带选项（例如 70-MF03 改老师提示后开场要求被冲淡）。修法：开场选项缺失或格式不对时，老师的讲解照常显示，选项改用程序给的固定两个（“直接开始讲”“我先说说我知道的”），不报错、不拦住上课；提示里把开场选项的格式要求写清楚放在最后。其他老师回复格式问题按同一原则：能显示就显示，不因格式拒掉整条回复。
  2. 走正式接线真实跑（不要用模拟工具里的假老师；用 app/handlers.py 注册出来的桥接调用课堂动作，日期函数也用正式那个）：在副本上从第 1 模块“开始上课”开讲 2～3 个点（含一次学生插话提问、一次老师对话式提问后学生回答）；用“我会这部分，直接考”或学完推进到第 3 模块“AI 大模型、Agent 与 RAG 核心”（有题）；讲 1～2 个点后做模块检测：至少一道问答题按“写回答 → 看答案 → 勾要点”提交（一次全勾、一次漏勾），确认按题判、≥90% 才过、只重测错题；中途关掉再进一次确认能接上。管理员 DeepSeek 预设，Key 只经现有代码从 Windows 凭据读取。
  3. 跑的过程中遇到任何报错、卡住、显示不对，都当场按“能继续就继续、不拦用户”的原则修，修完从卡住处接着跑；每个问题记一行（现象、原因、怎么修、怎么验证）。
  4. 加一条测试走正式 handlers 注册的课堂接线（防止 BUG-56 这类再出现），再加开场选项缺失时照常显示的测试。
  5. 全量通过、web 构建通过、离线完整场景（--to free）通过。
花费：上限 ¥2（价格按高峰折算）。
完成标准：副本上真实从开课走到第 3 模块模块检测完成（含问答题勾要点、关掉重进），过程中不再出现拦住上课的报错；发现的问题都修了并逐条记录；全量、构建、离线完整场景通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix77-classroom\report.md（附老师原话摘录与问题清单）。
回执（尽量短，用表格）：【MF-03｜classroom｜第七十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 开场选项问题的原因与修法 ／ 真实走到哪一步、每步结果 ／ 途中发现并修好的问题（每条一句）／ 新增的正式接线测试 ／ 全量、web 构建、离线完整场景 ／ 花费 ／ 本地最终占用
~~~

### [77-MF02] client｜老师改成右侧整页高的抽屉，用上模型自带动作

~~~text
【MentorFlow 第七十七批｜MF-02（工位 client）｜课堂老师改成右侧整页高的抽屉：收起时只留一条细边，展开往右侧滑出与页面同高；用上模型自带的动作和表情】

现成项目与许可证（第一节）
  - 只改本项目现有前端：refactor/learning/web/src/pages/Classroom.tsx（老师入口与布局）、web/src/teacher/TeacherStage.tsx、teacher-stage.css、teacherRuntime.ts（pixi.js 8.13.1 MIT、untitled-pixi-live2d-engine 1.3.5 已安装，用它现有的 motion / expression 接口）。模型与动作文件只读（在用户配置的老师资源目录里，路径见 app/live2d_assets.py 的设置项 live2d/asset_root）。不新增依赖。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard 130dea0d
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西（只读使用模型资源，不复制、不修改）。D:\MentorFlowApp 只读。D:\MentorFlowData\account-shell 只读。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则）：所有产物在 D:\MentorFlow\.local\test-tmp\fix77-teacher\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。
同时在跑：77-MF03（classroom，改课堂流程与老师回复解析，你不动 app/ 和 course/，Classroom.tsx 只改老师入口与布局部分）。
目标一个半小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建由你跑并如实写结果。花费 ¥0。
背景（CURRENT_STATE [UI-00] 课堂布局；用户 2026-10-07 亲测）：76-MF02 做成了右下角圆形头像 + 浮动小窗，用户不要：①要的是老师“往右展开”，展开后和页面同高（右侧整页高的一栏）；②右下角的头像按钮压在“开始上课”按钮上（截图可见）；③老师动作太少——现在 teacherRuntime 加载模型后 stopAllMotions、只做呼吸、眨眼、口型和点头摇头几个参数，模型本来带了很多动作和表情，用户记得它“有很多动态的感觉”。
做法（架构师定）
  1. 收起：课堂右边缘一条细竖条（约 28–36 像素宽，与页面同高，带老师小图标和“展开老师”提示），不压住任何按钮、输入框；对话区占满其余宽度。
  2. 展开：从右侧滑出一栏，与课堂页面同高（顶到底），宽度可拖动左边缘调整（有最小、最大值），对话区相应变窄；栏内是 Live2D 老师，背景透明或跟主题一致；栏顶部“收起”按钮；朗读开关与停止朗读放在栏内。保留 76-MF02 的模型平移、滚轮缩放、双击复位，去掉浮窗拖动与圆形头像。展开/收起与栏宽、模型缩放记住。收起时暂停渲染（保留 76 的做法），朗读照常。
  3. 动作与表情：先只读盘点模型自带的动作组、动作文件和表情（读 manifest 与 model3.json，列出名称、时长），回执列表。然后用上：空闲时按模型的 Idle 组随机轮播（间隔自然，不机械重复）；老师说话时播放说话类动作或轻微手势；学生答对用开心类表情/动作，答错或漏勾用思考/鼓励类，开始讲新点用打招呼或指示类（按盘点结果就近选，模型没有的就不做，不硬造）；口型、眨眼照常。动作之间平滑过渡，不打断口型。“减少动态效果”打开时只保留呼吸、眨眼和口型。
  4. 浏览器测试：收起时细竖条不压任何按钮、对话区满宽；展开与页面同高、拖左边缘改宽、收起、重开后保持；动作盘点结果与触发（用假事件触发答对、答错、讲新点各一次，记录播放了哪个动作）。亮色、暗色各截两张（收起、展开），另录或截一组展开后动作变化的截图。
  5. 全量通过、web 构建通过。
完成标准：收起不压任何按钮；展开是与页面同高的右侧栏；模型自带的空闲、说话与几类反应动作都用上；状态记住；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix77-teacher\report.md（附截图与动作盘点表）。
回执（尽量短，用表格）：【MF-02｜client｜第七十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 收起与展开的样子 ／ 模型自带动作与表情盘点（数量与名称）／ 哪些场合播放哪些动作 ／ 记住的状态、收起暂停渲染 ／ 全量、web 构建 ／ 截图路径 ／ 本地最终占用
~~~

## 第七十六批任务原文（2026-10-07，基线 c9ba7958）

### [76-MF02] client｜课堂老师改成可收起、可拖动的浮动小窗

~~~text
【MentorFlow 第七十六批｜MF-02（工位 client）｜课堂里的 Live2D 老师改成可收起、可拖动的浮动小窗：默认不占对话的位置】

现成项目与许可证（第一节）
  - 只改本项目现有前端：refactor/learning/web/src/pages/Classroom.tsx（右侧老师栏与布局）、web/src/teacher/TeacherStage.tsx、teacher-stage.css、teacherRuntime.ts（Live2D 画布，pixi.js 8.13.1 MIT 与 untitled-pixi-live2d-engine 1.3.5，已安装）。拖动、缩放用浏览器原生 Pointer Events 自己写几十行即可，不新增依赖。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard c9ba7958
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西（模型和朗读资源只读使用）。D:\MentorFlowApp 只读。D:\MentorFlowData\account-shell 只读（用户正在用）。先读 AGENTS.md（磁盘规则）：所有产物在 D:\MentorFlow\.local\test-tmp\fix76-teacher\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。
用户在中测试，目标一个半小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建由你跑并如实写结果。花费 ¥0。
背景（CURRENT_STATE [UI-00] 课堂布局，用户 2026-09-30 定“右侧 Live2D 老师可展开，全屏时不挡对话”；2026-10-07 用户亲测反馈）：现在老师固定占着课堂右边一大栏，对话区本来就窄，被挤得更小；用户要的是能展开、收起，不占位置；老师那块背景也没有能拖动的感觉。
做法（架构师定）
  1. 布局：课堂对话区占满整个宽度，不再给老师留固定的一栏。
  2. 老师改成浮在课堂上方的小窗：
     - 收起时：右下角一个圆形头像小按钮（显示老师头部或模型缩略），不挡输入框和发送按钮；点一下展开。
     - 展开时：一个圆角浮窗，里面是 Live2D 老师，背景透明或半透明（跟主题一致，不要整块白底卡片）；浮窗顶部有一条把手可以按住拖动到窗口任意位置（拖不出窗口边界），右下角可以拖动改大小（有最小、最大尺寸）；右上角“收起”按钮；朗读开关、停止朗读这些按钮移到浮窗里面。
     - 浮窗里的模型本身：鼠标拖动可以在浮窗里平移模型，滚轮缩放模型大小（有上下限），双击回到默认位置。
     - 展开/收起状态、浮窗位置和大小、模型缩放记住（下次进课堂保持），窗口大小变化时浮窗自动收回到可见范围。
     - 全屏、窄窗口时同样不挡对话的输入区；“减少动态效果”打开时不做展开动画。
  3. 收起时不白白消耗：暂停 Live2D 渲染循环（展开再恢复），朗读仍可正常播放。
  4. 浏览器测试：默认收起且对话区满宽；展开、拖动、改大小、收起、重开后状态保持；拖不出窗口；收起时渲染暂停。亮色、暗色各截两张（收起、展开）放进报告。
  5. 全量通过、web 构建通过。
完成标准：老师默认不占对话位置；展开后是可拖动、可改大小、可收起的浮窗，模型可平移缩放；状态记住；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix76-teacher\report.md（附截图）。
回执（尽量短，用表格）：【MF-02｜client｜第七十六批｜完成/部分完成/受阻】基线 ／ 提交 ／ 收起与展开的样子 ／ 拖动、改大小、模型平移缩放 ／ 记住了哪些状态 ／ 收起时是否暂停渲染 ／ 全量、web 构建 ／ 截图路径 ／ 本地最终占用
~~~

## 第七十五批任务原文（2026-10-07，基线 77a45e6f）

### [75-MF01] server｜永久登录第二步：原生会话不过期、存数据库、临时故障不当吊销

~~~text
【MentorFlow 第七十五批｜MF-01（工位 server）｜永久登录第二步：桌面实际用的原生会话去掉 8 小时、128 次刷新上限，存进数据库（后端重启不丢），身份服务临时故障不再当成吊销】

现成项目与许可证（第一节）
  - 只改本项目现有代码：服务器 refactor/server/enterprise/identity/native.py（NativeSessions）、需要时 identity 下验证器调用处、后端现有数据库与 alembic 迁移（照现有迁移写法新增一张表）、Keycloak 自定义验证器 refactor/server/keycloak/native-identity-verifier（Java 源码）与 build.py（只在必须时改）。不新增依赖。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard 77a45e6f          # 主线，已含 74-MF01（2bae1e5d）
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署（脚本只写不跑）；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。D:\MentorFlowData\account-shell 只读。先读 AGENTS.md（磁盘规则、功能先行）：所有产物在 D:\MentorFlow\.local\test-tmp\fix75-native-session\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
目标两小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建由你跑并如实写结果。花费 ¥0。
背景（CURRENT_STATE [BUG-55]，用户 2026-10-07 定“登录永久，用户不自己退出就不踢出”）：74-MF01 已把身份层与 Keycloak realm 改成永久，但桌面实际用的原生会话还有四处到期（74-MF01 报告 D:\MentorFlow\.local\test-tmp\fix74-session\report.md 与 native-evidence.json 已离线复现）：
  ① native.py login 建会话族时写死 clock()+8*3600；② refresh 满 128 次就整族作废；③ families/access/refreshes 全在内存，后端一重启所有人掉线；④ refresh 时验证器（Keycloak 自定义 SPI，NativeIdentityVerifier.java 274 行起 lease=28800）任何异常都被当成吊销返回 401。
做法（架构师定）
  1. 会话族不设到期：deadline 用身份层的永久设置（TTL=0 即不过期）；去掉 128 次上限（保留“同一个刷新令牌被重放就整族作废”的检测，这是防盗用不是到期）。
  2. 持久化：会话族与当前刷新令牌摘要存进后端数据库（新增一张表，alembic 迁移；只存摘要不存明文），后端重启后能继续刷新；访问令牌仍短期、可只在内存（重启后客户端用刷新令牌换新的即可）。
  3. 刷新时不再依赖 Keycloak 验证器的 8 小时租期：刷新只核对本地会话族有效、账号仍存在且未停用、未改密码、未注销、未被挤下线（这些事件发生时服务器主动作废对应会话族——查清现有改密码、注销、单设备挤下线的代码，保证它们会作废数据库里的会话族）。只有首次用密码登录时才走验证器。若查明刷新必须经过验证器，就把 SPI 的 lease 改成读取 realm 最大值，并写好重新构建 jar、更新 Keycloak 镜像的步骤（只写不跑），回执写明为什么必须走验证器。
  4. 临时故障不当吊销：数据库或身份服务暂时不可用时返回 503（客户端已按 74-MF01 处理为“保留会话、稍后重试”），只有明确的吊销（重放、改密码、注销、挤下线、账号停用）才返回 401。
  5. 上线步骤：迁移、后端重建与重启的命令清单写进报告（只写不跑），并说明上线那一刻已登录的人会不会掉线（会就写清楚只掉这一次）。
  6. 测试：会话推进 10 年仍可刷新；刷新 1000 次仍可用；重放旧刷新令牌整族作废；模拟后端重启（新建服务实例、同一数据库）后旧刷新令牌仍可用；改密码、注销、挤下线后刷新返回 401；数据库或验证器抛临时错误返回 503 且会话保留。全量通过、web 构建通过。
完成标准：原生会话没有任何时间或次数到期；后端重启不丢登录；临时故障返回 503 不吊销；明确吊销仍有效；全量、构建通过；上线命令清单齐全。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix75-native-session\report.md（附上线命令清单）。
回执（尽量短，用表格）：【MF-01｜server｜第七十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 四处到期各怎么去掉 ／ 持久化表与迁移 ／ 刷新还走不走验证器、为什么 ／ 哪些事件会作废会话 ／ 上线步骤与上线时是否掉线一次 ／ 全量、web 构建 ／ 本地最终占用
~~~

## 第七十四批任务原文（2026-10-07，基线 fb167d82）

### [74-MF01] server｜登录改成永久（代码与上线脚本，不碰服务器）

~~~text
【MentorFlow 第七十四批｜MF-01（工位 server）｜登录改成永久：用户不自己退出就一直保持登录，不再闲置 30 分钟、8 小时被踢出；只写代码和上线脚本，不碰服务器】

现成项目与许可证（第一节）
  - 用本项目现有代码与现成的 Keycloak 管理接口（Keycloak Admin REST API，Apache-2.0）：服务器 refactor/server/enterprise/identity/config.py、keycloak.py；Keycloak 模板 refactor/server/keycloak/keycloak-realm.json；上线脚本照 refactor/server/keycloak/ 下现有的 realm_admin.py（可重复执行、可回滚）的写法新写一个；客户端 refactor/desktop/login_session.py、account_protocol.py、account_shell_main.py 只在需要时改。不新增依赖。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard fb167d82
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署（脚本只写不跑）；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。D:\MentorFlowData\account-shell 只读（用户正在用）。先读 AGENTS.md（磁盘规则、功能先行）：所有产物在 D:\MentorFlow\.local\test-tmp\fix74-session\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
目标一小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建由你跑并如实写结果。花费 ¥0。
背景（CURRENT_STATE [BUG-55]，用户 2026-10-07 定）：用户反复被踢回登录界面。原因是服务器登录配置：访问令牌 5 分钟、闲置 30 分钟会话失效（ssoSessionIdleTimeout 1800）、最长 8 小时（ssoSessionMaxLifespan 28800），身份层会话默认 8 小时（session_ttl_seconds 28800，且校验上限 7 天）。用户定：全部改成永久——用户不自己点退出，就一直保持登录，用着用着绝不踢出去。这是去掉安全加固，符合“功能先行”。线上 Keycloak 的 realm 不会被模板重新导入（DEPLOY.md），必须用管理接口改线上 realm。
要做
  1. 服务器身份层：会话不过期（例如 session_ttl_seconds=0 表示永不过期，并设为默认）；放开配置校验里的上限；keycloak.py 里算会话到期的地方按“永不过期”处理。服务器重启后已登录的人仍然有效。
  2. Keycloak：模板里把闲置超时、最长会话、刷新令牌相关的时长都改成 Keycloak 允许的最大值或“不限”（查清 Keycloak 当前版本对 0 与最大值的含义，选真正等于永久的做法，回执写明依据）；客户端会话同样不限；刷新令牌不因轮换作废。
  3. 上线脚本（只写不跑）：放进 refactor/server/keycloak/，照 realm_admin.py 的写法：先只读打印线上当前值（--dry-run），备份当前设置到文件，再改成第 2 条的值，最后再读一次核对；带 --rollback 用备份恢复。另写一段在服务器上执行的完整命令清单（含后端环境变量、需要重启哪个容器、怎么确认生效），放进报告，供架构师或用户在服务器上照着执行。
  4. 客户端：只有用户自己退出、改密码、注销账号、或服务器明确吊销时才要求重新登录；网络断开、服务器暂时不可用、电脑睡眠醒来时一律不踢出，后台安静重试续上。查一遍现有逻辑，有违反的改掉，回执列出。
  5. 测试：身份层会话永不过期；配置校验接受永久；客户端在网络错误、5xx、睡眠后刷新时不进入重新登录；明确 401 吊销时才重新登录。全量通过、web 构建通过。
完成标准：代码里不再有闲置 30 分钟、8 小时这类到期；上线脚本可 dry-run、可回滚；客户端只在明确吊销时重新登录；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix74-session\report.md（附服务器上执行的命令清单）。
回执（尽量短，用表格）：【MF-01｜server｜第七十四批｜完成/部分完成/受阻】基线 ／ 提交 ／ 身份层怎么改成永久 ／ Keycloak 设成什么值、依据 ／ 上线脚本用法（dry-run、执行、回滚）／ 客户端什么情况才要求重新登录 ／ 全量、web 构建 ／ 本地最终占用
~~~

## 第七十三批任务原文（2026-10-07，基线 788112d9）

### [73-MF01] server｜备题改回一次全备，失败互不影响、显示原因

（任务原文见当天对话；回执：完成，合入 1046be6b，全量 2882 通过，离线完整场景 59 步 0 问题）

## 第七十二批任务原文（2026-10-07，基线 ec23c4f6）

### [72-MF01] server｜知识地图这一步能直接删模块和知识点，不重填

~~~text
【MentorFlow 第七十二批｜MF-01（工位 server）｜知识地图这一步能直接删模块（和单个知识点）：程序从地图里去掉，不调模型、不重填，已生成的其他内容全保留】

现成项目与许可证（第一节）
  - 只改本项目现有代码：app/plan_flow.py（_carry_out 的意图处理、知识地图阶段的预估 map_ranges）、course/plan_intent.py（意图识别里加“删模块/删知识点”）、web/src/pages/NewPlan.tsx 与 planning/PlanGraph.tsx（模块详情面板里加删除按钮）。删除范围的记录沿用 58-MF01 的 removed_content。不新增依赖。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard ec23c4f6
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell 亲测：只读，不改不删；要用的草稿复制到测试目录，只在副本上跑。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：所有产物在 D:\MentorFlow\.local\test-tmp\fix72-mapdelete\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
用户在等着接着测，目标半小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建、离线完整场景由你跑并如实写结果。花费上限 ¥0.1（只为第 4 条真实识别一次删除意图）。
背景（CURRENT_STATE [PLAN-04] 2026-10-07 待修）：用户在知识地图这一步（知识点已填完，541 点）对话里说“删掉导航和求职两个模块”，AI 回“当前是轮廓阶段，还没有可删的模块编号……要按这个来调整轮廓吗”。查代码：这一步没有“直接删模块”的处理，用户确认后会走 supplement（回到按资料定结构，已填的点作废）或 limit_scope（make_outline 把整张地图重填），都会让已生成的 541 个点重来、再花一次钱。用户定：这里必须能直接删，已经生成的东西不撤掉重来。
要做
  1. 新增意图“删模块 / 删知识点”（按模块名、模块序号、知识点名都能认）：知识地图这一步（phase=outline）由程序直接从 draft["outline"] 里去掉这些模块或知识点，并把它们记进 removed_content（以后任何重新整理都不加回）；不调用 generate、不调 material_structure、不重填别的模块；删完立即按剩下的点重算时长预估并刷新地图。删除前用一句话确认（“将删掉：××、××，共 N 个知识点，删除后不能恢复，确定吗？”），用户说“是/确定”才删。
  2. 意图识别不能再把这一步说成“轮廓阶段”；提到不存在的模块名时，回复列出现有模块名让用户选。
  3. 知识地图的模块详情面板里加“删掉这个模块”按钮（同样先确认）；知识点列表每条后面加一个小的删除按钮（先确认）。结构这一步（material_preview）的删模块走现有“调整结构”，不改。
  4. 用复制出来的用户草稿（“ai-agent学习”，停在知识地图，541 点，含“资料导航与学习路线”“面试计划、项目表达与求职准备”等模块）：真实识别一次“删掉导航和求职两个模块”（管理员 DeepSeek 预设，Key 只经现有代码从 Windows 凭据读取），确认后删除；报告删前删后的模块数、点数、预估总时长与每天负担，并确认整个过程没有 generate / outline 调用（附调用记录）。
  5. 离线测试：按名字删、按序号删、删单个知识点、删不存在的名字、取消确认、删后再调整结构时被删内容不回来。
  6. 全量通过、web 构建通过、离线完整场景（--to free）通过；面板删除按钮与确认各截一张图。
完成标准：知识地图这一步能直接删模块和知识点，不调用生成、不重填；删后预估正确；全量、构建、离线完整场景通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix72-mapdelete\report.md（附截图）。
回执（尽量短，用表格）：【MF-01｜server｜第七十二批｜完成/部分完成/受阻】基线 ／ 提交 ／ 怎么删、怎么确认 ／ 用户草稿副本删前删后（模块、点数、预估）与调用记录 ／ 全量、web 构建、离线完整场景 ／ 花费 ／ 截图路径 ／ 本地最终占用
~~~

## 第七十一批任务原文（2026-10-06，基线 76bae4b2）

### [71-MF01] server｜联调第七十批四份，问答题要点改为程序切、AI 选编号，副本上真实走通备题与模块检测

~~~text
【MentorFlow 第七十一批｜MF-01（工位 server）｜联调第七十批四份：合在一起、解决冲突、全量与离线完整场景通过，再在用户草稿副本上真实走一遍“备第 1 模块 → 模块检测（含问答题勾要点）”】

现成项目与许可证（第一节）
  - 只合并、联调本项目第七十批已完成的四个提交，不新增功能、不新增依赖：
    70-MF05 office/library cf8584ee（拆资料自带题、删挖空与上网找题、结构步显示题量）
    70-MF01 office/server 8cddaf7e（按题考核、recall 两个桥接动作）
    70-MF03 office/classroom 486d660f（随口问改对话）
    70-MF02 office/client 791a289b（问答题勾要点界面）
  四个提交都在同一个仓库里（工位共用对象库），用 git cherry-pick 或 git diff c95b0032 <提交> | git apply --3way 合。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard 76bae4b2
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户的 D:\MentorFlowData\account-shell 只读：要用的草稿、资料、知识点复制到测试目录，只在副本上跑。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：所有产物在 D:\MentorFlow\.local\test-tmp\fix71-integrate\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
目标两小时内交回。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。
背景：CURRENT_STATE [DIR-01] 开头“题目来源与考核”及细则（用户 2026-10-06 定）。架构师试合时：70-MF01 改了 70-MF05 已删除的 4 个测试（find59/test_find59_question_recovery.py、point_materials/test_cloze46_mastery_retry.py、test_questions44_contract.py、test_trusted56_user_materials.py——它们测的是已删的挖空与上网找题，跟着删掉即可）；另有 7 个文件冲突：app/classroom.py、app/plan_flow.py、web/src/pages/NewPlan.tsx、tests/refactor/classroom42/test_classroom42_question_allocation.py、conversation37/test_grounded_conversation37.py、integration38/test_offline_integrated38_flow.py、timeline/test_final_check_time_full_flow.py。各自回执里的遗留：70-MF01 全量 12 项旧挖空备题测试失败；70-MF05 完整场景停在课堂 QUESTION_UNAVAILABLE（缺新挑题），都应在合在一起后消失。
要做
  1. 按 70-MF05 → 70-MF01 → 70-MF03 → 70-MF02 的顺序合；冲突按四份任务各自的意图都保留（DIR-01 细则为准）：题只来自资料自带题，按题考核，随口问只在对话里，问答题走勾要点；只测已删除功能的测试删掉，其余测试改到新做法。回执列出每个冲突文件怎么合的。
  1b. 问答题要点改法（用户 2026-10-06 定，CURRENT_STATE [DIR-01]）：题和参考答案来自资料就一律收，绝不因为要点格式丢题。70-MF05 真实拆题丢掉的 10 道（什么是 RAG、SQL 和 NoSQL 有什么区别、数据库设计通常分为哪几步、NoSQL 数据库有哪些类型等，见 D:\MentorFlow\.local\test-tmp\fix70-questions\report.md）都是因为“要点必须是完整单句”的检查——答案是表格、编号列表或整段。改成：程序把参考答案切成单位（一句话、一个列表项、表格的一行），AI 只回编号选 3–5 条要点，不抄文字；切出来不超过 5 条就全部当要点、不调模型。删掉“完整单句”那条检查。用 70-MF05 保存的真实回复与这 10 道题的原文离线复现：修前丢、修后收，回执列出这 10 道各切成几条、选了哪几条。
  2. 全量通过、web 构建通过、离线完整场景（--to free）通过：离线场景里要有一个带题的模块（含 recall 题）和一个没题的模块，走通“没题只学不考”和问答题勾要点。
  3. 真实走一遍（管理员 DeepSeek 预设，Key 只经现有代码从 Windows 凭据读取）：复制用户草稿“ai-agent学习”（JavaGuide，已填完知识点，停在知识地图），在副本上点“保存与准备”，只备第 1 个有题的模块；然后在课堂里走这个模块的模块检测：至少一道问答题按“写回答 → 看答案 → 勾要点”提交（一次全勾、一次漏勾），确认按题判、≥90% 才过、只重测错题；确认第 2 个模块在后台提前备。报告每一步实际看到的结果、题数、花费与用时。
  4. 用 Qt 里的真实页面（或浏览器测试）各截一张：结构步题量显示、问答题看答案前/后、模块检测结果。
花费：上限 ¥2（价格按高峰折算）。
完成标准：四份合在一起后全量、构建、离线完整场景通过；10 道被丢的题修后都收进来；副本上真实走通“备第 1 个有题模块 → 模块检测含问答题勾要点”。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix71-integrate\report.md（附截图）。
回执（尽量短，用表格）：【MF-01｜server｜第七十一批｜完成/部分完成/受阻】基线 ／ 提交 ／ 每个冲突文件怎么合 ／ 删掉的测试 ／ 要点改法与 10 道题修前修后 ／ 全量、web 构建、离线完整场景 ／ 真实一遍：备题数量与用时、模块检测各步结果、后台是否提前备第 2 模块 ／ 花费 ／ 截图路径 ／ 本地最终占用
~~~

## 第七十批任务原文（2026-10-06，基线 c95b0032）

### [70-MF05] library｜题从资料里来：拆资料自带题、去掉挖空与上网找题、结构步显示每模块有没有题

~~~text
【MentorFlow 第七十批｜MF-05（工位 library）｜题从资料里来：建计划时把资料自带的题按原文拆出来、对到模块；去掉挖空出题和上网找题；结构这一步显示每个模块有没有题】

现成项目与许可证（第一节）
  - 只改本项目现有代码：course/point_materials.py（prepare_module，去掉挖空出计分题、随口问挖空、上网找题这几条路）、course/research*.py（去掉找题用途，找资料推荐不动）、新建 course/material_questions.py（拆题），题库入库沿用 course/question_bank.py 现有的入库接口与 66 批去重（不改它的挑题函数，挑题归 70-MF01）；结构这一步的显示改 app/plan_flow.py 的 structure_ranges 与 web/src/pages/NewPlan.tsx、planning/PlanGraph.tsx。识别“问题式标题”的规则用 69-MF05 实验里已验证的那套（D:\MentorFlow\.local\test-tmp\exp69-questions\）。不新增依赖。

开工同步
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard c95b0032
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户的 D:\MentorFlowData\account-shell 只读：要用的数据复制到测试目录，只在副本上跑。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：所有产物在 D:\MentorFlow\.local\test-tmp\fix70-questions\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
同时在跑：70-MF01（server，按题考核）、70-MF02（client，问答题勾要点界面）、70-MF03（classroom，随口问改对话）。你不改挑题、课堂、检测的代码。
目标两小时内交回。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建、离线完整场景由你跑并如实写结果。
背景：CURRENT_STATE [DIR-01] 开头“题目来源与考核”及其细则（用户 2026-10-06 定）。
共用约定（四份任务一致，照这个对接，冲突由架构师合并）
  题目记录（存进现有题库，question_bank）新增或沿用这些字段：
    kind: "objective"（有标准答案：选择、填空、计算）或 "recall"（问答题）
    stem: 题干（逐字取自资料）；options: 选择题选项（逐字）；answer: 客观题标准答案（逐字）
    reference: 问答题的原文参考答案（逐字，资料里这道题下面的回答）
    key_points: 问答题要点，3–5 条，每条是 reference 里逐字的一句
    module_id、point_ids、source（出处：原文块编号）
  判对规则：objective 由程序判（沿用 course/answer_compare.py）；recall 学生先写回答、再看参考答案，逐条勾 key_points，全部勾到算对。
  课堂桥接动作（62 批后的现有桥接方式）：
    class.recall_reveal {question_id, answer} → 保存学生回答，返回 {reference, key_points}（没提交回答前不返回参考答案）
    class.recall_submit {question_id, ticked: [要点下标]} → 返回 {correct, missed: [没勾的下标]}
  不再自己出题：不挖空出计分题、不上网找题、随口问不再出题。题只来自资料自带的题（以后还有用户上传）。
要做
  1. 拆题：备一个模块时，先用程序在这个模块引用的原文里找出资料自带的题（问题式标题及其下面的回答；带选项、带“答案”标记的习题），再让 AI 只把它们按原文拆成上面的题目记录（kind、stem、options、answer 或 reference、key_points），一律逐字取自原文；程序逐字核对每个字段都在原文里，对不上的丢掉并记录。非知识问题（“有没有 PDF 版”“没项目经验怎么办”这类）不收。每道题按原文块重叠对到模块和知识点。
  2. 去掉三条路：计分题挖空、随口问挖空、上网找题（相关代码删掉，连同只测它们的测试；不留开关）。模块“备好”的判断改成“这个模块的拆题做完了”（题可以是 0 道），不再要求每点 2 道（改 app/plan_flow.py 的 _module_ready，只改这一个判断）。
  3. 结构这一步（确认前）：程序（不调模型）按问题式标题统计每个模块大约有几道资料自带题，节点和详情里显示“约 N 道题”或“资料里没有题，这个模块只学不考”；卡片上写全计划约多少道题、多少个模块有题。
  4. 在用户草稿副本（“ai-agent学习”，JavaGuide）上真实跑两个模块的拆题（选一个题多的、一个题少的），报告：每模块拆出几道（objective / recall 各几道）、丢掉几道及原因、每道题人工核对（题干和答案是否对应、key_points 是否是关键、有没有错配到别的模块）、每道题与每个模块的花费和用时。
  5. 全量通过、web 构建通过、离线完整场景（--to free）通过（离线场景里的假资料要带几道题，没题的模块要能走通“只学不考”，这一部分如果依赖 70-MF01 还没有的考核改动，写清楚等合入后联调）。
花费：上限 ¥1.5（价格按高峰折算）。
完成标准：拆题按原文逐字、对到模块；三条出题路删掉；结构这一步能看到每模块有没有题；两个模块真实拆题有人工核对结论；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix70-questions\report.md，附逐题核对表。
回执（尽量短，用表格）：【MF-05｜library｜第七十批｜完成/部分完成/受阻】基线 ／ 提交 ／ 拆题怎么做、怎么核对 ／ 删掉了哪些代码 ／ 结构这一步显示什么 ／ 两个模块真实拆题：道数、丢弃原因、人工核对合格率、花费与用时 ／ 全量、web 构建、离线完整场景 ／ 本地最终占用
~~~

### [70-MF01] server｜按题考核：90% 的题算过，答错的题连对两遍，同题隔开，没题只学不考

~~~text
【MentorFlow 第七十批｜MF-01（工位 server）｜按题考核：模块检测和最终检验以题为单位，答错的题连续答对两遍才过，同一道题隔开出；没题的模块只学不考】

现成项目与许可证（第一节）
  - 只改本项目现有代码：course/question_bank.py 的挑题部分（不改入库，入库与拆题归 70-MF05）、course/mastery.py、app/classroom.py 里模块检测、重测、最终检验、报告与自由检验的部分（随口问那部分归 70-MF03，别动）、课堂桥接里新增 class.recall_reveal 与 class.recall_submit。不新增依赖。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard c95b0032
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。D:\MentorFlowData\account-shell 只读。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：所有产物在 D:\MentorFlow\.local\test-tmp\fix70-mastery\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
同时在跑：70-MF05（library，拆题入库）、70-MF02（client，勾要点界面）、70-MF03（classroom，随口问改对话，也改 app/classroom.py 的随口问部分——你们改同一个文件的不同部分，各自只动自己那块）。
目标三小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建、离线完整场景由你跑并如实写结果。花费 ¥0（不调用真实模型，用假题）。
背景：CURRENT_STATE [DIR-01] 开头“题目来源与考核”及其细则（用户 2026-10-06 定）。旧的“每点 2 道、按知识点升三级”（CURRENT_STATE 里“过关等级”“学习回路确认”）被按题考核取代。
共用约定（四份任务一致，照这个对接，冲突由架构师合并）
  题目记录（存进现有题库，question_bank）新增或沿用这些字段：
    kind: "objective"（有标准答案：选择、填空、计算）或 "recall"（问答题）
    stem: 题干（逐字取自资料）；options: 选择题选项（逐字）；answer: 客观题标准答案（逐字）
    reference: 问答题的原文参考答案（逐字，资料里这道题下面的回答）
    key_points: 问答题要点，3–5 条，每条是 reference 里逐字的一句
    module_id、point_ids、source（出处：原文块编号）
  判对规则：objective 由程序判（沿用 course/answer_compare.py）；recall 学生先写回答、再看参考答案，逐条勾 key_points，全部勾到算对。
  课堂桥接动作（62 批后的现有桥接方式）：
    class.recall_reveal {question_id, answer} → 保存学生回答，返回 {reference, key_points}（没提交回答前不返回参考答案）
    class.recall_submit {question_id, ticked: [要点下标]} → 返回 {correct, missed: [没勾的下标]}
  不再自己出题：不挖空出计分题、不上网找题、随口问不再出题。题只来自资料自带的题（以后还有用户上传）。
要做
  1. 模块检测：出这个模块的全部题（有几道出几道）；答对 ≥90% 的题算过；没过时只重测答错的题（不重讲）；这个模块 0 道题就不做模块检测，学完直接进下一模块。
  2. 最终检验：全计划所有模块都过了才进；出全计划的题。每道题的过关：模块检测第一次就答对的题，最终检验里再答对一次就过；在模块检测或最终检验里答错过的题，要连续答对两遍才过；同一道题不紧挨着连考，中间至少隔 3 道别的题（题不够时尽量拉开）。全部题过关出报告。整份计划 0 道题：不开模块检测和最终检验，学完全部内容就算完成，报告只写学习部分。
  3. recall 题：实现 class.recall_reveal / class.recall_submit；全部勾到算对，否则算错，按第 2 条规则继续；记录学生回答原文和勾选。objective 题照旧程序判。
  4. 旧数据：旧计划（按知识点出题、带挖空题的）不迁移，按现有“旧版计划，已不可用”的处理方式。删掉旧的按知识点升级与换角度挑题代码及只测它们的测试。
  5. 报告、自由检验、模拟工具（tests/refactor/simulation）跟着改到按题；时长预估里检测时间改成按题数估（每题 2 分钟；最终检验按“90% 的题一次过、10% 的题要多答 3 次”估），没题的模块检测时间为 0。
  6. 离线测试：三模块假计划（一个模块 5 道题含 recall、一个模块 0 道、一个模块 2 道），覆盖：模块检测过与不过、只重测错题、0 道模块直接跳过、最终检验里“首次对的题再对一次”“错过的题连对两遍”“同题隔开”、recall 勾全与漏勾、整份 0 道题的计划。
  7. 全量通过、web 构建通过、离线完整场景（--to free）通过。
完成标准：上面六种情况离线测试通过；旧的按知识点升级代码删掉；全量、构建、离线完整场景通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix70-mastery\report.md。
回执（尽量短，用表格）：【MF-01｜server｜第七十批｜完成/部分完成/受阻】基线 ／ 提交 ／ 模块检测规则 ／ 最终检验规则与同题间隔 ／ recall 两个桥接动作 ／ 0 道题的模块与计划怎么走 ／ 删掉了什么 ／ 全量、web 构建、离线完整场景 ／ 本地最终占用
~~~

### [70-MF02] client｜问答题界面：先写回答、再看答案、逐条勾要点

~~~text
【MentorFlow 第七十批｜MF-02（工位 client）｜问答题界面：先写回答、再看参考答案、逐条勾要点，全部勾到算对】

现成项目与许可证（第一节）
  - 只改前端课堂里出题、作答的组件（refactor/learning/web/src/pages/Classroom.tsx、web/src/classroom/ 下相关组件与样式），对接上面约定的 class.recall_reveal / class.recall_submit。勾选清单的交互参考 eXeLearning Checklist（CC BY-SA 4.0，只借做法不抄文件，68-MF04 调研）。不新增依赖。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard c95b0032
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。D:\MentorFlowData\account-shell 只读。先读 AGENTS.md（磁盘规则）：所有产物在 D:\MentorFlow\.local\test-tmp\fix70-recall-ui\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。
同时在跑：70-MF01（实现两个桥接动作，你用假桥接开发）、70-MF05、70-MF03。你只改前端。
目标一个半小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建由你跑并如实写结果。花费 ¥0。
背景：CURRENT_STATE [DIR-01] 开头“题目来源与考核”细则①（用户 2026-10-06 定）。
共用约定（四份任务一致，照这个对接，冲突由架构师合并）
  题目记录（存进现有题库，question_bank）新增或沿用这些字段：
    kind: "objective"（有标准答案：选择、填空、计算）或 "recall"（问答题）
    stem: 题干（逐字取自资料）；options: 选择题选项（逐字）；answer: 客观题标准答案（逐字）
    reference: 问答题的原文参考答案（逐字，资料里这道题下面的回答）
    key_points: 问答题要点，3–5 条，每条是 reference 里逐字的一句
    module_id、point_ids、source（出处：原文块编号）
  判对规则：objective 由程序判（沿用 course/answer_compare.py）；recall 学生先写回答、再看参考答案，逐条勾 key_points，全部勾到算对。
  课堂桥接动作（62 批后的现有桥接方式）：
    class.recall_reveal {question_id, answer} → 保存学生回答，返回 {reference, key_points}（没提交回答前不返回参考答案）
    class.recall_submit {question_id, ticked: [要点下标]} → 返回 {correct, missed: [没勾的下标]}
  不再自己出题：不挖空出计分题、不上网找题、随口问不再出题。题只来自资料自带的题（以后还有用户上传）。
要做
  1. recall 题卡片：题干 → 多行输入框“先写下你的回答”（空着不能看答案）→ 按钮“写好了，看参考答案”（调 class.recall_reveal）→ 显示原文参考答案（可折叠）和 3–5 条要点，每条前面一个勾选框，提示“对照你的回答，勾出你答到的要点”→ 按钮“提交”（调 class.recall_submit）→ 显示“全部答到，算对”或“漏了 N 条”，漏掉的要点高亮。
  2. 学生的回答在看答案后不能再改（只读显示在旁边），防止看完答案再补写。
  3. objective 题界面不变。手机宽度、窄窗口、亮暗主题都正常，不溢出。
  4. 用假桥接写浏览器测试：写回答前不能看答案、看答案后回答只读、全勾算对、漏勾显示漏了哪几条；亮暗各截一张图放进报告。
  5. 全量通过、web 构建通过。
完成标准：上面流程在浏览器测试里走通；截图无溢出；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix70-recall-ui\report.md（附截图）。
回执（尽量短，用表格）：【MF-02｜client｜第七十批｜完成/部分完成/受阻】基线 ／ 提交 ／ 界面流程 ／ 防偷看与防改答 ／ 全量、web 构建 ／ 截图路径 ／ 本地最终占用
~~~

### [70-MF03] classroom｜随口问改成老师对话直接问，不出题不记分

~~~text
【MentorFlow 第七十批｜MF-03（工位 classroom）｜随口问改成老师在对话里直接问：不出题、不记分、不进程序记录】

现成项目与许可证（第一节）
  - 只改本项目现有代码：course/teacher_agent.py（老师提示）、app/classroom.py 里随口问（role=quick）相关的部分（模块检测、最终检验那部分归 70-MF01，别动）、前端课堂里随口问的显示（web/src/classroom/ 下对应组件，只删随口问题卡）。随口问题的生成在 course/point_materials.py，归 70-MF05 删除，你不动。不新增依赖。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard c95b0032
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。D:\MentorFlowData\account-shell 只读。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：所有产物在 D:\MentorFlow\.local\test-tmp\fix70-quick\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
同时在跑：70-MF01（也改 app/classroom.py 的检测部分——你们改同一个文件的不同部分，各自只动自己那块）、70-MF05、70-MF02。
目标一个半小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建、离线完整场景由你跑并如实写结果。花费上限 ¥0.3（只为第 3 条真实跑一次）。
背景：CURRENT_STATE [DIR-01] 开头“题目来源与考核”细则⑤（用户 2026-10-06 定）：随口问不再挖空出选择题（抽查 6 道里 2 道答案范围重叠），改成老师在对话里直接问，不记分、不进程序记录；学生借此知道自己理解对不对，想问就问，口误不追究。
要做
  1. 课堂里不再弹随口问题卡、不再取 role=quick 的题、不再按随口问结果记“薄弱”；删掉相关代码与只测它们的测试。
  2. 老师提示加上：讲完一段后，可以用一句话问学生一个理解性的问题（例如“你能用自己的话说说它和 X 的区别吗？”），学生回答后老师就事论事地回应：理解对了简单肯定，理解偏了指出偏在哪、对照原文讲清；只是口误或表达不完整就不纠缠；不打分、不说“答对/答错”、不写进任何记录；不要每段都问，学生说“继续”就往下讲。
  3. 真实跑一次（管理员 DeepSeek 预设，Key 只经现有代码从 Windows 凭据读取）：用离线场景里的小资料讲 2 个点，学生分别答对、答偏、口误各一次，报告老师的原话，人工看是否符合第 2 条。
  4. 全量通过、web 构建通过、离线完整场景（--to free）通过。
完成标准：课堂不再出随口问题卡、不记薄弱；老师对话式提问符合第 2 条；全量、构建、离线完整场景通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix70-quick\report.md。
回执（尽量短，用表格）：【MF-03｜classroom｜第七十批｜完成/部分完成/受阻】基线 ／ 提交 ／ 删掉了什么 ／ 老师提示怎么改 ／ 真实一次的三种回应原话与人工结论 ／ 全量、web 构建、离线完整场景 ／ 花费 ／ 本地最终占用
~~~

## 第六十九批任务原文（2026-10-06，基线 06df6250）

### [69-MF05] 实验｜资料自带题覆盖率、要点回想、对准目标的填空（20 个点）

~~~text
【MentorFlow 第六十九批｜MF-05（工位 library）｜实验：资料自带题覆盖率、要点回想、对准目标的填空——在用户草稿副本上试 20 个点，只看效果不改产品】

现成项目与许可证（第一节）
  - 只用本项目现有代码（草稿与知识点数据、course/point_materials.py 里定位原文的现有调用方式、course/answer_compare.py 的填空判分），实验脚本放在 D:\MentorFlow\.local\test-tmp\exp69-questions\ 下（不进仓库），不改产品代码。做法依据 68-MF04 调研（D:\MentorFlow\.local\test-tmp\research68\report.md）：挖空按 Becker 等 2012（ACL N12-1092）先选对应目标的重要句、再选核心短答案；入库检查按 Jiang 与 Lee 2017（W17-5015）做原文回拼、查泄露、列明允许答案；要点逐项自评参考 eXeLearning Checklist（CC BY-SA 4.0，只借做法不抄文件）。不新增依赖。

开工同步
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard 06df6250
测试用 D:\MentorFlow\.venv\Scripts\python.exe。本任务不提交代码。
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户的 D:\MentorFlowData\account-shell 只读：草稿“ai-agent学习”（JavaGuide，已填完知识点，停在知识地图）与资料数据复制到实验目录，只在副本上跑。先读 AGENTS.md（磁盘规则）：所有产物在 D:\MentorFlow\.local\test-tmp\exp69-questions\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
目标两小时内交回。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。
背景（CURRENT_STATE [DIR-01]“找题”，用户 2026-10-06 定方向，细节待本实验数据）：
  - 不再上网硬找题。资料带题多（资料自带题覆盖 ≥70% 的知识点）就只考资料自带的题，没题的点只学不考；资料带题少（<70%）就在确认结构前告诉用户，请他补一份题源，或接受“要点回想”和“填空”补题。
  - 要点回想：问题用资料自带的面试题（没有就用标题“说说 X”），学生先写回答再看原文，原文拆成 3–5 条要点（AI 只从原文挑句子、逐字、不改写），学生逐条勾，全部勾到才算答对。
  - 填空：AI 只指出原文里回答这个点掌握目标的那一句和其中的核心短答案（逐字），程序挖掉、程序判；用户最担心挖到不重要的句子或词。
要做
  1. 资料自带题覆盖率（不调模型）：从 JavaGuide 的原文里找出“资料自带的题”——问题式标题（以问号结尾，或“A 和 B 的区别”“为什么……”这类问法，规则写清楚）加它下面的原文作为参考答案；按“题所在原文块与知识点引用的原文块是否重叠”对到知识点上。报告：资料自带题总数（去掉“JavaGuide 有没有 PDF”“没项目经验怎么办”这类非知识问题）、对上了几个点、覆盖率（对上的点 ÷ 全部点），按模块列，并判断这份资料按 70% 的线算“题多”还是“题少”。抽 20 个对上的配对人工看对不对。
  2. 选 20 个知识点（分布在各个知识模块，不取导航、求职类），每点做两件事（真实调用 DeepSeek，Key 只经现有代码从 Windows 凭据读取）：
     a. 要点回想：有资料自带题的用它，没有的用“说说 X”；让 AI 从这个点的原文里逐字挑 3–5 句作为要点（程序核对每句都逐字在原文里）。
     b. 填空：让 AI 逐字指出回答掌握目标的那一句和核心短答案；程序挖掉，做入库检查：答案在原句里只出现一次、挖掉后句子里没有泄露答案、列出允许的等价写法。
  3. 人工核对每一道：要点回想——要点是不是这个问题的关键、有没有漏掉最重要的、有没有凑数的句子；填空——挖的句子是不是这个点最重要的、挖的词是不是核心、不看原文能不能答、答案是否唯一。给出合格率，并把不合格的原因分类。
  4. 记录每点的模型调用次数、输入/输出 token、花费与用时，推算全部 541 点的花费。
花费：上限 ¥1（价格按高峰折算），到上限就停。
完成标准：覆盖率与“题多/题少”判断有数；20 个点两类题都做完并有逐题人工结论；每点花费有数。
验收：报告写到 D:\MentorFlow\.local\test-tmp\exp69-questions\report.md，附逐题核对表。
回执（尽量短，用表格）：【MF-05｜实验｜第六十九批｜完成/部分完成/受阻】资料自带题数与覆盖率（按模块）、题多还是题少 ／ 要点回想：合格率与不合格原因 ／ 填空：合格率与不合格原因（挖错句、挖错词、答案不唯一、太难、泄露）／ 每点花费、用时与 541 点推算 ／ 本地最终占用
~~~

## 第六十八批任务原文（2026-10-06）

### [68-MF04] 只读调研｜挖空怎么挖到关键处、要点回想自评、过关制度要不要换

~~~text
【MentorFlow 第六十八批｜MF-04（只读调研，不用工位）｜怎么考才靠谱：挖空怎么挖到关键处、要点回想自评、两道题升三级这套过关制度要不要换】

现成项目与许可证（第一节）
  - 本任务就是查现成项目和论文；每条结论注明项目名、链接、许可证（论文写作者、年份、出处）。架构师凭记忆给的三个链接先核实：https://github.com/ankitects/anki （Anki，据称 AGPL-3.0）、https://github.com/open-spaced-repetition/fsrs4anki （FSRS，据称 MIT）、https://github.com/st3v3nmw/obsidian-spaced-repetition （据称 MIT）。

规则
  只读：不改任何仓库（主目录和工位都不动），不提交、不推送；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则）：E 盘不写；所有产物在 D:\MentorFlow\.local\test-tmp\research68\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。
  不调用付费模型（¥0）。遇到选择自己决定，回执写明理由，不停下来问。只写查到的事实，查不到就写“没找到”，不编。
背景（CURRENT_STATE [DIR-01]“找题”，用户 2026-10-06）：
  - 产品方向：AI 不出题、不判主观题；计入过关的题由程序判。上网找现成题实测失败（JavaGuide 这类面试资料网上几乎没有带标准答案的客观题，两次试备 0 道，每点 ¥0.18–0.61）。
  - 现在的过关制度（用户 2026-09-29 定）：每点备 2 道题；模块检测 ≥90% 过；最终检验里随口问和模块检测都对的点从 2 级起、答对 1 次过，模块检测错过的点从 1 级起，任何一次答错退回 1 级，两道题换着考，连续答对两次升到 3 级才算过。
  - 正在讨论的新做法：①资料自带的面试题（问题＋原文参考答案）直接用；②“要点回想”：学生先写自己的回答，再看原文，原文拆成 3–5 条要点（AI 只从原文挑句子、不改写），学生逐条勾自己答到的，全部勾到才算答对；③资料题不够时“术语填空”：AI 指出原文里回答这个点掌握目标的那一句和关键词（逐字），程序挖掉关键词，程序判。用户最担心的是挖空挖到不重要的句子或词，学生根本填不出来；用户也拿不准“每点两道、升三级”这套制度还要不要坚持，要先看调研。
要查
  1. 挖空（gap-fill / cloze）怎么选句子、选词才挖在关键处：查自动生成填空题的论文与开源实现（例如“key sentence selection / gap selection”方向的研究，架构师印象里有 Becker 等 2012 年 “Mind the Gap: Learning to Choose Gaps for Fill-in-the-Blank Quizzes”、Agarwal 与 Mannem 2011 年从教材自动生成填空题的工作，先核实是否存在、讲了什么），总结：好空的判定标准（是不是核心概念、答案是否唯一、上下文能不能推出、学生不看原文能不能答）、常见失败（挖到无关词、多个答案都说得通、太容易/太难）、有没有人工或程序能做的质量检查；有没有中文的做法。
  2. 要点回想、逐条对照答案自评：查 Anki 等间隔重复软件的自评做法、有没有“把答案拆成要点逐条勾”的开源项目或插件，以及关于“有评分要点（rubric）时自评准不准”的研究结论（自评偏高多少、怎么减少）。
  3. 过关制度：对比我们的“两道题、升三级”与 Leitner 盒子、SM-2/FSRS、掌握学习（mastery learning）等的做法：一个知识点要答对几次、隔多久、题目重复会不会只是背答案；如果改成“资料里有几道就考几道、每道都要答对，没题的用要点回想或填空”，有什么依据和风险。
  4. 给出建议：每条写做法、出处、许可证、放进我们流程哪一步、解决什么问题、风险；明确写哪些做法不适合我们（例如完全靠 AI 判分）。
完成标准：三个链接核实；三个问题各至少 3 个可靠出处；每条建议有出处。
验收：报告写到 D:\MentorFlow\.local\test-tmp\research68\report.md。
回执（尽量短，用表格）：【MF-04｜调研｜第六十八批｜完成/部分完成/受阻】三个链接核实结果 ／ 挖空：好空的标准、常见失败、可用的检查（各附出处）／ 要点自评：现成做法与自评准确度结论 ／ 过关制度：几种做法对比与建议 ／ 不适合我们的做法 ／ 本地最终占用
~~~

## 第六十七批任务原文（2026-10-06，基线 2653eb65）

### [67-MF01] server｜非知识内容不进计划，提前标出资料里没有现成题的模块

~~~text
【MentorFlow 第六十七批｜MF-01（工位 server）｜结构这一步：导航、求职、学习路线这类非知识内容不进计划，并提前告诉用户哪些模块资料里没有现成题】

现成项目与许可证（第一节）
  - 只改本项目现有的结构这一步：course/planner_agents.py（MATERIAL_STRUCTURE_SYSTEM、parse_material_structure）、app/plan_flow.py（material_structure、63/65 的预估）、web/src/pages/NewPlan.tsx 与 planning/PlanGraph.tsx（结构图卡片）。资料里有没有自带题，复用 course/point_materials.py 里已有的“资料原题”识别（自己查，只读调用，不改它）；没有可复用的就只报告，不自己写新规则。不新增依赖。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard 2653eb65
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell 亲测：只读，不改不删，要用的数据复制到测试目录再用。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：所有产物在 D:\MentorFlow\.local\test-tmp\fix67-structure\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
同时在跑：67-MF05（library，改找题，不碰你的文件）。
目标一小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建、离线完整场景由你跑并如实写结果。花费上限 ¥0.3（只为下面第 4 条真实跑一次结构这一步）。
背景（CURRENT_STATE [DIR-01]“找题”，用户 2026-10-06 定）：用户的 JavaGuide 计划里有“导航/学习路线”“求职”这类模块，点是“了解资料入口”“怎么安排学习”之类，本来就不是知识、也没有题可找；66-MF05 试备题在这类点上花了 ¥1.6 一道题没找到。用户定：这类模块直接删掉；而且要在用户确认结构之前就提前告诉他，哪些模块资料里没有现成题、需要上网找（会慢、会花钱），让他心里有数。
要做
  1. 结构提示里写清：资料里的导航、目录说明、如何使用本资料、学习路线/学习计划安排、求职/简历/面试心态与流程、推广与关于作者等，不是学习内容，不分进任何模块；单独列在返回结果的一个新字段里（例如 excluded，写名字和一句原因），不算 missing。面试中会被问到的真实知识（例如“项目中如何用 Redis”）仍是知识，要保留。
  2. 结构图卡片下方用一行灰字列出“这些内容不是知识，未放进计划：……”，用户可以在对话里说“把××也加进来”恢复（走现有调整结构）。
  3. 每个模块标出“资料自带题”的情况：复用现有的资料原题识别，统计该模块章节里能识别出的带答案题数量；0 道的模块在节点和详情里标“资料里没有现成题，需要上网找题，备题会慢一些、花费多一些”。若现有代码里没有可复用的识别，就不做这一条，回执写明。
  4. 用复制出来的用户草稿（JavaGuide，“ai-agent学习”）真实跑一次结构这一步，报告：模块列表、excluded 列表（导航、求职等应在里面）、每个模块的资料自带题数量。
  5. 全量通过、web 构建通过、离线完整场景（--to free）通过；结构卡片截一张图。
完成标准：导航、求职、学习路线类内容不再成为模块并在卡片上说明；每个模块的资料自带题情况在确认前可见（或写明无法复用）；全量、构建、离线完整场景通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix67-structure\report.md（附截图）。
回执（尽量短，用表格）：【MF-01｜server｜第六十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 排除哪些内容、怎么显示 ／ 资料自带题怎么统计、每模块数量 ／ 用户草稿真实一次的模块与排除列表 ／ 全量、web 构建、离线完整场景 ／ 花费 ／ 截图路径 ／ 本地最终占用
~~~

### [67-MF05] library｜找题先按模块整体找，网页先由程序切出题块

~~~text
【MentorFlow 第六十七批｜MF-05（工位 library）｜找题改成先按模块整体找：一个模块搜一两次、找整套带答案的题，再分给各个点；单点补搜只给剩下的点，并把网页先由程序筛出题目再给模型】

现成项目与许可证（第一节）
  - 改本项目现有的备题流程：course/point_materials.py（prepare_module）、course/research*.py、course/question_bank.py。网页抓取、分档沿用现有 ResearchPipeline 与 source_quality。做法参考用户给的 good-learning-skill（MIT，references/prepare-and-plan.md：先收集整套试卷/题源，再按知识点归类）与 59-MF04 调研结论（D:\MentorFlow\.local\test-tmp\research59\report.md）。不新增依赖。

开工同步
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard 2653eb65
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户的 D:\MentorFlowData\account-shell 只读：要用的草稿、资料、知识点复制到测试目录，只在副本上跑。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：所有产物在 D:\MentorFlow\.local\test-tmp\fix67-find\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
同时在跑：67-MF01（server，结构这一步，不碰你的文件）。
目标两小时内交回。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建、离线完整场景由你跑并如实写结果。
背景（CURRENT_STATE [DIR-01]“找题”，用户 2026-10-06 定）：66-MF05 在用户草稿副本上试备题，每个点单独搜：每点 8 次模型调用、约 40 万输入 token（一半未命中缓存）、22 次搜索，约 ¥0.61、184 秒，541 点要约 ¥327、28 小时，用户认为太离谱。钱主要花在每个点各自搜索、把整页网页内容塞进模型。用户定：先按模块找。原则不变：只找现成带答案的客观题，越权威越靠前，不让 AI 出题，计分题不挖空。
要做
  1. 先量清楚：用 66-MF05 保存的记录（D:\MentorFlow\.local\test-tmp\exp66-find\），列出每点 8 次调用各是什么、输入 token 花在哪（搜索返回的网页、资料原文、提示），回执给一张表。
  2. 按模块找：备一个模块时，先用模块名和它的知识点清单做 1–2 次搜索（例如“JVM 面试题 选择题 答案”），找整套带答案的题（题库页、真题卷、教材习题）；网页抓回来后先由程序切出“像题目的块”（有题干、选项或答案标记的段落），只把这些块和本模块知识点清单交给模型，让它做“哪道题对应哪个点、答案是什么（必须在原页上逐字找到）”的配对；配上的题照现有规则检查入库。
  3. 单点补搜：模块整体找完后，仍不到 2 道的点才单独补搜，每点最多 1 轮；同样先由程序切题块再给模型。
  4. 不是知识的点（例如“了解资料入口”）不要去搜：如果 67-MF01 还没合入，你这边遇到掌握目标明显是“了解资料/学习路线”的点，标“非知识点，跳过找题”，回执列出。
  5. 在副本上用“Java 后端核心与计算机基础”模块的前 10 个点真实跑新做法，和 66-MF05 的老做法比：每点花费、用时、模型调用次数、输入 token、找到的计分题数、达到 2 道的点数；人工核对每道入库的题（答案对不对、是否唯一、是否考到目标、题面是否完整、有没有重复）。
  6. 全量通过、web 构建通过、离线完整场景（--to free）通过。
花费：上限 ¥3（价格按高峰折算），到上限就停。
完成标准：新做法在 10 个点上每点平均花费比老做法（¥0.61）低一半以上，并且找到的合格计分题不比老做法少；达不到就写清卡在哪、建议下一步，不自行加检查环节。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix67-find\report.md，附逐题核对表。
回执（尽量短，用表格）：【MF-05｜library｜第六十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 老做法 token 花在哪 ／ 新做法怎么找、怎么切题块 ／ 新老对比（每点花费、用时、调用次数、输入 token、计分题数、达到 2 道的点数）／ 不合格题 ／ 全量、web 构建、离线完整场景 ／ 花费 ／ 本地最终占用
~~~

## 第六十六批任务原文（2026-10-06，基线 9fac6a46）

### [66-MF01] server｜备题改成“学一个、备下一个”，不出跨模块重复题

~~~text
【MentorFlow 第六十六批｜MF-01（工位 server）｜备题改成“学一个、备下一个”：开课时只备第 1、2 模块，进入第 N 模块时提前备第 N+1 模块；不出跨模块重复题】

现成项目与许可证（第一节）
  - 只改本项目现有代码：app/plan_flow.py（generate、_prepare_remaining、_record_preparation）、课堂里确定“当前模块”的地方（app/classroom.py 或 app 层对应入口，自己查）、必要时 course/question_bank.py 的入库去重。备题本身仍用 course/point_materials.py 的 prepare_module，不改找题逻辑。不新增依赖。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard 9fac6a46
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell 亲测：只读，不改不删。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：所有产物在 D:\MentorFlow\.local\test-tmp\fix66-prepare\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
同时在跑：66-MF05（在用户草稿副本上试备 10 个点，只读实验，不改代码）。
目标一小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建、离线完整场景由你跑并如实写结果。花费 ¥0（不调用真实模型）。
背景（CURRENT_STATE [DIR-01]“找题”下“备题这一步的风险”，用户 2026-10-06 定）：现在 generate 备好第 1 模块后，_prepare_remaining 立刻在后台把其余所有模块依次备题，一次花掉全部备题的钱（用户草稿约 987 点，备题是最贵的一步），中途一个模块不达标就停住、后面都不备。用户定：一个模块一个模块备——开始学第 1 模块时提前备好第 2 模块，之后每次只提前备一个模块；用户还担心后面模块备出和前面重复的题，要在这次一起看。缺题怎么兜底另行讨论，本任务不改 _module_ready 的规则。
要做
  1. generate：知识地图入库照旧；同步备第 1 模块（照旧）；第 1 模块备好、计划就绪后，后台只备第 2 模块，不再一次备全部。
  2. 进入第 N 模块（学习者第一次在课堂里开始这个模块的第一个点，或这个模块成为当前模块——按代码里最稳的那个时机，回执写明）时，如果第 N+1 模块还没备好、也没在备，就在后台开始备它。同一时刻同一个计划最多一个后台备题。已经备好的模块不重备。
  3. 某个模块后台备题失败：记为 failed，不影响已经备好的模块；轮到它的前一个模块被进入时（或进入它自己时）再试一次；课堂里进入一个还没备好的模块时，显示“这个模块的题还在准备”，并立刻开始备（不卡死界面）。
  4. 跨模块重复题：先查现有代码是否已经防止同一计划里两道题题面相同（规范化：去空白、全半角、标点）；没有的话，在入库时遇到题面与本计划已有题相同的就跳过这道（不算这个点的题），回执写明怎么判断“相同”、跳过了几道。
  5. 关掉软件再打开：已经备好的模块不丢；正在备的模块下次打开时，如果它是“当前模块的下一个”，重新开始备。
  6. 离线测试：三模块假计划，验证开课时只备了第 1、2 模块；进入第 2 模块时开始备第 3 模块；第 2 模块失败后进入它会重试；跨模块同题面只入库一次；重开后状态正确。
  7. 全量通过、web 构建通过、离线完整场景（--to free）通过。
完成标准：任何时候最多只提前备一个模块；上面五种情况的离线测试通过；全量、构建、离线完整场景通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix66-prepare\report.md。
回执（尽量短，用表格）：【MF-01｜server｜第六十六批｜完成/部分完成/受阻】基线 ／ 提交 ／ 什么时机备下一个模块 ／ 失败与重试 ／ 跨模块重复怎么判、原来有没有 ／ 重开后的行为 ／ 全量、web 构建、离线完整场景 ／ 本地最终占用
~~~

### [66-MF05] 实验｜用户草稿副本上给第 1 模块 10 个点试备题

~~~text
【MentorFlow 第六十六批｜MF-05（工位 library）｜实验：在用户草稿副本上给第 1 模块的 10 个点试备题，看能从资料拿到几道、上网找到几道、错几道、每点花多少钱】

现成项目与许可证（第一节）
  - 只用本项目现有的备题流程 course/point_materials.py 的 prepare_module（59-MF05 恢复的“资料原题 → 上网找现成题”，计分题不挖空），不改产品代码；实验脚本放在 D:\MentorFlow\.local\test-tmp\exp66-find\ 下（不进仓库）。不新增依赖。

前提：用户已在软件里把草稿“ai-agent学习”填完知识点（停在知识地图这一步，没点“保存与准备”）。开工前先确认这一点：草稿 phase 是 outline 且有知识点；不是就停下回报。

开工同步
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard 9fac6a46
测试用 D:\MentorFlow\.venv\Scripts\python.exe。本任务不提交代码。
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户的 D:\MentorFlowData\account-shell 只读：把草稿与它用到的资料、知识点数据复制到实验目录，只在副本上跑。先读 AGENTS.md（磁盘规则）：所有产物在 D:\MentorFlow\.local\test-tmp\exp66-find\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
同时在跑：66-MF01（改备题时机，server 工位，不碰你的东西）。
目标一小时内交回。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。
背景（CURRENT_STATE [DIR-01]“找题”）：题目先用资料自带的带答案题，没有才上网找现成带答案客观题（越权威越靠前），计分题不挖空。第五十九批没跑到出题，所以找题的命中率、质量和单价都没测过；备题是最贵的一步（以前每点约 ¥0.12–0.17），用户草稿约 987 点，用户要求先小规模试。
要做
  1. 在副本上对第 1 模块的前 10 个点运行现有备题流程（用管理员 DeepSeek 预设，Key 只经现有代码从 Windows 凭据读取）。
  2. 每个点记录：从资料拿到几道、上网找到几道（来源、档位、链接）、最后入库几道计分题、是否达到 2 道；用时；花费（输入/输出/思考/搜索次数与金额）。
  3. 人工核对每一道入库的题：答案对不对、是否唯一（多个选项都对算不合格）、是否考到这个点的掌握目标、题面是否完整（缺图、缺上下文），以及有没有和别的点重复的题。
  4. 推算：每点平均花费与用时；按这个单价，第 1 模块全部点、全部 987 点各要多少钱、多少时间。
  5. 没到 2 道的点，写清卡在哪（资料里没题、搜不到、搜到的没答案、被检查删掉）。
花费：上限 ¥2（价格按高峰折算），到上限就停。
完成标准：10 个点跑完（或到上限停下）；每道题有人工核对结论；单价与推算写进报告。
验收：报告写到 D:\MentorFlow\.local\test-tmp\exp66-find\report.md，附逐题核对表。
回执（尽量短，用表格）：【MF-05｜实验｜第六十六批｜完成/部分完成/受阻】10 个点：资料题、网上题、入库数、达到 2 道的点数 ／ 不合格题（答案错、多个都对、没考到目标、题面残缺、重复）／ 没到 2 道的原因 ／ 每点平均花费与用时 ／ 推算第 1 模块与全部 987 点的花费与时间 ／ 本地最终占用
~~~

## 第六十五批任务原文（2026-10-06，基线 60116a27）

### [65-MF01] server｜填点前预估按一般情况、真完不成才严重警告；顶部日期跟表单

~~~text
【MentorFlow 第六十五批｜MF-01（工位 server）｜填点前预估别太保守：按一般情况显示和给按钮，真完不成才严重警告；顶部目标里的旧日期跟着表单走】

现成项目与许可证（第一节）
  - 只改本项目现有代码：app/plan_flow.py（63-MF01 的 structure_ranges、比例常量、map_ranges、adjust_window）、web/src/pages/NewPlan.tsx（结构步预估卡片、顶部标题区）、必要时 course/planner_agents.py 里生成需求摘要的提示。不新增依赖。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard 60116a27
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell 亲测：只读，不改不删，要用的数据复制到测试目录再用。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：所有产物在 D:\MentorFlow\.local\test-tmp\fix65-estimate\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
用户在等着接着测，目标半小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建、离线完整场景由你跑并如实写结果。花费 ¥0（不调用真实模型）。
背景（CURRENT_STATE [DIR-01]“建计划”，用户 2026-10-06 定）：63-MF01 的填点前预估按每点 300–1700 字给范围，但超限提醒和按钮都按最坏情况（300 字/点）算。用户草稿（JavaGuide，8 模块，10-06 至 10-30，每日上限 600）一般情况约每天 500 分钟、不超，却出了“超过 600”的警告和“提高到 780 分钟 / 延到 11-07”的按钮。用户定：不要这么保守，真正判断完成不了才严重警告。另：顶部目标描述还写着旧的“2026-10-06 至 2026-10-15 的 10 天内”，用户已把日期改到 10-30，显示没跟着变。
要做
  1. 一般情况的比例：用 63-MF01 的 4 份校准记录加 64-MF01 实测（JavaGuide AI 核心模块 28813 字 / 62 点 ≈ 465 字/点，报告 D:\MentorFlow\.local\test-tmp\exp64-javaguide\report.md），取中位数作为“一般情况”的每点字数（注释写明 5 份记录和日期）；低、高仍用于范围。
  2. 预估卡片主数字按一般情况显示（每模块点数、总时长、每天负担），范围放在下面一行小字作参考。
  3. 三档提示：
     - 一般情况每天负担 ≤ 每日上限：不提示，不显示调整按钮（用户仍可从“调整结构”或对话里改）；
     - 一般情况超上限、但最乐观（低估）不超：一行轻提示“可能会超出每日上限，填完知识点后会按实际点数再算一次”，附三个调整按钮但样式为次要；
     - 连最乐观都超上限：黄色严重警告“按最乐观的估计也完成不了”，给三个调整按钮。
  4. 按钮数字按一般情况算：“把每天时长提高到 N 分钟”（N = 一般情况每天需要的分钟，取整到 10）、“把结束日期延到某天”（按用户上限、一般情况算）。填完知识点后的精确预估（map_ranges）照旧按实际点数，只在超上限时提示。
  5. 顶部目标里的旧日期：顶部标题下方的日期与每日时长由程序按表单生成（例如“2026-10-06 至 2026-10-30 · 每天最多 600 分钟”）；需求摘要文字里的日期/天数说法在显示时去掉，并在生成需求摘要的提示里写明“不要写具体日期和天数，日期由表单决定”。改表单后顶部立即更新。
  6. 用复制出来的用户草稿离线验证：改后不出警告、主数字约每天 500 分钟（写出实际数）；再造两种情况各验一次：每日上限 450（应为轻提示）、上限 200（应为严重警告），写出三种情况的显示文字和按钮数字。
  7. 全量通过、web 构建通过、离线完整场景（--to free）通过；三种情况各截一张图放进报告。
完成标准：主数字按一般情况；三档提示按上面规则；按钮数字按一般情况；顶部日期跟表单走；全量、构建、离线完整场景通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix65-estimate\report.md（附截图）。
回执（尽量短，用表格）：【MF-01｜server｜第六十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 一般情况比例（5 份记录与中位数）／ 三档提示的规则与三种情况的显示和按钮数字 ／ 用户草稿改后的主数字 ／ 顶部日期怎么改 ／ 全量、web 构建、离线完整场景 ／ 截图路径 ／ 本地最终占用
~~~

## 第六十四批任务原文（2026-10-06，基线 757371b4）

### [64-MF01] 实验｜用户草稿副本上试填一个模块，推算 JavaGuide 全部模块

~~~text
【MentorFlow 第六十四批｜MF-01（工位 server）｜实验：在用户草稿的副本上试填一个模块，算出 JavaGuide 每点多少字，推算全部 8 个模块的点数与时长】

现成项目与许可证（第一节）
  - 只用本项目现有的填知识点流程（app/plan_flow.py 的 make_outline 与按模块分批生成、course/planner_agents.py）和 63-MF01 的预估函数；不改产品代码，实验脚本放在 D:\MentorFlow\.local\test-tmp\exp64-javaguide\ 下（不进仓库）。不新增依赖。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard 757371b4
测试用 D:\MentorFlow\.venv\Scripts\python.exe。本任务不提交代码。
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell 亲测：只读，不改不删；把用户草稿“ai-agent学习”和它用到的资料数据复制到实验目录，只在副本上跑。先读 AGENTS.md（磁盘规则）：所有产物在 D:\MentorFlow\.local\test-tmp\exp64-javaguide\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
同时在跑：64-MF02（client，修步骤条溢出，不碰你的东西）。
目标一小时内交回。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。
背景（CURRENT_STATE [DIR-01]“建计划”）：63-MF01 的填点前预估按“每点 300–1700 字”估，用户的 JavaGuide 草稿（8 个模块）估出 272–1530 点，区间太宽没有参考价值。用户定：这是一次实验，只为这份资料把估算收窄、看清真实量级；正式功能仍是填完全部模块后按实际点数精确估算，本任务不改产品代码。用户已把草稿周期改为 15 天（2026-10-06 至 2026-10-20），每日上限 400 分钟。
要做
  1. 在副本上只给一个模块真实填知识点：选“Java 后端核心与计算机基础”（63-MF01 估 8–44 点）；如果它展开后的章节字数不是 8 个模块里中等偏小的，就换一个中等偏小、内容和其余模块相近的模块，回执写明选哪个、为什么。用管理员 DeepSeek 预设（Key 只经现有代码从 Windows 凭据读取）。
  2. 算出这一块的“每点字数”，用它推算其余 7 个模块的点数；再按每点学习 8 分钟、模块检测与最终检验按现有算法，算总时长、15 天里每天负担、是否超 400、若超了“每天需要多少分钟”和“按 400 要延到哪天”。
  3. 人工看一眼填出来的点：有没有明显重复、太碎（一句话一个点）或太粗（一个点塞一整章），写 3–5 句结论；这影响推算是否可信。
  4. 把这次的字数与点数加进 63-MF01 的校准记录表（报告里列出，不改代码常量），说明 JavaGuide 这类“问答文章”资料的比例落在区间哪里。
花费：上限 ¥1（价格按高峰折算），到上限就停。
完成标准：一个模块真实填完；8 个模块的推算点数与 15 天每天负担写进报告；点的粒度有人工结论。
验收：报告写到 D:\MentorFlow\.local\test-tmp\exp64-javaguide\report.md。
回执（尽量短，用表格）：【MF-01｜实验｜第六十四批｜完成/部分完成/受阻】试填哪个模块、为什么 ／ 字数、点数、每点字数 ／ 8 个模块推算点数 ／ 总时长、15 天每天负担、是否超 400、两个选项的数字 ／ 点的粒度结论 ／ 花费 ／ 本地最终占用
~~~

### [64-MF02] client｜步骤条窄窗溢出

~~~text
【MentorFlow 第六十四批｜MF-02（工位 client）｜新建计划顶部步骤条在窗口窄时挤出去】

现成项目与许可证（第一节）
  - 只改 refactor/learning/web/src/pages/NewPlan.tsx 顶部步骤条与对应样式（CSS），不新增依赖。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard 757371b4
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。D:\MentorFlowData\account-shell 只读。先读 AGENTS.md（磁盘规则）：所有产物在 D:\MentorFlow\.local\test-tmp\fix64-stepbar\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。
同时在跑：64-MF01（只读实验，不改代码）。
目标半小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建由你跑并如实写结果。花费 ¥0。
背景：63-MF01 报告记录，新建计划页面顶部的 6 步步骤条（说清目标与模块 → … → 确认开始）在窗口较窄时溢出（用户截图里第 6 步“确认开始”已经掉到第二行）。
要做
  1. 步骤条在任何窗口宽度下都不溢出、不遮挡标题和“放弃”按钮：宽时一行排开；窄时自动换成紧凑样式（例如只显示序号圆点 + 当前步骤名，其余步骤名悬停显示），或可横向滚动且当前步骤自动滚到可见处——选一种，回执写理由。
  2. 亮色、暗色各在 1280、1000、800、600 像素宽截图（共 8 张）放进报告。
  3. 全量通过、web 构建通过。
完成标准：四种宽度下都不溢出、当前步骤清楚可见；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix64-stepbar\report.md（附截图）。
回执（尽量短，用表格）：【MF-02｜client｜第六十四批｜完成/部分完成/受阻】基线 ／ 提交 ／ 窄窗怎么显示、为什么 ／ 全量、web 构建 ／ 截图路径 ／ 本地最终占用
~~~

## 第六十三批任务原文（2026-10-06，基线 65f70d20）

### [63-MF01] server｜填知识点之前先给时间预估

~~~text
【MentorFlow 第六十三批｜MF-01（工位 server）｜填知识点之前先给时间预估：按资料字数估点数、每点 8 分钟、按用户自己的每日上限提醒，超了给选择且不撤掉已生成的东西】

现成项目与许可证（第一节）
  - 只用本项目现有代码：估时 app/plan_flow.py（MINUTES_PER_POINT_GUESS、with_checks、minute_ranges、map_ranges）、账本 course/ledger.py（distribute、full_day_minutes、check_cap、estimate_check_minutes，只调用不改算法）、结构这一步 material_structure 与 Section 的 chars、前端 refactor/learning/web/src/pages/NewPlan.tsx（结构图卡片、EstimateCard）与 planning/PlanGraph.tsx。改表单走现有的计划对话意图与表单校验（course/plan_intent.py、plan_flow 的 chat/_carry_out），不另写一套。不新增依赖。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard 65f70d20
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell 亲测：只读，不改不删，要用的数据复制到测试目录再用。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：所有产物在 D:\MentorFlow\.local\test-tmp\fix63-estimate\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
用户在等着接着测，目标一小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建、离线完整场景由你跑并如实写结果。花费 ¥0（不调用真实模型）。
背景（CURRENT_STATE [DIR-01]“建计划”，用户 2026-10-06 定）：现在点“确认结构，开始填知识点”会直接给所有模块填知识点（最花钱），填完才按“点数 × 15 分钟”估时，用户看到“学不完”时钱已经花了。用户定：
  ① 填知识点之前就要给时间预估；
  ② 学习按每点约 8 分钟估（只改估时，一个点切多大不变）；检测估时保持现状：模块检测每点 1 题 × 2 分钟，最终检验每点约 2.6 分钟（90% 答 1 题、10% 答 4 题、每题 2 分钟）——用户认为每题 2 分钟已经把看错题的时间摊进去了，不再加；
  ③ 提醒按用户表单里自己的每日上限（这次是 400），不是固定 300；
  ④ 超了要给选择：提高每天时长、延长周期、或调整内容（删模块）；已经生成的东西（结构、知识点）不要撤掉重来。
  已知缺陷：map_ranges 里“建议延到哪天”和 over_300 用的是 min(daily_cap, 300)，要改成用户的上限。
要做
  1. 校准“多少字出一个点”：用已有的真实记录算资料字数与生成点数的比例，至少用 D:\MentorFlow\.local\test-tmp\small57（小测试）和 find59b（Google 机器学习术语表，162 点）两份；能找到的其他真实生成记录也算上。得出一个比例区间（低、高），写进代码常量并在注释里注明来源与日期。回执写清每份记录的字数、点数和比例。
  2. 结构这一步加粗略预估（不调模型）：按每个模块展开后的章节字数 ÷ 比例区间，估每个模块的点数范围，再按 ② 算学习、模块检测、最终检验和总时长，按表单的日期与可学日算每天负担。结构图下方显示：总时长范围、每天约多少分钟、对比用户的每日上限；在模块详情里显示该模块估计点数。顺带显示“填知识点大约要用的输入 token”（按字数折算，只显示 token，SYS-03 不显示金额）。
  3. 超出上限时，在“确认结构，开始填知识点”旁边给三个选择：
     - “把每天时长提高到 N 分钟”（N = 按高估算每天需要的分钟，取整到 10）；
     - “把结束日期延到 YYYY-MM-DD”（按用户自己的上限算）；
     - “调整内容”（定位到“调整结构”，用户可以删模块）。
     前两个直接改表单（走现有表单校验），改完重新算预估；结构和已生成内容都保留，不重新生成结构。用户也可以不理会，直接点确认继续。
  4. 填完知识点后的精确预估（map_ranges / EstimateCard）同样改成每点 8 分钟、按用户上限，超了给同样的前两个选择；改日期或每日时长不清掉已生成的知识地图。
  5. 检查并保证：在结构这一步和知识地图这一步改每日时长或结束日期，不会把 material_structure、知识地图或已采用资料作废；如果现有代码会作废，改成只重算预估。写一条测试锁住。
  6. 用复制出来的用户草稿（“ai-agent学习”，JavaGuide，8 个模块，10 天，每日上限 400）离线算一遍，报告：每个模块估计点数、总时长范围、每天负担、是否超 400、三个选项给出的具体数字。
  7. 全量通过、web 构建通过、离线完整场景（--to free）通过；结构步预估卡片与超出时的选项各截一张图放进报告。
完成标准：确认结构之前就能看到时长预估；每点 8 分钟、按用户上限；超出时三个选项可用，改表单后结构与已生成内容保留；用户草稿的离线预估写进报告；全量、构建、离线完整场景通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix63-estimate\report.md（附截图）。
回执（尽量短，用表格）：【MF-01｜server｜第六十三批｜完成/部分完成/受阻】基线 ／ 提交 ／ 字数与点数比例（每份记录与最终区间）／ 结构步预估显示什么 ／ 超出时三个选项与改表单后保留了什么 ／ 用户草稿的离线预估（各模块点数、总时长、每天负担、是否超 400）／ 全量、web 构建、离线完整场景 ／ 截图路径 ／ 本地最终占用
~~~

## 第六十二批任务原文（2026-10-06，基线 21c73e2f）

### [62-MF01] server｜结构这一步收拢后的目录带上小标题，覆盖判断才准

~~~text
【MentorFlow 第六十二批｜MF-01（工位 server）｜结构这一步模型“没看清资料”：收拢后的目录条目要带上它下面的小标题，覆盖判断和分模块才准】

现成项目与许可证（第一节）
  - 只改本项目现有的资料结构这一步：course/planner_agents.py（60-MF01 加的目录收拢、material_structure_request、MATERIAL_STRUCTURE_SYSTEM）、必要时 app/plan_flow.py 的 material_structure。不新增依赖。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard 21c73e2f
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell 亲测：只读，不改不删，要用的数据复制到测试目录再用。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：所有产物在 D:\MentorFlow\.local\test-tmp\fix62-coverage\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
同时在跑：62-MF02（client，只改前端图的详情面板，不碰你的文件）。
用户在等着接着测，尽快，目标半小时内交回。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。
背景（CURRENT_STATE [PLAN-04] 用户亲测）：60-MF01 把 JavaGuide 的目录从 906 条收拢到 69 条后，这一步不再超长，但“资料没讲到”列出了 AI Agent 核心概念、Java 并发/JVM/Spring/消息队列/缓存/MySQL 等——JavaGuide 明明都有（收拢前章节里有“大模型基础 / Agent / ReAct、MCP、RAG”等）。判断：模型只看到上层标题（如“大模型基础”“Java”），看不到下面讲了什么，就判成没讲到。后果：用户被误导去补资料，或点“删掉没讲到的部分并继续”把本来有的目标删掉；模型也可能因为看不清而把整块相关条目漏出所有模块，那一块就不会生成知识点。
要做
  1. 收拢后的每个目录条目附带它下面的小标题名（只带名字，不带正文），按层级从上往下取，每条最多约 30 个名字、超出写“等 N 个”；整份目录给模型的部分控制在约 1.5 万 token 以内（资料再大也不超，自动减少每条附带的名字数）。模型仍只回条目编号，程序展开，60-MF01 的其余做法不变。
  2. 提示里写清：判断“资料没讲到”要看条目下面的小标题，只有目标里的内容在整份目录（含小标题）里都找不到才列入 missing。
  3. 复现与验证：复制用户这份草稿（目标“Java 后端 + AI Agent 面试”，JavaGuide），修前离线确认请求里看不到 Agent、JVM 等小标题；修后用真实模型只跑这一步一次，报告：输入/输出/思考用量、耗时、模块数与模块名、missing 原文。完成标准里 missing 不能再出现 JavaGuide 有对应章节的内容（AI Agent、ReAct、MCP、RAG、Java 并发、JVM、Spring、消息队列、缓存、MySQL 等），逐条对照章节名写进报告。再离线回放 small57 删除范围，确认删掉的内容仍不被加回。
  4. 全量通过、web 构建通过。
花费：上限 ¥0.3。
完成标准：真实一次成功、不超上限；missing 里没有资料其实有的内容；删除范围回放通过；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix62-coverage\report.md。
回执（尽量短，用表格）：【MF-01｜server｜第六十二批｜完成/部分完成/受阻】基线 ／ 提交 ／ 条目怎么带小标题、总量怎么控 ／ 修前修后：输入 token、输出与思考、耗时、模块数与模块名 ／ missing 修前修后原文与逐条对照 ／ 全量、web 构建 ／ 花费 ／ 本地最终占用
~~~

### [62-MF02] client｜结构图点模块后详情面板自己消失

~~~text
【MentorFlow 第六十二批｜MF-02（工位 client）｜结构图点模块后详情面板自己消失：面板要留住，并显示能看到的内容】

现成项目与许可证（第一节）
  - 只改你上一批写的 refactor/learning/web/src/planning/PlanGraph.tsx、plan-graph.css 与 NewPlan.tsx 里调用它的地方（React Flow @xyflow/react，MIT，已安装）。不新增依赖。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard 21c73e2f
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell 亲测：只读，不改不删。先读 AGENTS.md（磁盘规则）：所有产物在 D:\MentorFlow\.local\test-tmp\fix62-graph-panel\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。
同时在跑：62-MF01（server，只改后端结构请求，不碰你的文件）。
用户在等着接着测，尽快，目标半小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建由你跑并如实写结果。花费 ¥0。
背景（CURRENT_STATE [PLAN-04] 用户亲测）：在真实软件（Qt WebEngine 里）的“按资料确认结构”图上点一个模块，右侧详情面板弹出来又立刻自己消失，用户以为是 bug。用户认可这一步不显示知识点，但点开后要能看到能看的内容，不能一点就没了。
要做
  1. 查清面板为什么自己消失（例如点节点的事件冒泡到画布、画布点击把选中清空，或重新渲染/自动适配全图时选中状态被重置），在回执里一句话写明原因，从根上修，不加延时之类的绕法。
  2. 面板点开后一直留着，直到用户点面板上明显的“关闭”按钮、按 Esc、或点另一个模块（切换到那个模块）。点画布空白处不要关。
  3. 面板内容：结构这一步显示模块名、“学什么”、依据的资料章节（全部列出，可滚动）、预计知识点会从这些章节生成的一句说明；知识地图这一步照上一批显示全部知识点。内容多时面板内部滚动，不撑破页面。
  4. 用浏览器测试复现：修前点节点后面板消失、修后面板留住；结构步与知识地图步各测一次；再截两张图（亮色、暗色）放进报告。
  5. 全量通过、web 构建通过。
完成标准：两步里点模块后面板都稳定留住并显示内容；关闭方式按上面三种；全量、构建通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix62-graph-panel\report.md（附截图）。
回执（尽量短，用表格）：【MF-02｜client｜第六十二批｜完成/部分完成/受阻】基线 ／ 提交 ／ 面板消失的原因 ／ 怎么修、关闭方式 ／ 面板显示什么 ／ 复现修前修后 ／ 全量、web 构建 ／ 截图路径 ／ 本地最终占用
~~~

## 第六十一批任务原文（2026-10-06，基线 ca0208dd）

### [61-MF02] client｜结构树与知识地图改成可交互、好看的图

~~~text
【MentorFlow 第六十一批｜MF-02（工位 client）｜结构树与知识地图改成可交互、好看的图：放得下、能拖能缩放能点开，不再是一大段字】

现成项目与许可证（第一节）
  - 画图用项目里已经装好的 @xyflow/react 12.11.3（React Flow，MIT）做可拖动、可缩放的画布，@dagrejs/dagre 3.1.1（MIT）自动排版；图标用已有的 lucide-react（ISC）。不新增依赖。
  - 改 refactor/learning/web/src/pages/NewPlan.tsx（material_preview 卡片、OutlineCard）与对应样式；后端只改 app/plan_flow.py 里生成 material_preview 消息文字的那一段。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard ca0208dd
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell 亲测：只读，不改不删，要用的数据复制出来再用。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：所有产物在 D:\MentorFlow\.local\test-tmp\fix61-structure-view\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
用户在等着接着测，尽快，目标一小时内交回。遇到选择自己决定，回执写明理由，不停下来问。全量测试、web 构建、离线完整场景由你跑并如实写结果。花费 ¥0（不调用真实模型）。
背景（CURRENT_STATE [PLAN-04] 用户亲测反馈）：用户选好资料后，“按资料确认结构”这一步本该给一个简略版的知识地图——只有模块、模块之间连起来的结构树，没有知识点。现在给的是一大段字（每个模块后面列出几十个章节名），翻都翻不完，也不知道点“确认结构，就这样继续”会发生什么；上一次失败的“模型输出达到上限”提示成功后还留在页面上。用户还要求：结构树和后面的完整知识地图都会大到页面放不下，要想办法；图要可交互、要好看，不要吝啬。
要做
  1. 一个共用的图组件（例如 PlanGraph），结构树和知识地图都用它：
     - 放得下：图放在固定高度的画布里（约 420–520 像素高），能拖动、滚轮缩放，打开时自动适配全图（fitView）；右下角缩放按钮、小地图（MiniMap）；右上角“全屏查看”按钮，点开铺满窗口，Esc 或按钮退出。几十个模块、上百个点也不卡。
     - 排版：dagre 自动排，从上到下或从左到右（模块多时选更省地方的方向），阶段用浅色分组框包住；模块之间按顺序和“先学”关系连线，用平滑曲线，先学关系的线带箭头。
     - 交互：鼠标悬停模块高亮它和相连的线；点模块在右侧滑出详情面板：结构树里显示“学什么”和全部依据章节（默认收起，可展开）；知识地图里显示这个模块的全部知识点（名称、掌握目标、出处，原来 OutlineCard 里有的信息都不能丢）。知识点不直接画成节点，避免图太乱；模块节点上用数字徽标写“N 个点”。
  2. 好看（照这个视觉要求做，不要省）：
     - 配色跟软件现在的粉紫主题一致（沿用现有 CSS 变量），亮色与暗色主题都要好看。
     - 模块节点做成圆角卡片：左侧编号圆章、模块名加粗、下一行一句“学什么”（最多两行，超出省略）、底部一排小标签（预计分钟、N 个点或 N 处依据）；轻阴影，悬停时微微上浮、描边变成主题色。
     - 不同阶段用同一色系的不同浅色区分；连线柔和、半透明，被选中的路径变成主题色并有流动动画。
     - 画布背景用淡淡的点阵；节点出现时依次淡入。
     - 中文字体、行距与现在页面一致，不出现文字溢出、遮挡、重叠。
  3. 结构这一步（material_preview）：用上面的图显示 draft.material_structure（version 与消息一致时）；最上面一行“按你的资料整理出 N 个模块”；资料没讲到的目标（missing）单独一个小块“资料没讲到：……”。原来那一大段文字不再显示。按钮“确认结构，就这样继续”改成“确认结构，开始填知识点”，下一行小字：“确认后按每个模块的资料生成知识点，生成完会给你看完整知识地图再确认”。其他按钮行为不变。
  4. 知识地图这一步（OutlineCard）：上面的时长汇总与超时提醒保留，下面的模块树换成同一个图组件。
  5. 后端：plan_flow 生成 material_preview 消息的文字缩短为“已按资料整理出 N 个模块：1. 模块名；2. 模块名……”加 missing 一句，不再逐条列章节（模拟工具与无界面场景读的是这段文字）。受影响的测试按新文字调整。
  6. 过时的错误提示：一条 error 消息之后如果已经有同一步骤成功的 AI 消息，就不再显示这条 error（只在前端渲染时过滤，不删历史）。
  7. 全量通过、web 构建通过、离线完整场景（--to free）通过。用 fix60 的 JavaGuide 结构数据和 small57 / find59b 的知识地图数据（163 点）在构建后的页面里截图：结构树全图、点开一个模块的详情、知识地图全图、全屏、暗色主题，各一张放进报告。
完成标准：结构树和知识地图都是可拖动缩放、能点开详情的图，大计划放得下、不卡；亮暗两种主题截图都好看、无溢出重叠；旧错误提示不再残留；全量、构建、离线完整场景通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix61-structure-view\report.md（附截图）。
回执（尽量短，用表格）：【MF-02｜client｜第六十一批｜完成/部分完成/受阻】基线 ／ 提交 ／ 图怎么放得下（画布、缩放、全屏）／ 交互（悬停、点开详情）／ 视觉做了哪些 ／ 结构这一步与按钮文字 ／ 旧错误提示怎么处理 ／ 全量、web 构建、离线完整场景 ／ 截图路径 ／ 本地最终占用
~~~

## 第六十批任务原文（2026-10-06，基线 e910c914）

### [60-MF01] server｜大资料在“按资料调整结构”时输出超上限

~~~text
【MentorFlow 第六十批｜MF-01（工位 server）｜大资料在“按资料调整结构”时输出超上限：去掉我们自己加的多余负担，让这一步快速出大致模块】

现成项目与许可证（第一节）
  - 只改本项目现有的资料结构这一步：app/plan_flow.py 的 _sections / material_structure、course/planner_agents.py 的 build_sections / material_structure_request / parse_material_structure / MATERIAL_STRUCTURE_SYSTEM。不新增依赖。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard e910c914
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell 亲测：只读，不改不删，要用的数据先复制到你的测试目录再用。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：所有产物在 D:\MentorFlow\.local\test-tmp\fix60-structure\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
用户在等着接着测，尽快，目标半小时内交回。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。
背景（CURRENT_STATE [BUG-53]）：用户目标“Java 后端 + AI Agent 面试”，贴 JavaGuide 仓库（约 577 千字），点“按已提供的 1 份资料调整结构”，报“模型输出达到上限”。这一步只该给出大致模块（不含知识点），输出本来很少。用量记录：两次 outline 调用输入约 6.2 万 token，输出 12288、24576 全部用在思考上（思考 2.3 万字、4.2 万字），没写出答案。原因是我们自己给模型加的负担：
  ① plan_flow._sections 调 build_sections 时把条目上限和层级上限都放开了（max_per_source=总块数、max_depth=None），整个仓库每一级标题都成了目录条目；
  ② 输出格式要求每个模块逐字抄写全部章节名（chapters），parse_material_structure 又要求逐字相等、随后自己按编号重算——抄写纯属多余，还容易抄错被拒。
证据：D:\MentorFlowData\account-shell\logs\mentorflow.log 与该账号 learning\accounts\*\model-usage.sqlite3 里 purpose=outline 的两条（2026-10-05T16:04、16:05 UTC）；用户这份草稿与 JavaGuide 已导入的块也在该目录的数据库里（复制后使用）。
要做
  1. 先用 git log -S 查清 max_depth=None、max_per_source 放开是哪一批、为什么加的，回执一句话写明；修改时保住它当时要解决的问题。
  2. 给模型看的目录收拢：按层级收拢到不超过约 150 条（深层标题并进上一级，内容和块不丢，可按资料大小自动选层级）；模型只按收拢后的条目编号分模块，程序把编号展开到它下面的全部章节和块。用户删掉的模块/章节（removed_content、scope 的 include/exclude，58-MF01 刚改过）在收拢后仍然生效，不能被加回。
  3. 去掉输出里的 chapters：不再要求模型抄章节名，章节名由程序按编号填。
  4. 先不改思考档位（用户定过不靠降思考省钱，SYS-07）；做完 2、3 若真实仍超上限，回执写清输出用在哪，不自行降档。
  5. 复现与验证：用复制出来的用户草稿和 JavaGuide 块，修前离线确认目录条目数与请求大小，修后对比；再用真实模型对这份草稿只跑这一步一次（管理员 DeepSeek 预设，Key 只经现有代码从 Windows 凭据读取），报告输入/输出/思考用量、耗时、给出的模块数和模块名。再用 small57 保存的资料跑一次离线结构请求，确认小资料不受影响。
  6. 全量通过、web 构建通过。
花费：上限 ¥0.5。
完成标准：JavaGuide 这一步真实一次成功给出大致模块，不超上限；删掉的内容不被加回的测试仍通过；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix60-structure\report.md。
回执（尽量短，用表格）：【MF-01｜server｜第六十批｜完成/部分完成/受阻】基线 ／ 提交 ／ 放开上限是哪批为什么 ／ 怎么收拢、怎么展开、删掉的内容怎么保住 ／ 修前修后：目录条目数、输入 token、输出与思考、耗时、模块数与模块名 ／ 全量、web 构建 ／ 花费 ／ 本地最终占用
~~~

## 第五十九批任务原文（2026-10-05，基线 2a2bbd0c）

### [59-MF04] 只读调研｜别人怎么给知识点配题：从资料取题、上网找现成题、核对答案、给模型的出题规则

~~~text
【MentorFlow 第五十九批｜MF-04（只读调研，不用工位）｜别人怎么给知识点配题：从资料取题、上网找现成题、核对答案、给模型的出题规则】

现成项目与许可证（第一节）
  - 本任务就是查现成项目和论文；每条结论都注明项目名、链接、许可证（论文写出处）。

规则
  只读：不改任何仓库（主目录和工位都不动），不提交、不推送；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则）：E 盘不写；所有产物在 D:\MentorFlow\.local\test-tmp\research59\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。
  不调用付费模型（¥0）。遇到选择自己决定，回执写明理由，不停下来问。
背景（我们的产品方向，CURRENT_STATE [DIR-01]“找题”）
  AI 不稳定，所以只让 AI “找”题、不让它出题。每个知识点的计分题先用用户选定资料里自带的带答案题（单选、多选、填空、计算都可以）；资料里没有合适的题，才上网找现成的带答案客观题，越权威的来源越靠前（教材、考试院、大学、官方 > 成熟教育平台 > 其他），权威的找不到再用其他来源。判分由程序做。以前遇到的问题：从原文挖空出选择题，常常“多个选项都对”；网上找来的题有过错题（把 TCP 和 TCP/IP 混写）；拿原文比对来拦错题，9 道错题只拦下 2 道。
要查
  1. 用户指定的两个项目，读到具体文件：
     - https://github.com/yoli-mi/good-learning-skill （references/daily-study.md、prepare-and-plan.md、source-search.md、SKILL.md）
     - https://github.com/Miaotofu01/Study-Mate （.dsh/skills/ 下的 layered-practice、practice-evaluator、evidence-check、resource-scout、lesson-design 等）
     写清：题目从哪来、按知识点怎么组织、给模型的出题/找题规则和提示词原话要点、怎么保证答案对、怎么防“多个选项都对”。
  2. 另外至少 5 个开源项目或论文（中英文都查），覆盖：
     a. 从教材、讲义、PDF 里抽出自带的习题和答案，并对应到知识点；
     b. 按知识点上网检索现成题（公开题库、考试真题、开放课程习题）以及核对找来的题的答案；
     c. 给模型的出题/找题提示词与质量规则（歧义、多个正确选项、干扰项、题面缺图缺上下文）；
     d. 中文公开题源（考试院真题、计算机等级考试样题、开放教材课后题等）哪些能合法取用、怎么取。
  3. 对照我们的流程，给出能直接借用的做法，每条写：是什么、出自哪里、许可证、放进我们流程哪一步、预期解决什么问题、风险。也写明哪些做法不适用、为什么（例如完全靠 AI 出题再自评）。
完成标准：两个指定项目都读到具体文件；另外至少 5 个项目或论文；每条借用建议都有出处和许可证。
验收：报告写到 D:\MentorFlow\.local\test-tmp\research59\report.md。
回执（尽量短，用表格）：【MF-04｜调研｜第五十九批｜完成/部分完成/受阻】查了哪些项目与论文（各一句话 + 许可证）／ 能直接借用的做法（前 5 条，每条一句话 + 放进哪一步）／ 不适用的做法 ／ 本地最终占用
~~~

### [59-MF05] library｜按块测“找题”：从聊需求跑到生成计划，看多少点能从资料拿到题、上网找的题质量如何、答错后找新题

~~~text
【MentorFlow 第五十九批｜MF-05（工位 library）｜按块测“找题”：从聊需求跑到生成计划，看多少点能从资料拿到题、上网找的题质量如何、答错后找新题】

现成项目与许可证（第一节）
  - 本项目现有的模拟工具 tests/refactor/simulation/（42-MF04，用法见其 README：--live、--pause-material、--material-file/--material-url、--to generated）、资料与题目 course/point_materials.py、course/question_bank.py、course/research*.py、course/source_quality.py。
  - 第五十批删掉的“每点上网找题”：合入提交 98869c89（git show 98869c89 看删了什么），从 98869c89^ 的实现恢复，不重写。不新增依赖。

开工同步
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard 2a2bbd0c
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。用户装好的软件 D:\MentorFlowApp 及其数据只读，不改不删。不打包、不发版（内测阶段）。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\find59\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
做法已由架构师和用户定好，照“要做”逐条做，不自行换方案、不另加检查环节；走不通就写清证据交回。细节按 CURRENT_STATE 条目和本任务原则自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。架构师只读你的结论、不重跑测试，所以全量测试、离线完整场景都由你跑并如实写结果。真实运行怎么接管理员的 DeepSeek 预设，参考上次小测试 D:\MentorFlow\.local\test-tmp\small57\report.md 的做法（Key 只经现有代码从 Windows 凭据读取，不查看、不显示、不保存）。
同时在跑：59-MF04（只读调研，不改代码）。
背景（用户 2026-10-05 定，CURRENT_STATE [DIR-01]“找题”）：AI 不稳定，只让它找题、不让它出题。题目主要从用户选定的资料里拿（资料自带的单选、多选、填空、计算题都能用）；资料里没有合适的题才上网找现成的带答案客观题，越权威越靠前，权威的找不到再用其他来源。从原文挖空不算“资料里的题”。这次不跑全程，只按块狠测找题；建计划、找资料两段顺路跑过去。
要做
  1. 恢复找题、关掉挖空：
     - 计分题（bank）来源顺序：资料自带的带答案题 → 资料里没有合适的题时上网找现成带答案客观题，按来源档位排序（教材、考试院、大学、官方 > 成熟教育平台 > 其他），权威的找不到再用其他来源。每道题记下来源（资料原题 / 上网找到）、档位和链接。
     - 备计分题时不再从原文挖空：加一个参数关掉 bank 的挖空，产品默认关；随口问（role=quick）不动。挖空代码先保留，删不删看这次数据再定。
     - 同一点反复答错、题不够时，按同样顺序找新题：先用资料里没用过的题，再上网找。
     - 恢复的代码以 98869c89^ 的实现为准，只改上面三处；受影响的离线测试按新规则调整。
  2. 离线：全量通过；离线完整场景（--to free）通过。
  3. 真实按块测（模拟工具 --live，模拟时钟）：
     - 从聊需求开始，DeepSeek 扮演用户。表单目标：“我不太懂计算机，想把计算机相关的各种术语概念都搞明白（包括常见的 AI 术语），不需要上手操作，要老师用生活化例子讲清楚，以后交流时能说得头头是道”。其余表单项按正常零基础用户填，写进回执。
     - 资料：用 --pause-material，在 AI 推荐的资料里挑正常用户会挑的几份（下载文件或贴链接）；不自己另外找资料。
     - 跑到 generated，让后台给所有知识点备题。点数不限，系统生成多少就是多少。
     - 停点：前 10 个点备题完成时，如果 6 个以上的点一道计分题都没有，停下回报。
     - 模拟答错：抽约三分之一的点，每点模拟连续答错两次（模块检测错、重考又错），触发找新题；可以直接调用题库/课堂接口模拟，不必跑课堂对话。
     - 建计划、找资料两段的问题只记录（P1/P2/P3 各一句话），不修；卡死跑不下去就停下报告卡在哪。
  4. 人工核对每一道计分题：来源（资料原题 / 上网找到，档位、链接）；答案对不对；答案是否唯一（多个选项都对算不合格）；是否考到这个点的掌握目标；题面是否完整（缺图、缺上下文）。
花费：上限 ¥10（价格按高峰折算），到上限就停。
完成标准：恢复找题并关掉 bank 挖空、全量通过；真实跑到所有点备完题（或在停点按规则停下）；下面统计齐全，每道题都有人工核对结论。
验收：报告写到 D:\MentorFlow\.local\test-tmp\find59\report.md，附逐题核对表。
回执（尽量短，用表格）：【MF-05｜library｜第五十九批｜完成/部分完成/受阻】基线 ／ 提交 ／ 怎么恢复、怎么关挖空 ／ 全量、离线完整场景 ／ 跑到哪一步、模块数与点数 ／ 从资料拿到题的点（数量与占比）／ 需要上网找的点：找到几个、一道都找不到的点 ／ 各来源题数与不合格数（资料原题、权威来源、其他来源；不合格按答案错、多个都对、没考到目标、题面残缺分）／ 答错后找新题：成功几次、每次平均花费、不合格几道 ／ 建计划与找资料段的问题 ／ 总花费、每点平均花费 ／ 本地最终占用
~~~

### [59-MF05b] library｜续跑按块测“找题”：模拟工具已放行资料结构的截断重试，从停下的地方接着跑到备完题

~~~text
【MentorFlow 第五十九批｜MF-05（工位 library）｜续跑按块测“找题”：模拟工具已放行资料结构的截断重试，从停下的地方接着跑到备完题】

现成项目与许可证（第一节）
  - 同 59-MF05：本项目模拟工具 tests/refactor/simulation/ 与你恢复的找题代码（已合入主线 110ef0c2）。不新增依赖。

开工同步
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard 110ef0c2
其余规则同 59-MF05（禁止事项、磁盘规则、Key 只经现有代码读取、不另加检查环节、遇到选择自己决定并写明理由）。产物放 D:\MentorFlow\.local\test-tmp\find59b\；上一轮的 find59 目录保留作证据，不改不删。
上次卡住的原因（架构师已修，CURRENT_STATE [BUG-51]）：产品遇到资料结构（outline）输出截断，本来会把上限翻倍重试一次；模拟工具只给 generate、point_materials 放行截断重试，把 outline 的截断当成失败止损了。主线 110ef0c2 已把 outline 加进放行名单。
要做
  1. 接着跑：模拟工具支持就从上一轮已采用 3 份资料后的检查点续跑（先把 find59 里需要的检查点复制到 find59b，再从复制件续跑）；不支持或检查点已被止损污染，就在 find59b 从聊需求重新开始，主题、表单、挑资料方式同 59-MF05。回执写明用了哪种、为什么。
  2. 资料结构翻倍重试后仍然截断：这是产品问题，停下报告（哪一步、上限多少、输出了多少），不修。
  3. 模拟用户嘴上说要上传资料却不操作（上一轮 P3）：照上一轮的做法，用现有选择步骤补贴 AI 推荐过的链接，记录次数。
  4. 跑到 generated 并让后台给所有点备完题之后，按 59-MF05 的“停点”“模拟答错”“人工核对每一道计分题”三项原样做。
  5. 建计划、找资料两段的新问题只记录（P1/P2/P3 各一句话），不修。
花费：本次上限 ¥9（59-MF05 已花约 ¥0.64，合计不超过 ¥10），价格按高峰折算，到上限就停。
完成标准：跑到所有点备完题（或在停点、产品截断处按规则停下）；统计齐全，每道题都有人工核对结论。
验收：报告写到 D:\MentorFlow\.local\test-tmp\find59b\report.md，附逐题核对表。
回执（尽量短，用表格）：【MF-05｜library｜第五十九批续跑｜完成/部分完成/受阻】基线 ／ 续跑还是重开 ／ 资料结构重试结果 ／ 跑到哪一步、模块数与点数 ／ 从资料拿到题的点（数量与占比）／ 需要上网找的点：找到几个、一道都找不到的点 ／ 各来源题数与不合格数（资料原题、权威来源、其他来源；不合格按答案错、多个都对、没考到目标、题面残缺分）／ 答错后找新题：成功几次、每次平均花费、不合格几道 ／ 建计划与找资料段的新问题 ／ 总花费、每点平均花费 ／ 本地最终占用
~~~

## 回执格式

~~~text
【员工｜任务号｜完成/部分完成/受阻】
基线：开工时切到的提交编号。
提交：分支 office/<工位名> 上的提交编号。
改动：修改/新增的文件。
验证：实际运行的命令与结果（通过/失败/跳过）；用了替身的写明。
未完成：没做完或拿不准的地方。
~~~

每条任务还会在末尾列出这次要回报的具体项目。

## 任务索引

只列最近几批；更早的任务原文与索引在 `git show c74ac725:docs/project/WORKFLOW.md`（2026-10-05 精简；再早的在 f2dcc15）。

| 任务 | 员工 | 内容 | 关联需求 | 基线 | 状态 |
| --- | --- | --- | --- | --- | --- |

| 56-MF01 | MF-01 server | 资料结构核对分开“讲法偏好”和“学习内容”：生活化例子等讲法不当成资料缺的章节（¥0.2） | PLAN-06、TEACH-01 | 24dbddd4 | 完成，已合入（a2664eae） |
| 56-MF02 | MF-02 library | 用户给的资料一律当可信：找出并去掉所有按发布者/档位筛资料的旧关卡（¥0.5） | DIR-01 | 24dbddd4 | 部分完成，已合入（1724a2e1）：旧关卡 35 处改 20；真实生成卡在 distractor_check 60 秒超时（架构师已改后台时限） |
| 57-MF02 | MF-02 classroom | 小测试（重跑）：一份小资料、真实模型、模拟时钟，从建计划一直跑到报告与自由检验，只找问题（¥6） | QA-01、DIR-01 | e7c68407 | 完成：第一次跑完全程，P1 3、P2 5、P3 1 |
| 58-MF01 | MF-01 server | 删掉的内容不再被加回；报告不再误称讲稿核对失败 | PLAN-06、RPT-01 | 39a42744 | 完成，已合入 c74ac725（a0e2e777） |
| 58-MF02 | MF-02 library | 错误选项其实成立：检查干扰项时把它出自的原文段落一起给检查器（¥0.3） | DIR-01 | 39a42744 | 部分完成，已合入 c74ac725（fbd0d29c）：真实 4 题拦下 3 题，硬件单选未解决 |
| 58-MF03 | MF-03 classroom | 最终检验隔开重复用题；老师讲解不强制每段标出处、不再贴原文兜底 | DIR-01、CHK-01、TEACH-06 | 39a42744 | 完成，已合入 c74ac725（879af7bd） |
| 59-MF04 | MF-04 调研 | 别人怎么给知识点配题：从资料取题、上网找现成题、核对答案、出题规则（¥0） | DIR-01 | 2a2bbd0c | 完成：9 份指定文件、另 5 个项目 2 篇论文；结论见 .local/test-tmp/research59/report.md |
| 59-MF05 | MF-05 library | 按块测找题：恢复上网找题、关 bank 挖空，从聊需求跑到生成计划（¥10） | DIR-01、QA-01 | 2a2bbd0c | 部分完成，代码已合入 110ef0c2（ca543cf5）；真实测试卡在模拟工具（BUG-51），未到备题 |
| 59-MF05b | MF-05 library | 续跑按块测找题（¥9） | DIR-01、QA-01、BUG-51 | 110ef0c2 | 部分完成：生成 2 模块 163 点，第一个点上网找题截断又被模拟工具止损，未到备题；两轮合计约 ¥5.85 |
| 60-MF01 | MF-01 server | 大资料“按资料调整结构”超上限：目录收拢、去掉抄章节名（¥0.5） | BUG-53、PLAN-04 | e910c914 | 完成，已合入（0ddbbc4d）：目录 906→69 条，真实一次 39.9 秒给出 4 个模块 |
| 61-MF02 | MF-02 client | 结构树与知识地图改成可拖动缩放、可点开详情的好看图；结构步按钮说清下一步、清旧错误提示（¥0） | PLAN-04 | ca0208dd | 完成，已合入（8fb2273a），待用户亲测 |
| 62-MF01 | MF-01 server | 收拢后的目录条目带小标题，“资料没讲到”不再误报（¥0.3） | PLAN-04、BUG-53 | 21c73e2f | 部分完成，已合入（5aca7b76）：带上 726 个小标题后真实一次在 12288 上限被思考用完截断，missing 是否准确未验证；架构师把这一步上限直接设为 32768，待用户在软件里验证 |
| 62-MF02 | MF-02 client | 结构图详情面板不再自己消失、显示能看的内容（¥0） | PLAN-04 | 21c73e2f | 完成，已合入（4e3c03e4） |
| 63-MF01 | MF-01 server | 填知识点前先给时间预估（按字数估点数、每点 8 分钟、按用户上限、超了给三个选择不撤掉）（¥0） | DIR-01、PLAN-05 | 65f70d20 | 完成，已合入（9015c8e0）：比例 300–1700 字/点，用户草稿估 272–1530 点、每天 343–1928 分钟，区间太宽待讨论 |
| 64-MF01 | MF-01 实验 | 用户草稿副本试填一个模块，推算 JavaGuide 8 个模块点数与 15 天负担（¥1） | DIR-01 | 757371b4 | 完成：AI 核心模块 28813 字/62 点（465 字/点），推算 987 点、12437 分钟，15 天每天 830 分钟；¥0.27 |
| 64-MF02 | MF-02 client | 步骤条窄窗溢出（¥0） | PLAN-02 | 757371b4 | 完成，已合入（140ed465） |
| 65-MF01 | MF-01 server | 填点前预估按一般情况显示、三档提示（真完不成才严重警告）、顶部日期跟表单（¥0） | DIR-01 | 60116a27 | 完成，已合入（3f4c705e）：中位 465 字/点，用户草稿每天约 498 分钟、上限 600 无提示 |
| 66-MF01 | MF-01 server | 备题改成学一个备下一个、不出跨模块重复题（¥0） | DIR-01 | 9fac6a46 | 完成，已合入（89c0bc34）：开课备第 1、2 模块，进入第 N 模块备 N+1；入库按规范化题面去重 |
| 66-MF05 | MF-05 实验 | 用户草稿副本上第 1 模块 10 个点试备题：命中率、质量、单价（¥2） | DIR-01 | 9fac6a46 | 部分完成：跑了 2 点半、¥1.60，0 道计分题；每点约 ¥0.61、184 秒（8 次调用、约 40 万输入 token、22 次搜索） |
| 67-MF01 | MF-01 server | 导航、求职、学习路线不进计划；确认前标出资料里没有现成题的模块（¥0.3） | DIR-01、PLAN-04 | 2653eb65 | 完成，已合入（d8c5f631）：非知识内容单列 excluded，JavaGuide 结构 8→5 模块；资料自带题统计没有可复用的离线识别，未做 |
| 67-MF05 | MF-05 library | 找题先按模块整体找、程序先切题块、单点只补 1 轮；Java 核心 10 点新老对比（¥3） | DIR-01 | 2653eb65 | 部分完成，已合入（5a77e5ad）：每点 ¥0.605→¥0.182、184→105 秒、输入 39 万→2.8 万 token；但 7 个点仍 0 道计分题 |
| 68-MF04 | MF-04 调研 | 挖空怎么挖到关键处、要点回想自评、两道题升三级要不要换（¥0） | DIR-01、CHK-01 | — | 完成：三个链接已核实；挖空先选目标句再选核心短答案（Becker 2012）、入库查回拼与泄露（Jiang & Lee 2017）；逐项自评减少高估但仍会误判（Lipko 2009）；没找到“固定两题升三级”的依据 |
| 69-MF05 | MF-05 实验 | JavaGuide 资料自带题覆盖率；20 个点试要点回想与对准目标的填空，逐题人工核对（¥1） | DIR-01 | 06df6250 | 完成：自带题覆盖 39.9%（题少）；要点回想 10/20、填空 7/20 合格；每点 ¥0.025 |
| 70-MF05 | MF-05 library | 拆资料自带题入库、去掉挖空与上网找题、结构步显示每模块有没有题（¥1.5） | DIR-01 | c95b0032 | 部分完成（cf8584ee）：拆题与删路已做；JavaGuide 两模块拆出 1＋10 道问答题、0 道客观题、丢 10 道，人工合格 8/11；全量 7 失败、完整场景停在 QUESTION_UNAVAILABLE（待联调）；¥0.46 |
| 70-MF01 | MF-01 server | 按题考核：模块检测 90% 的题、答错连对两遍、同题隔开、没题只学不考（¥0） | DIR-01、CHK-01 | c95b0032 | 部分完成（8cddaf7e）：按题考核与 recall 桥接已做；全量 12 项旧挖空测试失败（待联调） |
| 70-MF02 | MF-02 client | 问答题先写回答、再看答案、逐条勾要点（¥0） | DIR-01 | c95b0032 | 完成（791a289b）：问答题界面，假桥接浏览器测试通过 |
| 70-MF03 | MF-03 classroom | 随口问改成老师对话直接问，不出题不记分（¥0.3） | DIR-01、TEACH-06 | c95b0032 | 完成（486d660f）：随口问改对话，真实三种回应符合；¥0.02；最终占用 1083.52 MiB（清理被拦，路径在报告） |
| 71-MF01 | MF-01 server | 联调第七十批四份；副本上真实走通备第 1 个有题模块与模块检测（¥2） | DIR-01 | 76bae4b2 | 完成，已合入（3221753c）：四份合并、10 道被丢的题修后全收；全量 2873 通过；副本真实走通备题与模块检测，¥0.02；测试目录约 1.6 GiB 清理被拦 |
| 72-MF01 | MF-01 server | 知识地图这一步直接删模块和知识点，不调模型不重填（¥0.1） | PLAN-04 | ec23c4f6 | 完成，已合入（bab600d3）：副本 8→6 模块、541→507 点，只一次意图调用、无重填；¥0.02 |
| 73-MF01 | MF-01 server | 备题改回一次全备，失败互不影响、显示原因、未备模块插队（¥0） | DIR-01、BUG-54 | 788112d9 | 完成，已合入（1046be6b） |
| 74-MF01 | MF-01 server | 登录改成永久：身份层与 Keycloak 会话不过期、上线脚本、客户端只在明确吊销时重新登录（¥0） | BUG-55、ACC-01 | fb167d82 | 部分完成，已合入（2bae1e5d）：身份层与 realm 永久、上线脚本、客户端不因网络/5xx/睡眠踢人；原生会话四处到期未改 |
| 75-MF01 | MF-01 server | 永久登录第二步：原生会话不过期、存数据库、临时故障返回 503（¥0） | BUG-55 | 77a45e6f | 完成，已合入（9862ce86）：原生会话不过期、表 native_session_families（迁移 0043）、刷新不再走 SPI、临时故障 503；全量 2912 通过；上线脚本 .local/test-tmp/fix75-native-session/deploy75.sh 未执行 |
| 76-MF02 | MF-02 client | 课堂老师改成可收起、可拖动、可改大小的浮动小窗，模型可平移缩放（¥0） | UI-00、TEACH-05 | c9ba7958 | 完成，已合入（a4bb493b）：老师默认收起为圆形头像，展开为半透明浮窗，可拖、可改大小、模型可平移缩放，状态记住，收起暂停渲染；全量 2914 通过；测试目录约 565 MiB 清理被拦 |
| 77-MF03 | MF-03 classroom | 用户计划副本上真实走课堂（开课到第 3 模块模块检测），修掉途中所有拦路问题，含开场选项格式报错（¥2） | TEACH-06、BUG-56 | 130dea0d | 部分完成，已合入（a5fa4a2f）：开场缺选项照常讲并给固定两项；插话上下文过长与原文反复读取导致的等待已修；副本真实走到第 3 模块检测、勾要点、只重测错题、关掉重进都正常；约 ¥1.69；测试目录约 1.1 GB 清理被拦 |
| 77-MF02 | MF-02 client | 老师改成右侧整页高的抽屉（收起细竖条），用上模型自带的动作与表情（¥0） | UI-00、TEACH-05 | 130dea0d | 部分完成，已合入（b602c828）：右侧整页高抽屉、收起 32px 细边；物理、鼠标跟随、表情已用上；模型没有真正的待机动作（idle 文件实为 Cry），待机循环未做；全量 2923 通过 |
| 78-MF02 | MF-02 client | 老师栏往外展开：窗口向右变宽、对话区不变（最大化时才向内）；老师栏背景按主题做层次（¥0） | UI-00、TEACH-05 | 9cf0752c | 完成，已合入（b2973385）：普通窗口 1280→1568、对话区保持 928；右边不够左移；最大化/全屏才向内；樱枝花瓣与深紫极光背景加地面光晕；全量 2936 通过 |
| 79-MF02 | MF-02 client | 课堂原文卡片默认折叠、同资料合并、出处标记跳转；收起细边箭头朝右（¥0） | TEACH-06、UI-00 | d9f920b0 | 完成，已合入（b602a6c3）：原文默认一行折叠、同资料合并、点出处标记跳转展开、箭头朝右；全量 2937 通过 |
| 80-MF02 | MF-02 client | “查看路线”白屏：计划详情改按题显示知识地图与掌握情况、加载提示与超时、全局错误边界（¥0） | MAP-01、UI-00 | 06c356af | 未发出，作废，并入 81-MF02 |
| 81-MF02 | MF-02 client | 计划详情白屏与慢、按题知识地图、错误边界、老师预设选择界面（¥0） | MAP-01、UI-00 | 5801844c | 已合入（a617aca0，部分完成：白屏根因未复现，已加错误边界） |
| 81-MF01 | MF-01 server | 老师预设：最多 4 个可手写人设、计划默认用预设一、随时可换、全链路打通（¥0.2） | TEACH-01 | 5801844c | 已合入（e9028eeb，完成） |
| 81-MF03 | MF-03 classroom | 课堂一个输入框（去提问开关）、知识点收起改进度条、进课堂提速、中文加粗（¥0.5） | TEACH-06、UI-00 | 5801844c | 已合入（93cc94a1，完成） |
| 81-MF05 | MF-05 platform | 模型用量按余额变化显示今天/本周/本月花费（¥0） | SYS-03 | 5801844c | 已合入（c5cf1492，完成） |
| 81-MF04 | MF-04 runtime | 语音模型国内镜像优先、失败回落 GitHub（¥0） | VOICE-01 | 5801844c | 已合入（c90918c2，部分完成：本机测得镜像不提速） |
| 81-MF06 | MF-06 tools | 结构步只排除和学习无关的内容（¥0.3） | DIR-01、PLAN-04 | 5801844c | 已合入（2e9c1350，部分完成：真实确认因网络连接失败没跑成） |
| 81-MF07 | MF-07 library | 上传题目自测新功能（¥0.5） | DIR-01 | 5801844c | 已合入（9654fe0a，完成） |
| 82-MF06 | MF-06 tools | 知识点连线：DeepSeek 配对前置/相关、程序校验、新老计划自动补（¥1.5） | MAP-01、DIR-01 | 52138de0 | 已合入（43c1ebe9，完成；真实 541 点 647 前置＋129 相关，抽检 16/20 合理，4 条前置偏强） |
| 82-MF02 | MF-02 client | 知识地图图形化：路线总览、拉近看知识点网、出处排版（¥0） | MAP-01、UI-00 | 52138de0 | 已合入（329f2cbc，部分完成：缺模块前置字段，架构师合并时补一行；逐点掌握按 DIR-01 本就不存在） |
| 83-MF05 | MF-05 platform | 软件静置时无声退出：查清原因、修掉、以后退出都留原因（¥0.5） | BUG-58 | fdc1d9ea | 已合入（6a24250f，主线 67dd8d74；原事故 60 分钟未复现，修了两处会无声退出的缺陷，加永久退出诊断与源码启动脚本） |

## 任务模板

新任务照上面最近一批任务原文的格式写（第一节现成项目与许可证、开工同步、禁止事项与磁盘规则、证据来源、同时在跑、花费、背景、要做、完成标准、验收、回执项目）。
