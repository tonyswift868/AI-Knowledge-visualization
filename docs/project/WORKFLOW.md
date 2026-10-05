# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读它开头的「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务；两位架构师（都是 Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。架构师之间不直接对话，交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：先读 AGENTS.md、CURRENT_STATE「当前阶段」和索引、本文件；看 `git log` 里上次基线之后的提交；确认 7 个工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。与用户讨论到一半的事，看「当前阶段」的“等用户决定的事”和各条目里标了“讨论中”的待修。
- **交班**（每次停手前都做，不等用户说换人）：用户定下的事当场写进对应编号条目并标日期；自己查到但还没讨论完的问题写进对应条目的“待修”，标明“讨论中”；更新「当前阶段」、任务索引、待派队列；本地提交。不把事情只留在对话或个人记忆里。
- **现在在做的事**（2026-10-05）：第五十八批三个回执已合入 main c74ac725（合入后全量未重跑）。等用户定找题做法（CURRENT_STATE [DIR-01]“找题”），然后第五十九批重跑小测试。

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
- **服务器**：`ssh -F "D:/MentorFlow/.local/server/ssh_config" mentorflow`（89.45.45.28，用户 mentorflow-admin，用 sudo -n）。线上后端是瘦身镜像 mentorflow-slim-backend:MF-14，按 refactor/server/DEPLOY.md 用 compose.slim.yml 启动；回滚 `sudo bash /opt/mentorflow/state/mf14/switch.sh rollback`（切回 MF-U11），切换前的数据库备份 /opt/mentorflow/state/mf14/mentorflow-before-mf14.dump。服务器上只有管理员一个账号。
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
| MF-01 | 空闲（58 已合入） | — |
| MF-02 | 空闲（58 已合入） | — |
| MF-03 | 空闲（58 已合入） | — |
| MF-04 | 空闲 | — |
| MF-05 | 空闲 | — |
| MF-06 | 空闲 | — |
| MF-07 | 空闲 | — |

全部工位空闲。

## 待派队列

- 无。第五十九批（重跑小测试）等找题做法定下后再写。

## 第五十八批任务原文（2026-10-05，基线 39a42744）

### [58-MF01] server｜删掉的内容不再被加回；报告不再误称讲稿核对失败

~~~text
【MentorFlow 第五十八批｜MF-01（工位 server）｜删掉的内容不再被加回；报告不再误称讲稿核对失败】

现成项目与许可证（第一节）
  - 只改本项目现有的资料结构确认与按模块填点（app/plan_flow.py、course/planner_agents.py）和报告文案（app/plan_flow.py 报告 Markdown、course/report_pdf.py）。不新增依赖。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard 39a42744
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。用户装好的软件 D:\MentorFlowApp 及其数据只读，不改不删。不打包、不发版（内测阶段）。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\fix58-plan\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
做法已由架构师和用户定好，照“要做”逐条做，不自行换方案；走不通就写清证据交回。细节按 CURRENT_STATE 条目和本任务原则自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。原因要用真实数据验证过再写。架构师只读你的结论、不重跑测试，所以全量测试、离线完整场景、web 构建都由你跑并如实写结果。
证据来源：第一次真实跑完全程的小测试 D:\MentorFlow\.local\test-tmp\small57\report.md（含 live\ 原始记录、human-reviewed.json、issue-evidence.json）；复现一律用这些保存的记录，不重新跑小测试。
同时在跑（各改各的文件，冲突由架构师合并）：
  58-MF01（MF-01，工位 server）删掉的内容被加回、报告错误文案：app/plan_flow.py、course/planner_agents.py、course/report_pdf.py。
  58-MF02（MF-02，工位 library）错误选项其实成立：course/point_materials.py、course/question_bank.py。
  58-MF03（MF-03，工位 classroom）最终检验隔开重复用题、讲解不强制出处：app/classroom.py、course/teacher_agent.py、course/mastery.py（如需）。
三个合入后，下一批重跑小测试。
花费：¥0（离线）。
要做
  1. 删掉的内容被加回（小测试 P2-01）：用户在资料结构阶段只留台式机、笔记本、平板、服务器，但保留的模块引用了资料里的“概述”一节（refs s1.4），填点时从概述里又抽出 ATM、超市扫描、计算器等独立知识点。改法：用户确认的结构里要记下“已删掉的内容”（被删的模块名/章节名）；按模块填点时，把已删内容作为“不要提炼”的清单交给模型，并且程序丢掉标题或出处只落在已删章节的点。用 small57 live\steps\step-00007.json、step-00008.json 与 structure-scope.json 做复现测试（修前出现 ATM 等点，修后没有）；保留模块里本来该有的点不能被误删。
  2. 报告错误文案（P2-04）：新流程不再用讲稿，老师都是现场讲；报告 Markdown 与 PDF 里把“现场讲的知识点”说成“讲稿未通过核对”的文字删掉或改成如实说明（不提讲稿）。用 small57 live\learning-report.md 对照修前修后。
  3. 全量、离线完整场景（--to free）、web 构建通过。
