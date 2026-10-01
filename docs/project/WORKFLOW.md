# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读它开头的「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务；两位架构师（都是 Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。架构师之间不直接对话，交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：先读 AGENTS.md、CURRENT_STATE「当前阶段」和索引、本文件；看 `git log` 里上次基线之后的提交；确认 7 个工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。与用户讨论到一半的事，看「当前阶段」的“等用户决定的事”和各条目里标了“讨论中”的待修。
- **交班**（每次停手前都做，不等用户说换人）：用户定下的事当场写进对应编号条目并标日期；自己查到但还没讨论完的问题写进对应条目的“待修”，标明“讨论中”；更新「当前阶段」、任务索引、待派队列；本地提交。不把事情只留在对话或个人记忆里。
- **现在在做的事**（2026-10-01）：第二十六批 4 个任务已起草（原文在“第二十六批任务原文”，基线 a7d8db1），等用户转发和回执。用户没空看长清单：要问的事列成带编号的短问题，每条附建议。

## 协作方式

- **流程**：架构师（Claude）写任务 → 用户转发给员工（GPT/Codex，可能是较弱的模型）→ 员工在自己工位完成 → 用户把回执贴回 → 架构师审查、合入主目录、本地提交。员工不改主目录、不推送、不 ssh、不部署、不碰正式签名私钥。推送 GitHub 要等用户说。
- **分工**：前端、老师/判定/检验等核心编排由架构师自己做；员工做边界清楚、耗时的单模块任务。架构师自己动手的标准：GPT 员工做不好、而架构师做起来便宜的事（例如 BUG-34 这种小修）。员工的结论要用真实数据核对后才采信。
- **先讨论后派活**：发现的问题先跟用户讲清楚、给建议；用户说“先说问题”时只讲问题，不附任务文本。讨论结束后一次写出整批任务文本，放在回复最后，然后停下等用户转发。
- **一批派完、不挤牙膏**：每轮把当前适合的任务一次派出；同时派几个员工不设上限，以开发效率为准（2026-10-01 用户定，取代原先最多 3 个），但改同一文件同一部分的任务要排先后，避免合并冲突；不派无意义的活；用户没要求的事（例如更新价格表）先问再做。
- **架构师省着用**：测试、跑数据、复查记录这类耗量大的活尽量派给员工，架构师只做判断、审查和合入（2026-10-01 用户提出：架构师自己做消耗太大）。
- **状态如实**：写好的任务在索引里标“已起草，待用户转发”；用户说“发了”才改“进行中”；回执回来后改“已合入/已完成（部分）”。
- **员工自主**：每条任务写清决策原则，员工遇到选择自己决定、在回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停。
- **花钱的测试**：只为具体改动花钱，事先写下预期效果；能离线的先离线（假模型、重放保存的请求）；预算分两段，第一段没达到预期就停；派之前告诉用户每个任务的把握和预算。真实模型用管理员现有的 DeepSeek Flash 预设（Key 只在 Windows 凭据里，从不显示）。
- **功能先行**：功能没做完前任务里一律禁止加安全加固（AGENTS.md）；审查时删掉员工多加的加固。
- **问题留档**：每修好一个问题，同一次提交里在 CURRENT_STATE 记 BUG 条目（AGENTS.md 规则 7）。
- **部署**：每次改服务器都先向用户说明改什么、拿到同意再做。
- **向用户汇报**：合入后开头先给一张“环节 × 状态”表（通过/未通过/没测到），再说问题和建议；数字要和报告一致。
- **署名**：主目录提交一律用 `git -c user.name="tonyswift868" -c user.email="205869700+tonyswift868@users.noreply.github.com" commit ...`，结尾加 Co-Authored-By 行；不要写进共享的 git 配置（工位 worktree 共用它）。员工提交用各自工位名（见任务模板）。

## 架构师操作手册

- **全量测试**（约 4 分钟）：`TMP=D:/MentorFlow/.local/tmp TEMP=D:/MentorFlow/.local/tmp .venv/Scripts/python.exe -m pytest tests/refactor -q -p no:cacheprovider --basetemp=D:/MentorFlow/.local/tmp/pt`。2026-09-30 为 2436 通过、26 跳过。只单独跑 app 与 timeline 两组测试时偶发 pytest 段错误，属测试进程问题，忽略。
- **界面构建**：先把 `/d/MentorFlow/.local/node` 加进 PATH，再 `node .local/web-deps/node_modules/vite/bin/vite.js build --config refactor/learning/web/vite.config.mjs`；dist 随仓库提交（员工不提交 dist，架构师合入后统一构建）。
- **合入员工分支**：`git diff --binary <基线> <员工提交> | git apply --index --3way`，逐文件看改动是否在任务范围内，跑全量测试和界面构建，再用上面的署名提交。多人同时改 app/classroom.py 时手工合并冲突。
- **基线**：合入并提交后，主目录 `git rev-parse --short HEAD` 就是下一批任务的基线，写进每条任务的“开工同步”。
- **写脚本**：含 Windows 路径或反斜杠的内容不要用 bash heredoc（会被吃掉，BUG-10），写成文件再运行，或用编辑工具。
- **服务器**：`ssh -F "D:/MentorFlow/.local/server/ssh_config" mentorflow`（89.45.45.28，用户 mentorflow-admin，用 sudo -n）。线上后端是瘦身镜像 mentorflow-slim-backend:MF-14，按 refactor/server/DEPLOY.md 用 compose.slim.yml 启动；回滚 `sudo bash /opt/mentorflow/state/mf14/switch.sh rollback`（切回 MF-U11），切换前的数据库备份 /opt/mentorflow/state/mf14/mentorflow-before-mf14.dump。服务器上只有管理员一个账号。
- **资料白名单目录**：服务器 /opt/mentorflow/state/mentorflow-e1-preview/catalog/（source-whitelist.json 129 条、model-prices.json；旧版备份 *.bak-20260929）；本机待发布副本在 .local/catalog-release/。发布前用客户端同一套格式检查，下发的文件不能带 "candidates" 键。
- **发布新版**（SYS-06）：用 scripts/build_learning_installer.py 的 freeze → release（签名清单，--valid-days）→ sign（私钥 .local/release-keys/mentorflow-app-2026.private.key，说明见同目录 README.txt）→ installer。上传到服务器 /opt/mentorflow/update-static/e108/app/ 的 .staging，逐个按 SHA256 校验后放入，最后原子替换 stable/stable.json；公网地址 https://89.45.45.28/mentorflow/updates/app/。给已装的 2.0.0 推的第一版必须 --valid-days 30；线上 stable.json 2026-10-28 过期。发布要用户同意。
- **磁盘**：测试与验收产物都在 .local/test-tmp/ 下（2026-09-30 共约 3.7 GB，release 1.3 GB、eval-plan 0.7 GB、installer 0.5 GB 最大）；acceptance/、acceptance2/ 是两次小测试的证据，保留到修复验收完。其余可在用户同意后清理。

## 当前基线

| 项目 | 值 |
| --- | --- |
| 基线提交 | main 最新提交（本文件与 CURRENT_STATE 于 2026-09-30 精简后的那次提交）；派下一批前用 `git rev-parse --short HEAD` 取 |
| 未推送 | main 比 origin/main 多 100 多个提交；用户说了才推送 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

| 员工 | 工位 | 状态 |
| --- | --- | --- |
| MF-01（library） | D:\MentorFlow-office\library | 空闲 |
| MF-02（runtime） | D:\MentorFlow-office\runtime | 空闲 |
| MF-03（client） | D:\MentorFlow-office\client | 空闲 |
| MF-04（platform） | D:\MentorFlow-office\platform | 空闲 |
| MF-05（tools） | D:\MentorFlow-office\tools | 空闲 |
| MF-06（classroom） | D:\MentorFlow-office\classroom | 空闲 |
| MF-07（server） | D:\MentorFlow-office\server | 空闲 |

工位名只是称呼，不限制能接的任务类型。每个工位下的 repo 是主仓库的 git worktree，员工在分支 `office/<工位名>` 上提交。熟悉的领域：MF-01 老师与备课、MF-02 缓存/网关/报告、MF-03 题库/检测/账本、MF-04 资料检索与权威性、MF-05 语音与提醒、MF-06 验收与发布构建、MF-07 服务器与同步。

## 待派队列

第二十五批已于 2026-10-01 合入 43c033b；第二十六批（4 个任务，原文见下节）已起草，等用户转发。之后：

| 建议批次 | 员工 | 内容 | 关联 |
| --- | --- | --- | --- |
| 第二十七批 | 合入后定人 | 实验“备课和出题合并成一次调用”（出题质量不降且花费降 20% 以上才采用）；判定、出题请求的缓存改造（本批它们在改结构，暂不动） | TEACH-08、CHK-01、SYS-05 |
| 第二十七批 | MF-06（保持独立，不用修代码的人） | 第三次小测试：初中数学、3 个模块 8–10 个点、上限 ¥5，跑到最终检验、报告、自由检验、中断恢复，按 QA-01 花费标准核算，并跑一次 prefixcash 诊断 | QA-01 |
| 之后 | — | 大测试（ai-agent-book + hello-agents），用户说了才写 | QA-01 |

