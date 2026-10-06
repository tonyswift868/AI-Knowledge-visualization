# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读它开头的「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务；两位架构师（都是 Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。架构师之间不直接对话，交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：先读 AGENTS.md、CURRENT_STATE「当前阶段」和索引、本文件；看 `git log` 里上次基线之后的提交；确认 7 个工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。与用户讨论到一半的事，看「当前阶段」的“等用户决定的事”和各条目里标了“讨论中”的待修。
- **交班**（每次停手前都做，不等用户说换人）：用户定下的事当场写进对应编号条目并标日期；自己查到但还没讨论完的问题写进对应条目的“待修”，标明“讨论中”；更新「当前阶段」、任务索引、待派队列；本地提交。不把事情只留在对话或个人记忆里。
- **现在在做的事**（2026-10-07）：第七十批经 71-MF01 联调后已合入 main（题只来自资料、按题考核、问答题勾要点、随口问改对话）。软件已重启，用户从草稿“ai-agent学习”的“保存与准备”开始中测试。待派：上传题目自测。待用户决定：几个员工测试目录清理被拦（70-MF03 约 1 GB、71-MF01 约 1.6 GB）。

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
| MF-01 | 空闲（71-MF01 已合入） | — |
| MF-02 | 空闲（70-MF02 完成） | — |
| MF-03 | 空闲（70-MF03 完成） | — |
| MF-04 | 空闲（68-MF04 完成） | — |
| MF-05 | 空闲（70-MF05 完成，部分） | — |
| MF-06 | 空闲 | — |
| MF-07 | 空闲 | — |

空闲工位：runtime、client、platform、tools、classroom、server。

## 待派队列

- 无（第五十九批两份已起草，见上）。

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

## 任务模板

新任务照上面最近一批任务原文的格式写（第一节现成项目与许可证、开工同步、禁止事项与磁盘规则、证据来源、同时在跑、花费、背景、要做、完成标准、验收、回执项目）。