完成标准：两处复现修前失败、修后通过；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix58-plan\report.md。
回执（尽量短，用表格）：【MF-01｜server｜第五十八批｜完成/部分完成/受阻】基线 ／ 提交 ／ 两处怎么改、复现修前修后 ／ 全量、离线完整场景、web 构建 ／ 本地最终占用
~~~

### [58-MF02] library｜错误选项其实成立：检查干扰项时把它出自的原文段落一起给检查器

~~~text
【MentorFlow 第五十八批｜MF-02（工位 library）｜错误选项其实成立：检查干扰项时把它出自的原文段落一起给检查器】

现成项目与许可证（第一节）
  - 只改本项目现有的方案 A 批量检查（course/point_materials.py 的 distractor_check 请求组装）与对照干扰项的记录；不新增检查环节、不新增依赖。

开工同步
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard 39a42744
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。用户装好的软件 D:\MentorFlowApp 及其数据只读，不改不删。不打包、不发版（内测阶段）。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\fix58-distractor\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
做法已由架构师和用户定好，照“要做”逐条做，不自行换方案；走不通就写清证据交回。细节按 CURRENT_STATE 条目和本任务原则自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。原因要用真实数据验证过再写。架构师只读你的结论、不重跑测试，所以全量测试、离线完整场景、web 构建都由你跑并如实写结果。
证据来源：第一次真实跑完全程的小测试 D:\MentorFlow\.local\test-tmp\small57\report.md（含 live\ 原始记录、human-reviewed.json、issue-evidence.json）；复现一律用这些保存的记录，不重新跑小测试。
同时在跑（各改各的文件，冲突由架构师合并）：
  58-MF01（MF-01，工位 server）删掉的内容被加回、报告错误文案：app/plan_flow.py、course/planner_agents.py、course/report_pdf.py。
  58-MF02（MF-02，工位 library）错误选项其实成立：course/point_materials.py、course/question_bank.py。
  58-MF03（MF-03，工位 classroom）最终检验隔开重复用题、讲解不强制出处：app/classroom.py、course/teacher_agent.py、course/mastery.py（如需）。
三个合入后，下一批重跑小测试。
花钱规则：离线为主；修完用真实模型对 small57 那道多选题和 3 道单选题重做一次检查（≤ ¥0.3）。
背景：小测试 P1-01、P1-02：多选“计算机的用途”把 use a calculator / withdraw cash 当错误选项，原文另一段（chunk0004）明说它们也是计算机的用途；方案 A 检查只拿到本点那一段（chunk0002），看不到干扰项出自的段落，于是判“原文没说它成立”，结果学生选了也算错（实际两次放水）。另 3 道单选同样多个选项成立（笔记本可在 almost anywhere / at work / on a desk 用；硬件题把机箱、显示器等当错误选项）。用户已定：仍用方案 A，只删不加，不另加 AI 核查（CURRENT_STATE [DIR-01]“题型”）。
要做
  1. 方案 A 检查每个干扰项时，请求里除了本点原文，还要带上这个干扰项出自的原文段落（对照干扰项里已记录了 contrast 出处；没有记录的就用干扰项文字在本资料里出现的段落），让检查器能看到“它在原文里被说成什么”。只删不加的规则不变；检查结果存进题库。
  2. 用 small57 的这 4 道题做复现：修前检查保留了这些成立的选项；修后离线（用保存的回复或假模型构造“检查器看到了出处段落”的情况）能删掉；再真实跑一次检查（≤ ¥0.3），报告每道题删了哪些选项、删完是否还够 2 个干扰项（不够按现有规则丢题重挖）。
  3. 全量、离线完整场景（--to free）、web 构建通过。
完成标准：检查请求带上干扰项出处段落；复现修前失败、修后通过；真实那次 4 道题里成立的选项都被删掉或题被丢。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix58-distractor\report.md。
回执（尽量短，用表格）：【MF-02｜library｜第五十八批｜完成/部分完成/受阻】基线 ／ 提交 ／ 怎么改 ／ 4 道题：修前、修后、真实检查结果 ／ 全量、离线完整场景、web 构建 ／ 花费 ／ 本地最终占用
~~~