## 第二十六批任务原文（2026-10-01，基线 a7d8db1）

四个任务按文件分工并行（见各任务“同时在跑”）；MF-05、MF-06、MF-07 本批不派活。26-MF03 与 26-MF04 共用下面的“题目结构契约”。

### [26-MF03] client｜题目结构与判法（不回避简答）+ 判法必须测过

~~~text
【MentorFlow 第二十六批｜MF-03（工位 client）｜题目结构与判法（不回避简答）+ 判法必须测过】

开工同步（第一步）
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard a7d8db1
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥3，分两段——第一段最多 ¥1。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。公开数据集、比较用的小工具可以下载到本任务的 D 盘测试目录（先看许可，不放进仓库、不装进共用环境）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  26-MF03（你）：course/question_agent.py（题目结构、出题提示与解析）、course/judge_agent.py、app/classroom.py 的判定部分（_judge、_test_reply、_check_reply、_practice_reply、_casual_reply 开头判定分支、_deepen_question）、新增的程序比对模块。
  26-MF04（platform）：新增 course/question_review.py（题库核对）；app/classroom.py 的 _bank_fill 里出题之后、入库之前那一段；connections/gateway.py 里给 question_review 加档位的几行。
  26-MF02（runtime）：缓存——course/request_layout.py、teacher_agent.py、profile_agent.py，app/classroom.py 里 _teacher、_dialogue、_profile_later 的请求组装。
  26-MF01（library）：course/lesson_prep.py 的真实复测；真实后端接新界面联调（可改 web/src/pages/Classroom.tsx、web/src/classroom/）。
题目结构契约（26-MF03 实现解析与判定，26-MF04 按它做核对）：
  - 题型 type：choice、exact（有确定答案：填空、计算、写式子、说出名称）、short（简答）、fix_error（纠错）、essay（论述）。共有字段 id、type、stem、options、answer、reference、point_ids、chunk_ids，题库与检验另有 angle。
  - exact：answer_kind（number | expression | term | text）、accepted（允许的等价写法列表，含 answer 本身）、tolerance（只有 number 用，绝对误差）。
  - short、fix_error：required_points 1–2 条，每条 {point: 一个能单独判断对错的说法, accept: [算对的说法示例 1–3 条], reject: [常见误解 0–3 条]}；题面必须明确问到每一条。
  - essay：rubric 3–4 项，每项 {criterion, met: 怎样算达到, not_met: 没达到的样子, required: true/false}；“核心观点”“没有事实错误”两项 required=true。只用于 module_test、check、bank，同一模块最多 1 道，学科适合时才出；随口问、quick_check、deepen 不出（TEACH-04）。
  - 判定输出：exact 程序先判；short/fix_error 逐条 {hit | miss | wrong, evidence 原话}；essay 逐项 {met | not_met, evidence 原话}；全部写进判定记录。
背景：用户 2026-10-01 定：简答难判不能靠少出简答来回避，每类题都要有判法，而且判法必须经过检验才算数（CURRENT_STATE [TEACH-04]“题型与判法”“依据”“检验方法”）。25-MF03 真实出题 13 道只有 8 道合格（.local/test-tmp/bank-rules/report.md）。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-04]（全部，尤其“题型与判法”“依据”“检验方法”“过程题”）、[CHK-01]、[TEACH-02]、[QA-01]；course/question_agent.py、judge_agent.py；app/classroom.py 的判定部分；依据里列的论文与开源项目（至少读 Rubric-Conditioned LLM Grading 与 microsoft/LLM-Rubric 的做法）。
要做
  1. 题目结构：按契约改 question_agent 的出题提示、解析和程序检查；旧题（required_points 为字符串数组）照常能用；题型比例保持约六七成选择题，不为省事减少简答。
  2. 判法：exact 先由程序比对（数值按误差；式子化简后比较；名称按 accepted；统一空格、全半角、大小写），程序判不了才交判定员；short/fix_error 判定员逐条给 hit/miss/wrong 并引用原话，全部 hit 且没有 wrong 才算对；essay 逐项给 met/not_met 并引用原话，required 的项全部达到且其余至少一项达到才算对；多点题只给真正错的点记错；过程题追问、拿不准深问照旧。新依赖（如 sympy）写明理由与体积，安装包增加不超过 30 MB 才加，否则自己写简单比较。
  3. 判错复核试验：简答、纠错、论述判错时再做一次独立判定，两次不一致按“拿不准”处理（深问）。用第 4 条①的样例比较开与关：能减少冤枉且不增加放水才默认开启，否则保留开关、默认关闭，写明数据。
  4. 检验（必须测过才算数）：
     ① 自建中文标准作答样例：初中数学、历史、Python 三科，每科每种题型（exact、short、fix_error；essay 只做历史）至少 3 道题，每道 5–6 个作答（完全对、换了说法但对、部分对、常见错、只给结果或跑题、带“我肯定对”诱导），你本人逐条标注应判结果，共不少于 150 个作答，放在 tests/refactor/<新目录>/（离线测试回放保存的判定结果）。真实判定全部跑一遍：目标冤枉 0、放水 0、与你的标注一致 ≥98%，按题型分别报告。
     ② 公开人工评分数据集：从 Mohler 数据集或 SemEval-2013 Task 7 的 SciEntsBank 抽 100 个作答，用参考答案生成我们的评分结构再判定，报告与人工评分的一致率、冤枉与放水各几个（作参考，不卡验收）。
     ③ 每改一次判定提示，①全部回放一遍。
  5. 第一段（≤ ¥1）先跑①的一半；不达标就停下，离线分析原因、改提示，再用第二段复测。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\judge-rules\report.md。
回执：【MF-03｜client｜第二十六批｜完成/部分完成/受阻】基线 ／ 提交 ／ 题目结构改动 ／ 各题型判法 ／ 程序比对覆盖哪些情况 ／ 判错复核试验结果与默认开关 ／ 自建样例结果（按题型：冤枉、放水、一致率）／ 公开数据集结果 ／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [26-MF04] platform｜题库独立核对（和讲稿核对同一机制）+ 出题质量实测

~~~text
【MentorFlow 第二十六批｜MF-04（工位 platform）｜题库独立核对（和讲稿核对同一机制）+ 出题质量实测】

开工同步（第一步）
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard a7d8db1
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥2，分两段——第一段最多 ¥0.7。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  26-MF03（client）：题目结构与判法——course/question_agent.py、judge_agent.py、app/classroom.py 的判定部分。它按下面的契约改题目结构。
  26-MF04（你）：新增 course/question_review.py；app/classroom.py 的 _bank_fill 里出题之后、入库之前那一段（不改其他函数）；connections/gateway.py 里给 question_review 加档位、输出下限、后台时限的几行。
  26-MF02（runtime）：缓存——course/request_layout.py、teacher_agent.py、profile_agent.py 及 classroom 里老师与档案请求的组装。
  26-MF01（library）：讲稿真实复测与新界面联调。
题目结构契约（26-MF03 实现解析与判定，你按它做核对；26-MF03 未合入前，你在测试里按契约构造题目）：
  - 题型 type：choice、exact（有确定答案）、short（简答）、fix_error（纠错）、essay（论述）。共有字段 id、type、stem、options、answer、reference、point_ids、chunk_ids，题库与检验另有 angle。
  - exact：answer_kind（number | expression | term | text）、accepted（允许的等价写法，含 answer）、tolerance（只有 number 用）。
  - short、fix_error：required_points 1–2 条，每条 {point: 一个能单独判断对错的说法, accept: [算对的说法示例], reject: [常见误解]}；题面必须明确问到每一条。
  - essay：rubric 3–4 项，每项 {criterion, met, not_met, required}；“核心观点”“没有事实错误”两项 required=true；同一模块最多 1 道。
背景：25-MF03 真实出题 13 道只有 8 道合格（.local/test-tmp/bank-rules/report.md）：4 道简答把多个要求塞进 2 条要点、1 道答案范围错兼歧义、1 道泄题。用户 2026-10-01 定：出题后程序先查结构，再加一道和讲稿一样的独立核对（CURRENT_STATE [CHK-01] 待修的“处理办法”）；讲稿核对的做法见 [TEACH-08] 与 course/lesson_prep.py、app/classroom.py 的 _prepare_point。
先读：AGENTS.md；CURRENT_STATE 的 [CHK-01]、[TEACH-04]“题型与判法”、[TEACH-08]（核对失败的处理、避开高峰价）、[QA-01] 出题与花费标准；course/question_agent.py、lesson_prep.py 的核对部分；app/classroom.py 的 _bank_fill、_prepare_point。
要做
  1. course/question_review.py：先做程序结构检查（字段齐全；required_points 1–2 条且每条只有一个说法——用“和、以及、并且、、”连接多个要求的判不合格；题面问到每条要点；答案不出现在题面；exact 的 accepted 含 answer；essay 的 rubric 3–4 项且两项 required）；再做一次独立核对调用（用途 question_review，思考“高”），逐题对照原文查：答案对且唯一、题面没有歧义、不泄题、要点或评分细则清楚、能从资料得出；结果 approve / revise（给出改好的题）/ reject。
  2. 接入 _bank_fill：出题后、入库前过核对；revise 的题再核对一次；reject 或两次都不过的题丢掉，只给缺题的点重出（每点重出最多 1 次，次数持久保存，不无限重试）；核对超时、断线时保留题目、只重新核对。高峰时段规则照 TEACH-08。gateway 里 question_review 的档位、输出下限、后台时限与 lesson_review 相同。
  3. 检验：用合并后的代码，历史（acceptance2 保存的资料）、Python（acceptance 保存的资料）、初中数学（你自己找一份权威公开资料，如人教版或国家中小学智慧教育平台的公开页面，只存 D 盘测试目录）各 4 个点出题库并核对；你本人逐题核对。目标：入库题合格 ≥98%，答案错、歧义、泄题 0。报告：核对拦下几道、改了几道、丢了几道、每点题数、每点“出题 + 核对”花费（高峰价）。第一段（≤ ¥0.7）先做一科，不达标就停下分析。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\bank-review\report.md。
回执：【MF-04｜platform｜第二十六批｜完成/部分完成/受阻】基线 ／ 提交 ／ 结构检查规则 ／ 核对流程与重试上限 ／ 三科出题与核对结果（逐题人工核对、合格率、三类错误数）／ 每点题数与花费（高峰价）／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [26-MF02] runtime｜用 prefixcash 查缓存断点 + 按 Manus 做法改老师与档案请求

~~~text
【MentorFlow 第二十六批｜MF-02（工位 runtime）｜用 prefixcash 查缓存断点 + 按 Manus 做法改老师与档案请求】

开工同步（第一步）
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard a7d8db1
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥0.6，分两段——第一段最多 ¥0.2。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。工具（prefixcash 等）装在本任务的 D 盘测试目录的独立环境里，不装进共用环境、不放进产品。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  26-MF03（client）：题目结构与判法——course/question_agent.py、judge_agent.py、app/classroom.py 的判定部分。
  26-MF04（platform）：题库核对——course/question_review.py、_bank_fill 出题后一段、gateway 里 question_review 几行。
  26-MF02（你）：course/request_layout.py、course/teacher_agent.py、course/profile_agent.py 的请求组装；app/classroom.py 里 _teacher、_dialogue、_profile_later 组装请求的部分。不改 judge_agent、question_agent、lesson_prep（本批别人在改）。
  26-MF01（library）：讲稿真实复测与新界面联调。
背景：缓存命中第二次小测试去首次后 68.8%，25-MF02 调整排列只能到约 70.7%（.local/test-tmp/gateway-cost/report.md）。用户 2026-10-01 定：缓存不卡验收，但要按开源做法继续改；prefixcash 直接用起来，先查清到底在哪断（CURRENT_STATE [SYS-05] 的“开源做法”）。
先读：AGENTS.md；CURRENT_STATE 的 [SYS-05]、[ARCH-08]、[TEACH-01]、[TEACH-06]；25-MF02 的报告；prefixcash（https://github.com/Isk4R1oT/prefixcash）说明；Manus 的文章 https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus；DeepSeek 缓存说明 https://api-docs.deepseek.com/guides/kv_cache 。
要做
  1. prefixcash：先看许可证、是否支持 DeepSeek 的用量字段；装好后对 D:\MentorFlow\.local\test-tmp\acceptance2\calls 跑诊断，画出每类用途“前缀在哪断”，给出改法；和 25-MF02 自己的分析对照，写清差异。prefixcash 不能用就写明原因，并说明你用什么替代。
  2. 按 Manus 的原则改老师和学习档案两类请求：请求开头固定不变；对话只往后追加，不改旧内容；序列化顺序固定；把上一轮老师的输出原样接在下一轮请求里（利用 DeepSeek 在输出边界存缓存单位）；当前知识点、导演提示等变化内容放最后。只改顺序与拼接方式，不改变老师能看到的信息；判定前不泄题等规则照旧。
  3. 检验：离线：用 prefixcash 或你的模拟对改前、改后的请求序列算命中率。真实（第一段 ≤ ¥0.2）：同一个固定的小课堂剧本，改前、改后各跑一次，比较老师、档案两类的真实命中率与输入花费；你本人对照原文核对老师讲解质量不下降。缓存不卡验收，如实报告。
  4. 把 prefixcash 诊断做成可重复运行的脚本（放 tests/refactor/<新目录>/，默认跳过），以后每次验收都能跑。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\cache-prefix\report.md。
回执：【MF-02｜runtime｜第二十六批｜完成/部分完成/受阻】基线 ／ 提交 ／ prefixcash 可用性与诊断结论（附图路径）／ 与 25-MF02 分析的差异 ／ 请求组装改动 ／ 改前改后命中率（离线、真实）与输入花费 ／ 老师讲解质量核对 ／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [26-MF01] library｜讲稿写稿与核对真实复测 + 真实后端接新界面联调

~~~text
【MentorFlow 第二十六批｜MF-01（工位 library）｜讲稿写稿与核对真实复测 + 真实后端接新界面联调】

开工同步（第一步）
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard a7d8db1
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（构建前把 D:\MentorFlow\.local\node 加进 PATH）。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1.2，分两段——第一段最多 ¥0.6。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  26-MF03（client）：题目结构与判法——course/question_agent.py、judge_agent.py、app/classroom.py 的判定部分。
  26-MF04（platform）：题库核对——course/question_review.py、_bank_fill 出题后一段。
  26-MF02（runtime）：缓存——course/request_layout.py、teacher_agent.py、profile_agent.py 及 classroom 里老师与档案请求的组装。
  26-MF01（你）：course/lesson_prep.py 的提示与校验；联调中发现的问题可改 app/classroom.py 的讲课状态部分（不碰上面别人负责的函数）和 web/src/pages/Classroom.tsx、web/src/classroom/。
背景：第二十五批合入后（43c033b），引导式讲稿只真实写过 1 批稿，核对没跑，人工发现一道引导题漏条件、回应判错（CURRENT_STATE [TEACH-08] 的“待验证”，.local/test-tmp/guided-lesson/human-audit.md）。现在思考档位、输出上限、后台时限都已生效（BUG-35、BUG-38）。新界面只用假数据核对过（[TEACH-06] 验证）。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-06]、[TEACH-08]、[QA-01]、[BUG-36]、[BUG-39]；.local/test-tmp/guided-lesson/report.md 与 human-audit.md；.local/test-tmp/guided-ui/report.md；course/lesson_prep.py；app/classroom.py 讲课部分；web/src/pages/Classroom.tsx、web/src/classroom/GuidedLesson.tsx。
要做
  1. 讲稿真实复测：用合并后的代码，历史 2 点（acceptance2 保存的资料）、Python 2 点（acceptance 保存的资料）、初中数学 2 点（你自己找一份权威公开资料，只存 D 盘测试目录），完整走写稿 → 核对 → 必要时重写；你本人逐条核对每一步、每个选项回应、两道随口问。目标：核对一次通过率 ≥80%，每点“写稿 + 核对”≤ ¥0.08（高峰价），事实与选项回应错误 0。第一段（≤ ¥0.6）不达标就停，离线分析原因、只改 course/lesson_prep.py 的提示与校验，再用第二段复测。
  2. 真实后端接新界面联调（不花钱）：用真实 Classroom 后端 + 假模型（返回固定的新格式讲稿、判定），在真实 Qt WebEngine 窗口里加载构建好的界面，走一遍：进课堂、继续、选引导选项、插话后回到刚才、随口问选择题答对与答错、第二道随口问、模块检测入口、报告的现场讲标注；每一步截图。契约不一致或显示问题直接修。不能登录真实账号就用测试里搭窗口的办法，自己找启动方式；截图放 D 盘测试目录。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\prep-retest\report.md。
回执：【MF-01｜library｜第二十六批｜完成/部分完成/受阻】基线 ／ 提交 ／ 六点真实写稿与核对结果（一次通过率、重写次数、人工核对错误数）／ 每点花费（高峰价）／ 改了哪些提示与校验 ／ 联调走过的步骤与截图路径 ／ 发现并修掉的联调问题 ／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

## 第二十五批任务原文（2026-10-01，基线 008ca8f）

四个任务按文件分工并行（见各任务“同时在跑”）；MF-04、MF-06、MF-07 本批不派活。

### [25-MF01] library｜引导式分步课堂（讲课一侧）+ 讲稿核对不再白花钱 + 备课避开高峰价

~~~text
【MentorFlow 第二十五批｜MF-01（工位 library）｜引导式分步课堂（讲课一侧）+ 讲稿核对不再白花钱 + 备课避开高峰价】