### [58-MF03] classroom｜最终检验隔开重复用题；老师讲解不强制每段标出处、不再贴原文兜底

~~~text
【MentorFlow 第五十八批｜MF-03（工位 classroom）｜最终检验隔开重复用题；老师讲解不强制每段标出处、不再贴原文兜底】

现成项目与许可证（第一节）
  - 只改本项目现有的最终检验选题（app/classroom.py、course/question_bank.py 的选题只读不改）与老师回复校验（course/teacher_agent.py 的 parse_teaching、app/classroom.py 的 _teacher）。“看原文”入口沿用课堂里已有的原文卡片显示。不新增依赖。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 39a42744
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。用户装好的软件 D:\MentorFlowApp 及其数据只读，不改不删。不打包、不发版（内测阶段）。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\fix58-class\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
做法已由架构师和用户定好，照“要做”逐条做，不自行换方案；走不通就写清证据交回。细节按 CURRENT_STATE 条目和本任务原则自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。原因要用真实数据验证过再写。架构师只读你的结论、不重跑测试，所以全量测试、离线完整场景、web 构建都由你跑并如实写结果。
证据来源：第一次真实跑完全程的小测试 D:\MentorFlow\.local\test-tmp\small57\report.md（含 live\ 原始记录、human-reviewed.json、issue-evidence.json）；复现一律用这些保存的记录，不重新跑小测试。
同时在跑（各改各的文件，冲突由架构师合并）：
  58-MF01（MF-01，工位 server）删掉的内容被加回、报告错误文案：app/plan_flow.py、course/planner_agents.py、course/report_pdf.py。
  58-MF02（MF-02，工位 library）错误选项其实成立：course/point_materials.py、course/question_bank.py。
  58-MF03（MF-03，工位 classroom）最终检验隔开重复用题、讲解不强制出处：app/classroom.py、course/teacher_agent.py、course/mastery.py（如需）。
三个合入后，下一批重跑小测试。
花费：¥0（离线）。
背景：小测试 P2-05：最终检验同一道题（同 ID、同题面）连续出现 4 次，只是事件里的“角度”标签换了；P2-03：老师回复有一段没标 [c编号]，被整条打回，两次后程序把整段英文原文直接贴给零基础学生。用户 2026-10-05 定（CURRENT_STATE [DIR-01]）：最终检验可以重复用同一道题，但两次之间至少隔几道别的题；老师按本点原文当场讲，可以打比方、举例，不强制每段标出处，能标就标，标不出不打回、不贴原文兜底，原文由“看原文”入口提供；打比方不能变成原文没有的结论（只写进老师提示，不加检查）。
要做
  1. 最终检验选题：优先用这个点还没在最终检验里出过的题；只剩用过的题时允许重复，但与上一次出同一道题之间至少隔 3 道别的题（点不够多时尽量拉开），并去掉“只换角度标签”的假换角度。用 small57 live\steps\step-00103/00113/00116/00117 与 ledger-audit.json 做复现测试。
  2. 老师回复校验：parse_teaching 不再因为某段没有 [c编号] 而拒绝；有编号的照常校验编号必须属于本点（编号写错的去掉那个编号，不拒整条）；没有任何编号的回复照常显示，并在消息上挂本点原文（现有原文卡片）。删掉“两次不合格就贴原文”的兜底。仍保留：不许现场出计分题、不许声称改了计划等现有限制、需要中文说明。老师提示里加一句“打比方、举例帮助理解，但不能借比方推出原文没有的结论”。用 small57 live\model\call-00061/00062 做复现测试（修前被拒并贴原文，修后正常显示中文讲解）。
  3. 全量、离线完整场景（--to free）、web 构建通过。
完成标准：两处复现修前失败、修后通过；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix58-class\report.md。
回执（尽量短，用表格）：【MF-03｜classroom｜第五十八批｜完成/部分完成/受阻】基线 ／ 提交 ／ 两处怎么改、复现修前修后 ／ 全量、离线完整场景、web 构建 ／ 本地最终占用
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

## 任务模板

新任务照上面最近一批任务原文的格式写（第一节现成项目与许可证、开工同步、禁止事项与磁盘规则、证据来源、同时在跑、花费、背景、要做、完成标准、验收、回执项目）。