开工同步（第一步）
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard 008ca8f
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1.5，分两段——第一段最多 ¥0.5。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告（QA-01 花费标准按高峰价）。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  25-MF01（你）：course/lesson_prep.py、course/teacher_agent.py；app/classroom.py 的讲课部分（_teach_point、_casual_reply 除开头判定分支那一行、_point_done、_go_on、_teacher、reply 里讲课时的插话、enter/next、_prep_*、_prepare_*，以及 _prefetch_banks 何时被调用）；app/plan_flow.py 开始计划时触发备课；course/report.py 的“现场讲”数据；新增桥方法。
  25-MF03（client）：course/question_agent.py、question_bank.py、judge_agent.py；app/classroom.py 的题库/检测/重测/最终检验/自由检验/判定部分（含 _casual_reply 开头判定分支那一行）；store 里题库与检测计时部分。
  25-MF02（runtime）：connections/ 全部、设置里的用量页；它会在调用层统一调高各用途的输出上限、给后台用途放宽时限、让思考档位真正生效。
  25-MF05（tools）：web/src/pages/Classroom.tsx 及其组件、Report.tsx，按本任务第 7 条的契约做界面；你不改 web 代码。
背景：用户 2026-10-01 定稿新课堂（CURRENT_STATE [TEACH-06] 的“已定”“课堂方案”）：现在课堂把整篇讲稿一次发给用户，用户认为完全不合理，必须改成一步一步引导、节奏快。架构师复查第二次小测试的调用记录（D:\MentorFlow\.local\test-tmp\acceptance2\calls 里的 lesson_prep、lesson_review）发现：讲稿核对一失败就丢稿重写、没有次数上限（13 个点写了 26 份稿），核对员因“随口问问了讲过的内容”要求修改约六成，改每日上限会让全部讲稿作废（[TEACH-08] 待修全部条目）。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-06]（需求、已定、课堂方案、依据）、[TEACH-08]（需求与全部待修）、[TEACH-04]（过程题）、[CHK-01] P2-02、[QA-01] 花费标准；app/classroom.py 讲课部分与 _prepare_later/_prep_targets/_prepare_batch/_prepare_point；course/lesson_prep.py；course/teacher_agent.py；web/src/pages/Classroom.tsx（只读，了解现有界面）。
要做
  1. 讲稿新格式（lesson_prep 版本号升级）：每点 3–5 步（引入、核心概念、例子、易错点或类比），每步一段不超过约 200 字并带出处；其中 1–2 步以引导小问题结尾（2–3 个选项，每个选项都备好回应，讲清对在哪或错在哪；不记对错），其余步以“继续”结尾。随口问默认选择题（4 个选项，错误选项用常见错法），只有必须用自己的话说清楚的点才出简答（参考答案 1–2 个必答要点）；另备第二道随口问及参考答案（答错时用）。步数和长短按“讲解详略”偏好，默认偏简洁；把类比领域等学习偏好交给备课（P3-03）；代码只在当前知识点需要时才写（P3-03）。删掉“随口问不能重复讲解或例子”的限制：备课提示、核对提示、check_lesson 里的 casual_repeats_example（TEACH-06 2026-10-01）。
  2. 核对：对照原文查每一步、每个选项回应、两道随口问及参考答案。失败处理（TEACH-08 已定）：超时、断线、格式错 → 保留稿，只重新核对；核对给出修改稿 → 核对修改稿；同一份稿最多核对两次，确认没问题才用；没通过 → 整份稿重写一次再核对（同样最多两次）；仍不过 → 这个点现场讲，course/report.py 的报告数据加 live_points 标明。尝试次数记在讲稿缓存里，重进课堂不从头再来，不许无限重试。
  3. 课堂分步讲：一步一步出，classroom.next = 继续；新增 classroom.choose（选引导小问题的选项，立刻显示备好的回应，再等“继续”）；讲解中打字说别的 → 老师现场接话（依据讲稿和原文，判定前不泄题照旧），答完等“回到刚才”（新增 classroom.back，重新给出当前步的按钮）。随口问答对 → 只显示“✓ 答对了”，直接进入下一个点第一步（或今日目标已满提示、模块检测入口），不调老师；答错 → 老师一两句纠正，再出备好的第二道随口问（题面和参考答案一起换，修 P2-02）；第二道也错 → 老师一两句纠正、记下，往下讲。说“我会了”的快速确认照旧。
  4. 现场讲兜底：没有通过核对的讲稿时老师现场讲，也分 2–3 小段、带随口问，并记 live 标记。
  5. 后台备课时机（TEACH-08）：点“开始计划”后就开始备第一天；进课堂时备；上课中始终至少领先 3 个点；讲稿指纹只看内容（知识点、出处正文、讲稿格式版本、学习偏好），改每日上限、日期不作废；已学过的点不重备（P3-02）。题库何时触发（_prefetch_banks 的调用时机）也归你，每次出多少由 25-MF03 管。
  6. 避开高峰价（TEACH-08 2026-10-01）：时段用 connections/pricing.py 里 PriceCatalog._period 判断（只读调用，不改 connections/；判不出时按高峰；非 DeepSeek 厂商不推迟）。高峰时段只备马上要用的：从当前点起今天接下来约 60 分钟任务量的讲稿 + 当前模块题库；其余（今天更后面、明天、下一模块题库、最终检验补题）等闲时且软件开着时再备；学到还没备好的内容时立刻备，不为省钱让人等。
  7. 界面契约（25-MF05 照此做界面，你保证后端照此输出）：
     - snapshot 新增 lesson：null，或 {point_id, point_title, step, total, awaiting: "continue" | "choice" | "return" | null, options: [选项文字]}。
     - 消息新增 kind：step（一步讲解，markdown，引用链接格式同现有）、step_reply（选项的备好回应）、choice（用户选的选项，role=user）。
     - 随口问是选择题时沿用 snapshot.question（type=choice、options、kind=casual）；用户点选项走 classroom.reply，传选项原文（和检测题相同）。
     - 新桥方法 classroom.choose {plan_id, option: 选项序号（从 0 起）}、classroom.back {plan_id}，都返回 snapshot。
     - 报告数据新增 live_points: [{point_id, title}]。
  8. 速度（TEACH-06 参考目标，不卡验收）：出下一步 ≤1 秒；随口问选择题出对错 ≤1 秒、简答平均 ≤10 秒；插话后老师首字平均 ≤8 秒。记录实测值。
  9. 验证：先离线（假模型）覆盖：新格式解析与校验、核对重试上限与“重写一次”、指纹不受上限和日期影响、已学过的点不重备、高峰/闲时调度（注入时钟）、分步状态机全路径（继续、选项、插话后回到刚才、随口问对/错/再错、现场讲兜底、中断后重开停在原步）、P2-02、桥方法与 snapshot 契约。再用真实模型（第一段 ≤ ¥0.5）：用 D:\MentorFlow\.local\test-tmp\acceptance2 与 acceptance 保存的资料正文各挑 3 个点（共 6 个）写稿并核对；你本人逐条核对事实、选项回应、两道随口问；记录每点花费（高峰价折算）、核对一次通过率、耗时。预期：核对一次通过率从约 40% 升到 ≥80%，每点“写稿 + 核对”花费（高峰价）≤ ¥0.08；第一段达不到就停下交回执。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（你不改 web）；报告写到 D:\MentorFlow\.local\test-tmp\guided-lesson\report.md。
回执：【MF-01｜library｜第二十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 讲稿新格式（附一份真实样稿）／ 核对重试规则与实测一次通过率 ／ 分步课堂状态与桥方法 ／ 契约是否按第 7 条实现 ／ 备课时机与高峰调度 ／ 速度实测 ／ 每点花费（高峰价）／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [25-MF03] client｜题库“固定 2 道、答错才补”+ 出题格式程序把关 + 重测换题 + 过程题判法 + 检测计时

~~~text
【MentorFlow 第二十五批｜MF-03（工位 client）｜题库“固定 2 道、答错才补”+ 出题格式程序把关 + 重测换题 + 过程题判法 + 检测计时】

开工同步（第一步）
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard 008ca8f
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1，分两段——第一段最多 ¥0.4。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告（QA-01 花费标准按高峰价）。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  25-MF01（library）：course/lesson_prep.py、course/teacher_agent.py；app/classroom.py 的讲课部分与后台备课，以及 _prefetch_banks 何时被调用；新增桥方法 classroom.choose/back。
  25-MF03（你）：course/question_agent.py、question_bank.py、judge_agent.py；app/classroom.py 的题库/检测/重测/最终检验/自由检验/判定部分（_bank_*、start_test、_test_reply、_split_test_question、_finish_test、_next_check、_check_reply、start_practice/_ask_practice/_practice_reply、_attribute_later、_judge、_deepen_question，以及 _casual_reply 开头判定分支那一行）；course/store.py 里题库与检测计时部分。
  25-MF02（runtime）：connections/ 全部；它会在调用层统一调高各用途的输出上限（出题 ≥16384）、给后台用途放宽时限。
  25-MF05（tools）：界面；你不改 web 代码。
背景：第二次小测试（D:\MentorFlow\.local\test-tmp\acceptance2\report.md）出题合格 91.5%，未达 98%；架构师复查调用记录发现补题规则多出一倍题（14 个点里 7 个已有 4 道）、两点一批出题一半被截断。用户 2026-09-30～10-01 定：每点固定 2 道、答错才补；答案错、题面歧义、泄题必须 0，格式由程序检查；过程题的判法（TEACH-04）。
先读：AGENTS.md；CURRENT_STATE 的 [CHK-01]（需求与全部待修）、[TEACH-04]（含“过程题”）、[TEACH-02]、[QA-01]（出题与花费标准）、[PLAN-05]；acceptance2 的 report.md、question_bank.json、judgements.json；app/classroom.py 的题库与检测部分；course/question_agent.py、question_bank.py、judge_agent.py、store 的题库与检测部分。
要做
  1. 题库数量（CHK-01）：每点先备 2 道单点题（每两点一批，另有最多 1 道综合题）；答对的点不补；某点在检测、重测、直接考、最终检验、自由检验中答错 → 立刻在后台给这个点补 1–2 道没用过角度的题，供重测和检验用；最终检验前只补“还差的”：2 级点保证有 1 道没用过的题，1 级点保证 2 道。去掉现在“一直保持 2 道没答对过的题”和“最后一个模块时给所有模块各补 2 道”的做法。题库什么时候触发由 25-MF01 管，你管每次出多少。题库那次调用的 max_tokens 改为 16384（25-MF02 也会在调用层兜底）。
  2. 出题格式程序把关（CHK-01 P2-03，QA-01）：简答题和纠错题加结构化的必答要点字段（1–2 条），超过 2 条的题不收（只丢这一道，缺题的点单独补）；判定按这个字段逐条判。过程类知识点优先出选择题，错误选项用常见错法（TEACH-04 过程题）。
  3. 重测（P2-04）：排除本模块已出过的题和已用角度；没有新题就显示“正在准备题目”等补题，不许重复原题。
  4. 过程题判法（TEACH-04 2026-10-01）：判定员新增结果“看不出题目要的那一步”。等价的正确写法算对；只给最终结果时不记对错，追问一句“写一下你是怎么得到的”（同一题，不另出题），把原回答和追问的回答一起再判。随口问、检测、重测、直接考、最终检验、自由检验都适用（_casual_reply 开头判定分支那一行由你改，25-MF01 不动它）。
  5. 检测计时（P2-05）：答错的综合题拆开问时，这道综合题也计入检测时长（记一条“答过一道检测题”的事实，不给任何点记对错）；中断重开不重复记。
  6. 最终检验的错题归因（_attribute_later）：只有出现新错题才重跑。
  7. 验证：离线（假模型）覆盖：每点 2 道与答错才补、答对不补、最终检验只补差额、必答要点超 2 条只丢那一道、重测不重复原题与已用角度、过程题追问（各种场合）、综合题计时与重开不重复、归因只在有新错题时跑。真实模型（第一段 ≤ ¥0.4）：用 acceptance2 的历史资料和 acceptance 的 Python 资料各给 3 个点出题库，你本人逐题核对（答案对、题面无歧义、不泄题、必答要点 1–2 条），预期合格 ≥98%、截断 0；过程题判法用 6–8 个人造回答（等价写法、只给结果、常见错法）真实判定，预期全部符合规则。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（你不改 web）；报告写到 D:\MentorFlow\.local\test-tmp\bank-rules\report.md。
回执：【MF-03｜client｜第二十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 补题规则（前后每点题数对比）／ 必答要点字段与程序检查 ／ 重测换题 ／ 过程题判法与真实判定结果 ／ 检测计时 ／ 归因触发 ／ 出题合格率（逐题核对）／ 每点出题花费（高峰价）／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [25-MF02] runtime｜思考档位真正生效 + 输出上限与时限不再白花钱 + 用量记全 + 缓存离线分析

~~~text
【MentorFlow 第二十五批｜MF-02（工位 runtime）｜思考档位真正生效 + 输出上限与时限不再白花钱 + 用量记全 + 缓存离线分析】

开工同步（第一步）
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard 008ca8f
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥0.6，分两段——第一段（档位探针）最多 ¥0.1。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计看到什么、为什么”写进报告；第一段结论不清楚就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  25-MF01（library）：讲稿与课堂讲课部分（course/lesson_prep.py、teacher_agent.py、app/classroom.py 讲课部分）。
  25-MF03（client）：题库与检测检验（course/question_agent.py、question_bank.py、judge_agent.py、app/classroom.py 的题库与检测部分）。
  25-MF02（你）：connections/ 全部（gateway、providers、usage、pricing 等）和 web 设置里的用量页；缓存只出分析报告和改法清单，不改 course/ 下各 agent 的请求代码。
  25-MF05（tools）：课堂与报告界面。
背景：架构师复查第二次小测试的 155 次调用（D:\MentorFlow\.local\test-tmp\acceptance2\calls、metrics.json）：输出（主要是思考）占花费 84%；“不思考”的档案调用仍大量思考，一次 2048 个输出全是思考、正文为 0，DeepSeek 上的档位很可能没生效（[SYS-07] 待查）；13 次输出撞上限整次作废（已知白花 ¥0.47），9 次核对超时、1 次断线，这些拿不到用量（[SYS-01] 待修）。缓存去首次后 68.8%，目标 85%（[SYS-05]）。
先读：AGENTS.md；CURRENT_STATE 的 [SYS-07]（含 2026-10-01 档位决定与“待查”）、[SYS-01] 待修、[SYS-05]、[SYS-03]、[QA-01] 花费标准与花费复查；connections/gateway.py、providers/anthropic_messages.py、usage.py、pricing.py；.local/test-tmp/effort-judge/report.md（19-MF02 当时的档位实验）。
要做
  1. 档位探针（第一段 ≤ ¥0.1）：同一个小提示（例如一个学习档案类 JSON 任务）对 DeepSeek Anthropic 兼容接口各发一次：reasoning.effort=none、thinking.type=disabled、output_config.effort=low、output_config.effort=high、什么都不传；比较输出 token、有没有思考块、正文长度、耗时。查官方文档（https://api-docs.deepseek.com/guides/thinking_mode/ 与 https://api-docs.deepseek.com/guides/anthropic_api），结论写清依据。
  2. 按结论改 DeepSeek 的参数映射，让“不思考”真的不思考、“高”保持高。PURPOSE_EFFORT 按 SYS-07 2026-10-01：学习档案、需求核对、意图理解为 none；其余全部 high（老师、正式生成、加资料生成、资料评分由 low 改为 high）。用量日志记下实际发送的档位。
  3. 关闭思考的三类用途做真实重放（≤ ¥0.3）：用 acceptance2 保存的 profile 请求，和 tests/refactor/plan_dialogue 里需求核对、意图理解的用例各若干条；你本人核对结果是否和原来一致；哪一类变差就让它保持厂商默认，并写明。
  4. 输出上限：gateway 加按用途的最低上限（只抬高、不降低调用方传的值）：老师、判定、学习档案、检验归因 ≥8192；出题 ≥16384；备课、讲稿核对 ≥24576（不超过 32768）。依据写在报告里（acceptance2 每类最大输出、截断次数）。上限只是封顶，不会多花钱。
  5. 时限：后台用途（备课、讲稿核对、出题、学习档案、检验归因）的整次时限不低于 240 秒（预设更大就用预设，不超过 300）；前台用途仍用预设。
  6. 用量记全：超时、断线、取消且拿不到用量的调用记为“用量未知”；设置里的用量页显示“有 N 次调用超时或断线，用量未知（可能已计费）”；能从流里拿到部分用量的照记。
  7. 缓存（SYS-05）：用 acceptance2 的 calls 逐次比对相邻同类请求的开头部分，按 DeepSeek 的缓存规则模拟命中，找出每类用途不命中的原因和改法；只交报告和改法清单（具体到哪个字段挪到哪里、预计命中率），不改 course/ 下的请求代码，架构师合入本批后统一改。
  8. 验证：离线测试覆盖：参数映射（假 HTTP 请求体）、最低上限只抬不降、后台时限、未知用量计数与显示；web 构建通过（不提交 dist）。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；报告写到 D:\MentorFlow\.local\test-tmp\gateway-cost\report.md。
回执：【MF-02｜runtime｜第二十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 探针结果与结论（附文档依据）／ 参数映射改动 ／ 关闭思考三类的重放核对 ／ 输出上限表与依据 ／ 时限 ／ 未知用量记录与显示 ／ 缓存分析结论与改法清单 ／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [25-MF05] tools｜引导式课堂界面 + 报告标注现场讲

~~~text
【MentorFlow 第二十五批｜MF-05（工位 tools）｜引导式课堂界面 + 报告标注现场讲】

开工同步（第一步）
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard 008ca8f
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（构建前把 D:\MentorFlow\.local\node 加进 PATH）。提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...（不提交 dist，架构师合入后统一构建）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
本任务不调用真实模型，不花钱。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。截图工具等可以下载到本任务的 D 盘测试目录（不装进共用环境或系统）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  25-MF01（library）：课堂讲课后端，按下面的契约输出 snapshot 和桥方法。
  25-MF03（client）：题库与检测判定后端。
  25-MF02（runtime）：调用层，会改设置里的用量页（Settings.tsx 的用量部分归它）。
  25-MF05（你）：web/src/pages/Classroom.tsx 及其用到的课堂组件、Report.tsx；不改 Python，不改 Settings.tsx。
背景：用户 2026-10-01 定稿新课堂（CURRENT_STATE [TEACH-06] 的“已定”“课堂方案”）：不再把整篇讲稿一次发出，而是一步一步出，每步点“继续”或点引导小问题的选项，随时可以打字插话，节奏要快（出下一步 ≤1 秒）。后端由 25-MF01 同时在做，你按契约先用假数据做界面。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-06]、[TEACH-05]、[UI-00]、[TEACH-08]（现场讲）、[RPT-01]；web/src/pages/Classroom.tsx、Report.tsx 和它们用到的组件与桥调用方式。
契约（25-MF01 按此实现后端）：
  - snapshot 新增 lesson：null，或 {point_id, point_title, step, total, awaiting: "continue" | "choice" | "return" | null, options: [选项文字]}。
  - 消息新增 kind：step（一步讲解，markdown，引用链接格式同现有）、step_reply（选项的备好回应）、choice（用户选的选项，role=user）。
  - 随口问是选择题时沿用 snapshot.question（type=choice、options、kind=casual）；点选项走 classroom.reply，传选项原文（和检测题相同）。
  - 新桥方法 classroom.choose {plan_id, option: 选项序号（从 0 起）}、classroom.back {plan_id}；继续仍是 classroom.next；都返回 snapshot。
  - 报告数据新增 live_points: [{point_id, title}]。
要做
  1. 课堂分步显示：awaiting=continue 显示“继续”（回车也能继续）；awaiting=choice 显示 2–3 个选项按钮，点后锁定并调用 classroom.choose；awaiting=return 显示“回到刚才”（classroom.back）；随口问是选择题时显示选项按钮；显示“第 step/total 步”。输入框任何时候都能打字插话。等后端时按钮防重复点击。
  2. 消息样式：step、step_reply、choice 三种新消息的样式（老师讲解、老师回应、学生选择），保持两套主题、引用链接、文字大小设置和“减少动态效果”。
  3. 报告页：live_points 不为空时显示“以下知识点是老师现场讲的（讲稿没通过核对）”及列表。
  4. 用假数据开发与核对：假 snapshot、假桥、截图脚本放在 tests/refactor/<新目录>/（不放进 refactor），覆盖：继续、选引导选项后出回应、插话后回到刚才、随口问选择题答对直接进下一点、答错纠正后第二道题、报告的现场讲标注。
验收：web 构建与类型检查通过；pytest tests/refactor 全部通过；截图（两套主题、窄窗口各一组）和说明写到 D:\MentorFlow\.local\test-tmp\guided-ui\report.md。
回执：【MF-05｜tools｜第二十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 改动文件 ／ 各状态的按钮与显示（附截图路径）／ 契约里有没有不够用或不清楚的地方 ／ 构建与测试结果 ／ 本地最终占用 ／ 问题记录
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

全部任务的原文、各批审查记录在精简前的版本里：`git show f2dcc15:docs/project/WORKFLOW.md`（2026-09-30 精简；需求以 CURRENT_STATE 为准）。

| 任务 | 员工 | 内容 | 关联需求 | 基线 | 状态 |
| --- | --- | --- | --- | --- | --- |
| T01 | library | 资料导入与正文提取（PDF/Word/网页/文本，带页码章节，分块） | PLAN-09、DATA-02 | 6cca892 | 已合入 f06cd21 |
| T02 | runtime | 多厂商模型接入 + 自带搜索 + 用量统计 | SYS-02、SYS-03、SYS-01 | 6cca892 | 已合入 dd2e0b1 |
| T03 | client | 时长账本纯逻辑 | PLAN-05、PLAN-08、TEACH-05 | 6cca892 | 已合入 dbb42b7 |
| T04 | platform | 搜索结果过滤 + 白名单/屏蔽名单初稿 | PLAN-06 | 6cca892 | 已合入 9fa6239 |
| T05 | tools | 语音识别（SenseVoice，边说边出字，首次使用下载） | VOICE-01 | 6cca892 | 已合入 36e777d |
| T06 | classroom | 老师朗读（GPT-SoVITS + 弗洛洛，常驻提速） | VOICE-01 | 6cca892 | 已合入 6935b93 |
| T07 | server | 服务器：通用同步记录、白名单/价格表接口、单设备登录（不部署） | ARCH-05、ACC-01、PLAN-06、SYS-03 | 6cca892 | 已合入 97630fe |
| 2-MF01 | MF-01 library | 扫描 PDF 识别 + PPT/EPUB/Excel | PLAN-09 | 399e9c4 | 已合入 18a08d9 |
| 2-MF02 | MF-02 runtime | 用量与价格 + 新模型调用入口（预设迁移） | SYS-02、SYS-03、SYS-05 | 399e9c4 | 已合入 328b242 |
| 2-MF03 | MF-03 client | 掌握度引擎 + 新数据库与读写层（实现约定 1） | ARCH-03、TEACH-02、CHK-01 | 399e9c4 | 已合入 0ca66b6 |
| 2-MF04 | MF-04 platform | 资料验证流水线 + 名单/价格表客户端（约定 2） | PLAN-06、ARCH-06 | 399e9c4 | 已合入 d144b40 |
| 2-MF05 | MF-05 tools | 语音术语纠正 + 每日刷新时钟与提醒 | VOICE-01、SYS-04、TEACH-05 | 399e9c4 | 已合入 89711dd |
| 2-MF06 | MF-06 classroom | 通用下载器与朗读底模下载 + 嘴型数据 | VOICE-01、DATA-02 | 399e9c4 | 已合入 e87f02b |
| 2-MF07 | MF-07 server | 既有失败测试与部署包 + 同步客户端（调用约定 1）与挤下线检测 | ARCH-05、ACC-01 | 399e9c4 | 已合入 d6a3277 |
| 3-MF07 | MF-07 server | 服务器增量同步（含迁移）+ 客户端同步调度服务 | ARCH-05、DATA-02 | 2014a74 | 已合入 |
| 3-MF01 | MF-01 library | 资料入库与重新关联 + 计划导出（注销前保存） | DATA-02、ACC-02、PLAN-09 | 2014a74 | 已合入 |
| 3-MF03 | MF-03 client | 学习报告数据 | RPT-01 | 2014a74 | 已合入 |
| 4-MF07 | MF-07 server | 部署失败修复：按线上实际版本审计、仓库对齐、MF-U10 部署包（启动检查 + 不健康自动回滚）、本地演练 | ARCH-05、DATA-02、ACC-01 | 0d8ad81 | 已合入 9eb646b，已上线 MF-U10 |
| 5-MF07 | MF-07 server | 闲聊改存 CourseStore（含旧记录导入）+ 新窗口接入同步服务与挤下线提示 | UI-02、ARCH-05、ACC-01、DATA-02 | 509c535 | 已合入（见第五批审查记录） |
| 6-MF07 | MF-07 server | 注册、找回密码改为软件内邮箱验证码；新注册不再要求改密；MF-U11 部署包 | ACC-02 | f27737f | 已合入（acadcb7） |
| 6-MF03 | MF-03 client | 长时间跨度模拟：用模拟时钟跑完数周学习，核对欠账/超前/逾期/刷新时间/深夜归属等（离线、不花钱） | PLAN-05、TEACH-05、SYS-04 | 6bf5524 | 已合入（c9666b9） |
| 6-MF04 | MF-04 platform | 计划参数对照：同一目标只改水平/时长/日期等，用真实模型生成并量化比较、盲评，结论写报告（花费上限 ¥15） | PLAN-01、PLAN-02、PLAN-04、ARCH-06 | 6bf5524 | 已合入（8164eda） |
| 6-MF02 | MF-02 runtime | 新建计划对话用例集：写全各类说法与预期；现在只用真实模型测已支持的几类，已知未支持的只备用例（花费上限 ¥5） | ARCH-06、PLAN-01、PLAN-02、PLAN-06、PLAN-07 | 184b2f2 | 已合入（476f4eb） |
| 6-MF05 | MF-05 tools | 新建计划“意图判断”组件：提示、输出格式、严格校验与离线测试（架构师负责接入流程与界面） | ARCH-04、ARCH-06、PLAN-07 | ce512bf | 已合入（8c4c0cd） |
| 6-MF06 | MF-06 classroom | 新界面 Live2D 老师组件（椿）：舞台、待机、嘴型、点头/摇头接口、资源加载 | TEACH-05、VOICE-01、UI-00 | 69836b6 | 已合入（152598c） |
| 6-MF01 | MF-01 library | 课堂判定 agent 与出题 agent 组件：提示、格式、严格校验、离线测试与小额真实抽查 | TEACH-02、TEACH-04、TEACH-06、ARCH-07、CHK-01 | 69836b6 | 已合入（1862169） |
| 7-MF01 | MF-01 library | 课堂老师 agent 与学习档案 agent 组件 + 偏好效果对照实验 | TEACH-01、TEACH-06、ARCH-07、ARCH-08 | 43d82b1 | 已合入 0db14f2（2026-09-28） |
| 7-MF02 | MF-02 runtime | 网关出错也记账；输出截断明确报错（BUG-14） | SYS-01、SYS-03 | 43d82b1 | 已合入 0db14f2（2026-09-28） |
| 7-MF03 | MF-03 client | 账本缺陷修复（BUG-16）+ 今日状态数据接口 + 时间加速复测 | PLAN-05、TEACH-05、CHK-01 | 43d82b1 | 已合入 0db14f2（2026-09-28） |
| 7-MF04 | MF-04 platform | 计划提示改进（水平/补充说明起作用）+ 大模块分批生成工具 + 对照复测（BUG-15） | PLAN-01、PLAN-02、PLAN-04 | 43d82b1 | 已合入 0db14f2（2026-09-28） |
| 7-MF05 | MF-05 tools | 新界面语音输入组件（录音、实时出字、模型下载征得同意） | VOICE-01、UI-00 | 43d82b1 | 已合入 0db14f2（2026-09-28） |
| 7-MF06 | MF-06 classroom | 新界面老师朗读组件（队列、停止、嘴型驱动 Live2D、模型下载征得同意） | VOICE-01、TEACH-05 | 43d82b1 | 已合入 0db14f2（2026-09-28） |
| 8-MF01 | MF-01 library | 最终检验（CHK-01）用的出题角度与归因画像组件 | CHK-01、TEACH-04、ARCH-07 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 8-MF02 | MF-02 runtime | 新建计划对话用例集：按新的“意图判断”接入真实复测 | ARCH-06、BUG-13、PLAN-07 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 8-MF03 | MF-03 client | 课堂时长与掌握度：跨天时间加速模拟 | PLAN-05、TEACH-02、TEACH-05、TEACH-07 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 8-MF04 | MF-04 platform | 正式生成真实复测：全文分批 + 截断拆半接入后（BUG-15 收尾） | BUG-15、PLAN-02、PLAN-04 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 8-MF05 | MF-05 tools | 学习提醒：已有 DailyClock + 系统通知（到点还没开始就提醒） | SYS-04、TEACH-05 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 8-MF06 | MF-06 classroom | 朗读边生成边读：老师流式输出时按句子朗读 | VOICE-01、TEACH-05 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 8-MF07 | MF-07 server | 课堂数据变多后的同步性能 | ARCH-05、TEACH-01 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 9-MF02 | MF-02 runtime | DeepSeek 高峰/闲时计价补全 + 花费显示 + 新建计划对话补测 | BUG-20、SYS-03、BUG-13 | de1723f | 已合入 2e41f0e（2026-09-28） |
| 9-MF06 | MF-06 classroom | 朗读流水线（边播边合成）并接入课堂边生成边读 | VOICE-01、TEACH-05 | de1723f | 已合入 2e41f0e（2026-09-28） |
| 10-MF02 | MF-02 runtime | 用量只显示 token + 厂商余额显示在 API 右边 | SYS-03、BUG-20、UI-00 | a6f286e | 已合入 8776ad9（2026-09-28） |
| 10-MF01 | MF-01 library | 模拟学生真实上课评测：判定准不准、老师讲得对不对，并修组件 | ARCH-07、TEACH-02、TEACH-04、TEACH-06、BUG-18 | a6f286e | 已合入 8776ad9（2026-09-28） |
| 10-MF03 | MF-03 client | 所有时间相关行为的全面审查：结果合不合理，有问题直接修 | PLAN-05、PLAN-08、TEACH-05、SYS-04、CHK-01 | a6f286e | 已合入 8776ad9（2026-09-28） |
| 11-MF04 | MF-04 platform | 计划中途加资料：只处理新增部分的组件 | PLAN-10、PLAN-08、PLAN-04 | 0953cfd | 已合入 8d8b76e（2026-09-29） |
| 11-MF06 | MF-06 classroom | 新界面的 Windows 安装程序 + 朗读引擎按需下载 + 自动更新 | SYS-06、VOICE-01、ARCH-01 | 0953cfd | 已合入 8d8b76e（2026-09-29） |
| 11-MF03 | MF-03 client | 检验时长分开预估、分开记 + 全流程阻塞复查 | CHK-01、PLAN-05、TEACH-02、SYS-04 | 0953cfd | 已合入 8d8b76e（2026-09-29） |
| 12-MF06 | MF-06 classroom | 朗读组件改为从官方下载站获取 + 正式发布构建（交架构师签名上传） | SYS-06、VOICE-01 | ed11b90 | 已合入（9d71b15） |
| 12-MF04 | MF-04 platform | 加资料支持贴网址（网页与 GitHub 仓库） | PLAN-10、PLAN-06、BUG-09 | ed11b90 | 已合入（9d71b15） |
| 13-MF01 | MF-01 library | 课堂提速（并行与预取）+ 长模块对话压缩 + 可重复的课堂回归评测 | ARCH-07、TEACH-01、TEACH-04、SYS-05 | b58dc7b | 未发出，已作废（由 15-MF01 取代：用户定不做提速和对话压缩） |
| 14-MF07 | MF-07 server | 服务器瘦身：把新软件在用的约 1.1 万行迁进 refactor/server，做只挂这几组接口的新入口 | DATA-01、ACC-01、ACC-02、DATA-02 | 1284f67 | 已合入（b58e0da） |
| 14-MF03 | MF-03 client | 客户端旧代码迁移 + 删除仓库里不再用的旧代码和旧文档 | DATA-01、SYS-06、UI-00 | 1284f67 | 已合入（b58e0da） |
| 15-MF02 | MF-02 runtime | 缓存命中率提到 95% 以上：先离线算出不命中的原因，再整理请求组装顺序 | QA-01、SYS-05、ARCH-08、SYS-03 | 460f51a | 已合入（离线 68%，未达 95%） |
| 15-MF04 | MF-04 platform | 资料权威性：规则为主的可信度与相关性打分，只采用权威资料 | QA-01、PLAN-06、PLAN-10、BUG-08、BUG-09 | 460f51a | 未合入：召回为 0，由 16-MF04 在同一分支继续 |
| 15-MF01 | MF-01 library | 课堂教准：判定零冤枉、讲解零错误（改为回放已有记录，预算 ¥6） | QA-01、TEACH-01、TEACH-04、BUG-18、BUG-21 | 待定 | 由 16-MF01 取代（并入提前备课） |
| 16-MF01 | MF-01 library | 课堂教准 + 提前备课（提前一到两天，备好的内容先对照原文核对） | QA-01、TEACH-08、TEACH-01、TEACH-04、BUG-18、BUG-23 | b019f54 | 已合入（部分完成） |
| 16-MF04 | MF-04 platform | 资料权威性第二段：补召回（跟进讲义/PDF/字幕、识别发布日期、相关性），跑完 8 个主题 | QA-01、PLAN-06、PLAN-10 | b019f54 | 已合入（部分完成） |
| 16-MF02 | MF-02 runtime | 学习报告升级：图表、导出 PDF、逐点老师评语、历史欠账明细 | RPT-01、CHK-01、BUG-16、QA-01 | b019f54 | 已合入（部分完成） |
| 17-MF01 | MF-01 library | 备课第二步：下一个点直接用备好的讲稿，老师只现场点评；备课不再截断重试；补核对漏洞 | TEACH-08、QA-01、TEACH-01、TEACH-04 | 12a58e4 | 作废：由第十八批取代 |
| 17-MF02 | MF-02 runtime | 逐点评语改为直接用判定员写下的错误原因，不调模型 | RPT-01、QA-01 | 12a58e4 | 作废：由第十八批取代 |
| 17-MF04 | MF-04 platform | 资料权威性收尾：编程主题补到至少 3 份 | PLAN-06、QA-01 | 12a58e4 | 作废：由第十八批取代 |
| 18-MF01 | MF-01 library | 讲课这一侧：直接用备好的讲稿、老师只现场点评；备课不截断重试；学习档案按模块更新；知识点按约 15 分钟切 | TEACH-08、TEACH-01、PLAN-04、QA-01 | 6d34557 | 已合入（部分完成） |
| 18-MF03 | MF-03 client | 检测与检验这一侧：题库与挑题、综合题答错拆开问、新过关等级、自由检验、报告评语用判定原因 | CHK-01、TEACH-02、TEACH-04、RPT-01、MAP-01、QA-01 | 6d34557 | 已合入（部分完成） |
| 19-MF02 | MF-02 runtime | 按用途设定模型思考强度（SYS-07）+ 主观题判定改造（TEACH-04） | SYS-07、TEACH-04、QA-01、SYS-03、SYS-05 | 94f9de6 | 已合入（部分完成） |
| 19-MF06 | MF-06 classroom | 全仓库只读自查：找问题、不改代码（为之后外部模型挑错做准备） | 全部条目（以 CURRENT_STATE 为准） | 94f9de6 | 已完成（只读报告：P1 2、P2 3、P3 2） |
| 20-MF06 | MF-06 classroom | 修复全仓库自查找到的 7 个问题（同步冲突、重复判定、完成计划、加资料、读取失败、开始计划、旧代码） | DATA-02、TEACH-02、CHK-01、PLAN-10、UI-01、BUG-23 | 1a0d8ca | 已合入（完成） |
| 21-MF02 | MF-02 runtime | 课堂反复调用的缓存命中率提到 95% 以上（在新的思考强度下） | SYS-05、SYS-07、ARCH-08、QA-01 | 6791655 | 已合入（部分完成） |
| 21-MF04 | MF-04 platform | 资料权威性收尾：编程主题补到至少 3 份，并换一个编程题目验证 | PLAN-06、QA-01 | 6791655 | 已合入（部分完成） |
| 22-MF01 | MF-01 library | 全流程验收：模拟时钟 + 真实模型，从建计划跑到报告与自由检验（只找问题，不改产品代码） | QA-01 及全部已实现条目 | ad8368e | 已完成（部分：P1 卡在第 2 模块检测，最终检验/报告/自由检验未验收） |
| 23-MF03 | MF-03 client | 题库提前出且分小批（修检测卡死 P1）+ 主观题拿不准时再问一道更深的题、判定依据不要求逐字 | CHK-01、TEACH-04、PLAN-10、QA-01 | a7e1dd8 | 已合入（部分完成） |
| 23-MF01 | MF-01 library | 判定之前老师不透露答案；随口问不和讲稿例子原样重复 | TEACH-06、TEACH-08、QA-01 | a7e1dd8 | 已合入（部分完成） |
| 23-MF02 | MF-02 runtime | 查清课堂缓存命中只有 62.6% 的原因并修（先用验收保存的真实请求离线分析） | SYS-05、ARCH-08、SYS-07、QA-01 | a7e1dd8 | 已合入（部分完成） |
| 24-MF06 | MF-06 classroom | 新样本重跑全流程验收（历史：唐朝与丝绸之路）：只找问题，不改产品代码 | QA-01 及全部已实现条目 | 1d3f292 | 已完成（部分：预算用完停在第 2 天；P2 5 条、P3 3 条） |
| 25-MF01 | MF-01 library | 引导式分步课堂（讲课一侧）+ 讲稿核对不再白花钱 + 备课避开高峰价 | TEACH-06、TEACH-08、CHK-01 P2-02、QA-01 | 008ca8f | 已合入 43c033b（部分：真实只写 1 批稿，核对未跑） |
| 25-MF03 | MF-03 client | 题库固定 2 道、答错才补 + 出题格式程序把关 + 重测换题 + 过程题判法 + 检测计时 | CHK-01、TEACH-04、TEACH-02、QA-01 | 008ca8f | 已合入 43c033b（部分：真实出题 8/13 合格，停在第一段） |
| 25-MF02 | MF-02 runtime | 思考档位真正生效 + 输出上限与时限 + 用量记全 + 缓存离线分析 | SYS-07、SYS-01、SYS-03、SYS-05、QA-01 | 008ca8f | 已合入 43c033b（部分：缓存排列只能到约 70.7%，档案保持思考） |
| 25-MF05 | MF-05 tools | 引导式课堂界面 + 报告标注现场讲 | TEACH-06、TEACH-08、RPT-01、UI-00 | 008ca8f | 已合入 43c033b（完成，假数据核对） |
| 26-MF03 | MF-03 client | 题目结构与判法（不回避简答）+ 判法必须测过 | TEACH-04、CHK-01、QA-01 | a7d8db1 | 已起草，待用户转发 |
| 26-MF04 | MF-04 platform | 题库独立核对 + 出题质量实测 | CHK-01、TEACH-04、TEACH-08、QA-01 | a7d8db1 | 已起草，待用户转发 |
| 26-MF02 | MF-02 runtime | 用 prefixcash 查缓存断点 + 按 Manus 做法改老师与档案请求 | SYS-05、ARCH-08 | a7d8db1 | 已起草，待用户转发 |
| 26-MF01 | MF-01 library | 讲稿写稿与核对真实复测 + 真实后端接新界面联调 | TEACH-08、TEACH-06、QA-01 | a7d8db1 | 已起草，待用户转发 |

## 任务模板

新任务照这个写（下面是第二十三批 MF-03 的原文，换掉编号、工位、基线、背景和要做的事）。固定部分：开工同步、禁止事项、磁盘规则、QA-01 验收、花钱规则、决策原则、同时在跑的任务、先读、要做、验收、回执项目。

### 示例：[23-MF03] client｜题库提前出且分小批（修检测卡死 P1）+ 主观题拿不准时再问一道更深的题、判定依据不要求逐字

~~~text
【MentorFlow 第二十三批｜MF-03（工位 client）｜题库提前出且分小批（修检测卡死 P1）+ 主观题拿不准时再问一道更深的题、判定依据不要求逐字】

开工同步（第一步）
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard a7e1dd8
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设，总上限 ¥2，分两段——第一段最多 ¥1。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没有明显改善就停下交回执。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
背景：第二十二批全流程验收（小测试）在第 5 天卡住：报告 D:\MentorFlow\.local\test-tmp\acceptance\report.md，完整对话 dialogue.md，117 次调用的完整请求与用量在 calls\。用户要求把查出的问题一次修好，再换一个新样本重跑小测试。用户 2026-09-30 的决定已写进 CURRENT_STATE 的 [CHK-01]、[TEACH-04]、[TEACH-06]（各条“需求”下 2026-09-30 的那一行）。
同时在跑三个任务：23-MF03 改题库与判定；23-MF01 改老师不透露答案与随口问；23-MF02 查并修缓存（只动请求里内容的先后顺序，不改提示文字）。三人都可能碰 app/classroom.py 与 course/ 下的请求构建：各自只改自己那部分，冲突由架构师合并。
现象（验收报告 P1、P2 第 3 条）：6 个点的模块一次性生成整套题库，第一次输出超过 8192 被截断，加倍后超过 60 秒超时，第三次拿到 14 题却因一个点没有单点题被整批丢弃；每次“开始模块检测”约 90 多秒后仍没有题，流程卡死，“直接考”同样超时；模块 1 成功那次也等了 83.75 秒。另一处：判定员用“……”把学生原话几段拼起来当依据，逐字校验不过被当成“拿不准”，完整答对的综合题被要求补充。
先读：AGENTS.md；CURRENT_STATE 的 [CHK-01]、[TEACH-04]、[PLAN-10]、[QA-01]；验收报告与 calls\call-0113～0118.json；app/classroom.py 的题库与检测部分（_bank_select、_bank_fill、_prepare_later 里的出题、start_test、start_check、start_practice）、course/question_bank.py、question_agent.py、judge_agent.py、store 的题库部分。
要做
  1. 题库按 1–2 个点一小批出题（一次请求只管这几个点，含它们的单点题和最多 3–4 点的综合题）；一批失败只丢这一批，缺哪个点就只补哪个点；严格校验以“批”为单位，不因一个点缺题丢掉整个模块的题。
  2. 提前出：进入一个模块就在后台出这个模块的题；做这个模块检测时同时准备下一个模块；学最后一个模块时准备最终检验要用的题；“直接考”同样用提前备好的题。开始检测时题应已备好（目标：点开始后 2 秒内出第一题）；后台还没备好时显示“正在准备题目”，并在备好后自动出题，不让用户反复点；实在出不了明确说原因。
  3. 中途加资料（PLAN-10）：新模块自带题库；补充到已有知识点的，只让受影响模块的题库在后台重出。
  4. 主观题判定（TEACH-04 2026-09-30 行）：判定依据不要求逐字引用，同义说法算数；“拿不准”只看判定员自己标的不确定。拿不准时，针对同一个知识点再问一道更深的题（从题库取或现场出一道），这道答错就记错、答对就记对；替换掉现在“再独立判一次、不一致就追问一句”的做法。
  5. 验证：先离线（假模型覆盖：分批、单批失败只补缺点、提前备题的时机、备题中进入检测、加资料只重出受影响模块、拿不准时出深问题并按结果记账、同义说法判对）。再用真实模型在一个小固定计划上各测一次：一个 6 个点左右的模块提前备题并开始检测、一次“直接考”、一小段最终检验（第一段 ≤ ¥1）；记录开始检测等待时间、每批出题耗时与是否截断；你本人核对题目与判定。
验收：pytest tests/refactor 全部通过；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\bank-judge\report.md。
回执：【MF-03｜client｜第二十三批｜完成/部分完成/受阻】基线 ／ 提交 ／ 分批与补缺做法 ／ 提前备题的时机 ／ 开始检测等待时间（前后）／ 主观题新判法与核对结果 ／ 加资料的处理 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~
