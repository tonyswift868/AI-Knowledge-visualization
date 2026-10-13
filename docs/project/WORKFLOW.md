# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读开头的「文档维护规则」和「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 开头三节和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-14）：线上 2.0.14，服务器 MF-76。已合入待发：[BUG-84]、[BUG-85] 第一轮；[BUG-86] 两轮原型已合入未接线。第一百一十四批四份并行已起草（重切跑完、换进计划的工具、按小要点出题、课堂多轮），待转发。四份合入后：架构师在用户数据上预演 → 用户看预演报告和对照网页点头 → 执行（备份、可回滚）→ 新建计划流程接线 → 总验收 → 发 2.0.15。全量里 loading86、self_test81、status88 三个浏览器测试满载时偶发失败（单独跑通过），记为已知不稳定测试。

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
| MF-01 | 114-MF01 已起草，待用户转发（按小要点出题） | server |
| MF-02 | 空闲 | client |
| MF-03 | 114-MF03 已起草，待用户转发（课堂多轮、资料导航、讲义） | classroom |
| MF-04 | 空闲（111-MF04 作废） | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 114-MF06 已起草，待用户转发（重切第三轮，跑完第 5～8 模块） | tools |
| MF-07 | 114-MF07 已起草，待用户转发（换进计划的工具：预演、执行、回滚） | library |

## 待派队列

- 第一百一十四批四份并行（[BUG-86]，共用数据约定写在每份任务里）：114-MF06、114-MF07、114-MF01、114-MF03。已起草，待转发。
- 之后（依赖四份都合入）：架构师用 114-MF07 的工具在用户数据上预演（用户软件先关），把预演报告（重排前后总时长、今天目标、逾期）和 114-MF06 的对照网页给用户看；用户点头才执行（自动备份、可回滚）；再派：新建计划流程接上新方法；发版前总验收；发 2.0.15（含 [BUG-84]、[BUG-85] 第一轮、[BUG-86]）。
- 待用户定：[BUG-85] 第三轮换 Whisper。

## 第一百一十四批任务原文（2026-10-14，基线 cb1d59c3）

### [114-MF06] tools｜知识点重切第三轮：修遗留、跑完第 5～8 模块

~~~text
【MentorFlow 第一百一十四批｜MF-06（工位 tools）｜知识点重切第三轮：修好两处遗留问题，跑完第 5～8 模块，产出最终新结构和对照网页（BUG-86）】

现成项目与许可证（第一节）
  - 不引新库，在你 112、113 的原型上改；模型调用用现有 gateway.complete。做法同前两轮（先分类再抽取、map-reduce 合并、知识组件粒度、抽取式讲义）。

开工同步
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard cb1d59c3         # 主线：已合入 113-MF06 原型（未接线）
提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读：要用用户数据就用 sqlite3 mode=ro 的 backup 复制到任务目录再用，不改原件、不改用户进度。不要启动、结束或重启用户的软件和源代码版。
E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。产物在 D:\MentorFlow\.local\test-tmp\points114\，中间产物用完即删，只留 report.md 和必要的小文件；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限或要动用户数据才停。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明）。
花费：真实模型总上限 ¥12，分两段：先重跑第 5 模块剩余部分，六项过关标准全过才跑第 6、7、8 模块；第一段不过关就停下写清原因。真实模式参数同上一轮（--live --account-id mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932 --preset-root D:\MentorFlowData\account-shell；Key 不读、不显示、不保存）。
同时在跑（第一百一十四批四份并行，各改各的文件，不要碰别人的范围）：
  - MF-06（tools）：修完重切方法并跑完第 5～8 模块，只改 planner_agents.py 的新函数和 points 脚本；
  - MF-07（library）：把新结构换进用户计划的工具，改 course/restructure.py（新）、store.py 的校验、scripts/plan_restructure.py（新）；
  - MF-01（server 工位）：新式点按小要点出题，改 course/point_materials.py、course/question_bank.py；
  - MF-03（classroom）：课堂按小要点多轮讲、显示资料导航，改 app/classroom.py、course/teacher_agent.py、课堂前端。
  

【第一百一十四批共用数据约定（四份任务相同，任何一份都不能自行改动；要改先写进回执由架构师统一定）】
  - 新式知识点仍存在 points 表，payload 必填字段不变：
    - id、module_id、position、title、summary、minutes、chunk_ids；
    - summary 写掌握目标，minutes 等于各小要点分钟之和，chunk_ids 为各小要点出处的并集，按出现顺序。
  - points 新增可选字段：
    - structure: "concept_v1"（有它就是新式点）；
    - goal: 字符串，同 summary；
    - subpoints: 列表，每项 {id, title, minutes, chunk_ids, notes}：
      - id 形如 "<点id>-s1"、"<点id>-s2"；
      - minutes 为正整数；
      - chunk_ids 只能是本计划已有的 source_chunks id；
      - notes 是讲义，每条 {text, chunk_id}，chunk_id 必须属于这个小要点的 chunk_ids。
  - modules 新增可选字段：
    - navigation: 列表，每项 {id, title, chunk_ids, reason}；
    - 只供查看，不讲、不考、不算时长。
  - question_bank 新增可选字段 subpoint_id：题目对应的小要点，必须属于该题 point 的 subpoints。
  - 旧式点（没有 structure 字段）的行为一律不变。用户计划前面已学的部分仍是旧式点，新旧两种会在同一计划里共存。
  - 样例数据：D:\MentorFlow\.local\test-tmp\points113\new-structure.json 第 5 模块剩余部分。
    - 里面 points[].subpoints[].bullets 对应这里的 notes，refs 对应 chunk_ids，cN 别名要换成真实 chunk id，见其 paragraphs 表。
    - 不要改这个文件，复制一份再用。

背景（CURRENT_STATE [BUG-86]）：
  - 用户的计划“ai-agent学习”（id 029b076315034c00bbd6842a5c9c31d9，JavaGuide，541 个旧点）原来按篇幅切点，约每 1000 字一个点，书单也成了点。
  - 用户定：书单等放模块开头的资料导航，不讲不考不算时长；一个大知识点 20～40 分钟、3～8 个小要点，讲课多轮对话、不走固定公式；原文提炼成带出处的讲义；从用户当前学到的位置往后全部重切，已学和已跳过的保留；改完才发 2.0.15。
  - 113-MF06 的第二轮原型：第 5 模块剩余 44 点 → 7 个大点、43 个小要点、29 条导航，书单 17/17 进导航，讲义没有书籍简介，总时长 259→241 分。
  - 用户看过对照网页，觉得样子不错。
  - 用户想尽快用新结构学习，并在换之前看到重排后的总时长、每天目标和逾期是否合理。
  - 113 的两处遗留（见 D:\MentorFlow\.local\test-tmp\points113\report.md）：
    1. NoSQL 总览没归进它下面的四类原子概念，K2 和 K3 的第 5 个小要点重复：合并只看概念编号唯一，不保证语义不重复。
    2. K7 一段 6 行、99 字的 SQL 示例两次被“超过 3 个换行”的限制拒掉，K7 缺 8 个小要点的讲义。

要做
  1. 合并层级：
     - “总览/定义 + 若干子类”这类概念要归成一个大点，总览做第一个小要点，不能另起一个大点再重复；
     - 合并后加一步语义去重检查：同模块内两个小要点讲的是同一件事，就合并或报警，在报告列出。
  2. 讲义里的短代码/SQL 示例：允许不超过 15 行、300 字的原文示例原样摘录；超过的只摘关键行并标出处。
  3. 用户当前位置以运行时读到的副本为准：用户这几天可能还在学，从最新位置往后切。回执写明从哪一点开始。
  4. 第一段重跑第 5 模块剩余部分，六项过关标准同 113（书单全进导航；讲义无书籍简介；重复合并；每大点 3～8 小要点且时长有内容依据；知识未覆盖 0；总分钟与旧知识类估时差 ±20% 内或逐项说明）。全过后跑第 6、7、8 模块，每个模块同样自查。
  5. 产出：
     - 最终 new-structure.json：结构照 113，另加一份按上面“共用数据约定”换好字段名和真实 chunk id 的 apply-ready.json，给 MF-07 的工具直接用；
     - 对照网页（沿用 113 的样子，用户已认可）；
     - 每模块旧点数 → 新点数与分钟。
  6. 测试（文件名带 114）：总览加子类合并、语义去重报警、短代码示例放行与超长截断、apply-ready.json 的字段与约定一致；112、113 的测试照过。
  7. 只改 course/planner_agents.py 中你加的新函数和 points 系列脚本与测试。不接线，不改别人的范围。

完成标准：第 5～8 模块全部跑完，且每模块自查过关（不过关的写清卡在哪）；apply-ready.json 与约定一致；测试通过；最后全量一次。花费不超过 ¥12。
回执：写到 D:\MentorFlow\.local\receipts\114-MF06.md。表格列：基线 ／ 提交 ／ 两处修法 ／ 从哪一点开始 ／ 各模块旧点数 → 新点数与分钟 ／ 各模块自查 ／ 文件路径（new-structure.json、apply-ready.json、网页）／ 新增测试 ／ 花费 ／ 全量 ／ 本地最终占用。
给用户只回一行“114-MF06 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\points114\report.md。
~~~

### [114-MF07] library｜把新结构换进用户计划的工具（预演、执行、回滚）

~~~text
【MentorFlow 第一百一十四批｜MF-07（工位 library）｜把新的知识点结构换进用户计划的工具：先预演给出重排前后的总时长、每天目标和逾期，再按用户确认执行，保留全部进度，可回滚（BUG-86）】

现成项目与许可证（第一节）
  - 不引新库。全部走现有存储与同步：CourseStore 的 _put/删除（会同步到服务器），重排账本用现有 replan（BUG-83 的规则：历史欠账按当天冻结，只重新分配今天及以后）。
  - 备份用 SQLite 自带的 backup API。

开工同步
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard cb1d59c3         # 主线：已合入 113-MF06 原型（未接线）
提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读：要用用户数据就用 sqlite3 mode=ro 的 backup 复制到任务目录再用，不改原件、不改用户进度。不要启动、结束或重启用户的软件和源代码版。
E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。产物在 D:\MentorFlow\.local\test-tmp\restructure114\，中间产物用完即删，只留 report.md 和必要的小文件；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限或要动用户数据才停。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明）。
花费：¥0（不调用模型）。
同时在跑（第一百一十四批四份并行，各改各的文件，不要碰别人的范围）：
  - MF-06（tools）：修完重切方法并跑完第 5～8 模块，只改 planner_agents.py 的新函数和 points 脚本；
  - MF-07（library）：把新结构换进用户计划的工具，改 course/restructure.py（新）、store.py 的校验、scripts/plan_restructure.py（新）；
  - MF-01（server 工位）：新式点按小要点出题，改 course/point_materials.py、course/question_bank.py；
  - MF-03（classroom）：课堂按小要点多轮讲、显示资料导航，改 app/classroom.py、course/teacher_agent.py、课堂前端。
  

【第一百一十四批共用数据约定（四份任务相同，任何一份都不能自行改动；要改先写进回执由架构师统一定）】
  - 新式知识点仍存在 points 表，payload 必填字段不变：
    - id、module_id、position、title、summary、minutes、chunk_ids；
    - summary 写掌握目标，minutes 等于各小要点分钟之和，chunk_ids 为各小要点出处的并集，按出现顺序。
  - points 新增可选字段：
    - structure: "concept_v1"（有它就是新式点）；
    - goal: 字符串，同 summary；
    - subpoints: 列表，每项 {id, title, minutes, chunk_ids, notes}：
      - id 形如 "<点id>-s1"、"<点id>-s2"；
      - minutes 为正整数；
      - chunk_ids 只能是本计划已有的 source_chunks id；
      - notes 是讲义，每条 {text, chunk_id}，chunk_id 必须属于这个小要点的 chunk_ids。
  - modules 新增可选字段：
    - navigation: 列表，每项 {id, title, chunk_ids, reason}；
    - 只供查看，不讲、不考、不算时长。
  - question_bank 新增可选字段 subpoint_id：题目对应的小要点，必须属于该题 point 的 subpoints。
  - 旧式点（没有 structure 字段）的行为一律不变。用户计划前面已学的部分仍是旧式点，新旧两种会在同一计划里共存。
  - 样例数据：D:\MentorFlow\.local\test-tmp\points113\new-structure.json 第 5 模块剩余部分。
    - 里面 points[].subpoints[].bullets 对应这里的 notes，refs 对应 chunk_ids，cN 别名要换成真实 chunk id，见其 paragraphs 表。
    - 不要改这个文件，复制一份再用。

背景（CURRENT_STATE [BUG-86]）：
  - 用户的计划“ai-agent学习”（id 029b076315034c00bbd6842a5c9c31d9，JavaGuide，541 个旧点）原来按篇幅切点，约每 1000 字一个点，书单也成了点。
  - 用户定：书单等放模块开头的资料导航，不讲不考不算时长；一个大知识点 20～40 分钟、3～8 个小要点，讲课多轮对话、不走固定公式；原文提炼成带出处的讲义；从用户当前学到的位置往后全部重切，已学和已跳过的保留；改完才发 2.0.15。
  - 113-MF06 的第二轮原型：第 5 模块剩余 44 点 → 7 个大点、43 个小要点、29 条导航，书单 17/17 进导航，讲义没有书籍简介，总时长 259→241 分。
  - 用户看过对照网页，觉得样子不错。
  - 用户想尽快用新结构学习，并在换之前看到重排后的总时长、每天目标和逾期是否合理。

要做
  1. store.py：按约定校验新增的可选字段（points 的 structure/goal/subpoints、modules 的 navigation、question_bank 的 subpoint_id）。
     - 只在字段出现时检查形状和引用（chunk id 属于本计划、notes 的 chunk_id 属于本小要点、subpoint_id 属于该点）；
     - 旧数据和没有这些字段的记录照常；
     - 不升数据库版本，除非确有必要，必要的话照 SCHEMA_VERSION 迁移写法并说明。
  2. 新建 course/restructure.py：apply_structure(store, plan_id, structure, *, dry_run)。
     - structure 就是 apply-ready.json 的内容。
     - 确定保留哪些点：当前位置（classroom_positions 与进度记录）之前的点，以及任何有讲过、作答、判定、跳过、时长记录的点，一律保留原样。
     - 结构里覆盖的其余旧点：删除，或标成被新点取代（自己选，要能同步、能回滚，写理由）。
     - 写入新式点和模块导航；更新模块的 point_count、learning_minutes。
     - 被取代旧点的题库题标成退役（不删作答记录）。
     - 课堂位置如果指向被取代的点，改指到第一个新点。
     - 按新总时长走现有 replan，cause 写 restructure，历史不动。
  3. 预演（dry_run）报告，给用户看的，要直白：
     - 保留多少旧点、取代多少、新增多少新点和导航条目；
     - 每模块重排前后的分钟；
     - 计划总分钟前后；
     - 按今天算的“逾期待还”和“今天目标”前后、之后每天目标前后；
     - 任何对不上的地方（例如结构里引用的 chunk 不存在、要取代的点其实有进度）一律列出并拒绝执行。
  4. 执行与回滚：新建 scripts/plan_restructure.py。
     - 子命令：--dry-run、--apply、--rollback；
     - 参数：用户数据目录、计划 id、结构文件；
     - apply 前自动备份整个 course.sqlite3 到同目录带时间戳的文件，rollback 用它恢复；
     - 写清楚：回滚只恢复本机，已同步到服务器的变化怎么处理（例如回滚后再以本机为准重新上传），自己设计并测试。
     - 要求软件没在运行（检测到就拒绝）。
  5. 测试（文件名带 114，用约定里的样例和一份临时库）：
     - 保留有进度的点；
     - 取代其余点；
     - 导航写入；
     - 题库退役；
     - 课堂位置改指；
     - replan 不动历史欠账；
     - 预演与执行一致；
     - 回滚后库与执行前一致；
     - 引用不存在的 chunk 时拒绝；
     - 另一台设备同步后看到同样的新结构。
  6. 用用户计划的只读副本 + 113 的样例结构（换成约定格式）做一次预演，把报告贴进回执。不在用户真实数据上执行。
  7. 只改 store.py（校验）、course/restructure.py、scripts/plan_restructure.py 和 tests/refactor/。

完成标准：第 5 条测试通过；第 6 条预演报告完整；最后全量一次。
回执：写到 D:\MentorFlow\.local\receipts\114-MF07.md。表格列：基线 ／ 提交 ／ 保留与取代规则 ／ 取代方式（删除还是标记）与理由 ／ 预演报告（副本）／ 回滚与同步怎么处理 ／ 新增测试 ／ 全量 ／ 本地最终占用。
给用户只回一行“114-MF07 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\restructure114\report.md。
~~~

### [114-MF01] server｜新式知识点按小要点出题

~~~text
【MentorFlow 第一百一十四批｜MF-01（工位 server，本任务改的是客户端的出题代码）｜新式知识点按小要点出题：每个小要点从自己的原文出客观题，题目标上小要点（BUG-86）】

现成项目与许可证（第一节）
  - 不引新库。沿用现有出题链：course/point_materials.py 的 prepare_point，现有的“先用资料自带的题、没有再从原文挖空”的规则，干扰项检查 distractor_check，以及 question_bank 的入库与判分规则（[DIR-01]）。

开工同步
  cd D:\MentorFlow-office\server\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/server
  git reset --hard cb1d59c3         # 主线：已合入 113-MF06 原型（未接线）
提交：git -c user.name="MentorFlow Worker server" -c user.email="worker-server@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读：要用用户数据就用 sqlite3 mode=ro 的 backup 复制到任务目录再用，不改原件、不改用户进度。不要启动、结束或重启用户的软件和源代码版。
E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。产物在 D:\MentorFlow\.local\test-tmp\questions114\，中间产物用完即删，只留 report.md 和必要的小文件；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限或要动用户数据才停。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明）。
花费：替身模型离线做完；最后允许一次真实模型核对，上限 ¥1，用模拟工具真实模式 --live --account-id mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932 --preset-root D:\MentorFlowData\account-shell，Key 不读、不显示、不保存。
同时在跑（第一百一十四批四份并行，各改各的文件，不要碰别人的范围）：
  - MF-06（tools）：修完重切方法并跑完第 5～8 模块，只改 planner_agents.py 的新函数和 points 脚本；
  - MF-07（library）：把新结构换进用户计划的工具，改 course/restructure.py（新）、store.py 的校验、scripts/plan_restructure.py（新）；
  - MF-01（server 工位）：新式点按小要点出题，改 course/point_materials.py、course/question_bank.py；
  - MF-03（classroom）：课堂按小要点多轮讲、显示资料导航，改 app/classroom.py、course/teacher_agent.py、课堂前端。
  工位 server 上先 git status：上一轮作废的 107-MF01 可能留有改动，有就先 git stash 留存再开工，回执写明。

【第一百一十四批共用数据约定（四份任务相同，任何一份都不能自行改动；要改先写进回执由架构师统一定）】
  - 新式知识点仍存在 points 表，payload 必填字段不变：
    - id、module_id、position、title、summary、minutes、chunk_ids；
    - summary 写掌握目标，minutes 等于各小要点分钟之和，chunk_ids 为各小要点出处的并集，按出现顺序。
  - points 新增可选字段：
    - structure: "concept_v1"（有它就是新式点）；
    - goal: 字符串，同 summary；
    - subpoints: 列表，每项 {id, title, minutes, chunk_ids, notes}：
      - id 形如 "<点id>-s1"、"<点id>-s2"；
      - minutes 为正整数；
      - chunk_ids 只能是本计划已有的 source_chunks id；
      - notes 是讲义，每条 {text, chunk_id}，chunk_id 必须属于这个小要点的 chunk_ids。
  - modules 新增可选字段：
    - navigation: 列表，每项 {id, title, chunk_ids, reason}；
    - 只供查看，不讲、不考、不算时长。
  - question_bank 新增可选字段 subpoint_id：题目对应的小要点，必须属于该题 point 的 subpoints。
  - 旧式点（没有 structure 字段）的行为一律不变。用户计划前面已学的部分仍是旧式点，新旧两种会在同一计划里共存。
  - 样例数据：D:\MentorFlow\.local\test-tmp\points113\new-structure.json 第 5 模块剩余部分。
    - 里面 points[].subpoints[].bullets 对应这里的 notes，refs 对应 chunk_ids，cN 别名要换成真实 chunk id，见其 paragraphs 表。
    - 不要改这个文件，复制一份再用。

背景（CURRENT_STATE [BUG-86]）：
  - 用户的计划“ai-agent学习”（id 029b076315034c00bbd6842a5c9c31d9，JavaGuide，541 个旧点）原来按篇幅切点，约每 1000 字一个点，书单也成了点。
  - 用户定：书单等放模块开头的资料导航，不讲不考不算时长；一个大知识点 20～40 分钟、3～8 个小要点，讲课多轮对话、不走固定公式；原文提炼成带出处的讲义；从用户当前学到的位置往后全部重切，已学和已跳过的保留；改完才发 2.0.15。
  - 113-MF06 的第二轮原型：第 5 模块剩余 44 点 → 7 个大点、43 个小要点、29 条导航，书单 17/17 进导航，讲义没有书籍简介，总时长 259→241 分。
  - 用户看过对照网页，觉得样子不错。
  - 用户想尽快用新结构学习，并在换之前看到重排后的总时长、每天目标和逾期是否合理。

要做
  1. 新式点（有 structure: concept_v1）出题时按小要点走：
     - 每个小要点从它自己的 chunk_ids 出 1 道客观题（DIR-01 的题型和规则不变），题目写上 subpoint_id；
     - 某个小要点出不了题时，按现有规则跳过并记录原因，不硬凑；
     - 一个大点的题数等于能出题的小要点数。
  2. 模块检测、重测、最终检验、自由检验取题时，新式点按小要点的题来，旧式点不变。
     - 现有“每点 2 题”的规则对新式点改成“每个小要点 1 题”；
     - 合格线照现有规则按题算；
     - 在 question_bank 的取题处实现，写清改了哪些函数。
  3. 不改 store.py：约定里的新字段 store 本来就容许多余字段，MF-07 会补校验。
  4. 测试（文件名带 114）：
     - 新式点按小要点出题并带 subpoint_id；
     - 出不了题的小要点跳过并记录；
     - 模块检测按小要点取题；
     - 旧式点出题与取题完全不变。
  5. 真实核对（≤¥1）：用约定样例里第 5 模块的 2 个大点出题，逐题看是否考到对应小要点、答案是否在原文里、有没有两个选项都对。回执写结果和花费。
  6. 只改 course/point_materials.py、course/question_bank.py（如需，app/plan_flow.py 里调用出题的地方）和 tests/refactor/。

完成标准：第 4 条测试通过；第 5 条核对完成；最后全量一次。花费不超过 ¥1。
回执：写到 D:\MentorFlow\.local\receipts\114-MF01.md。表格列：基线 ／ 提交 ／ 出题怎么按小要点 ／ 取题与合格线怎么改 ／ 新增测试 ／ 真实核对（题数、考到小要点、答案在原文、两个都对、花费）／ 全量 ／ 本地最终占用。
给用户只回一行“114-MF01 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\questions114\report.md。
~~~

### [114-MF03] classroom｜课堂按小要点多轮讲、资料导航、讲义

~~~text
【MentorFlow 第一百一十四批｜MF-03（工位 classroom）｜课堂按小要点多轮讲新式知识点，不走固定公式；模块开头显示资料导航；显示讲义（BUG-86）】

现成项目与许可证（第一节）
  - 不引新库。沿用现有课堂状态机（app/classroom.py）、老师提示与解析（course/teacher_agent.py）、讲解与出处显示（前端 Markdown 与原文出处），以及 [TEACH-06] 的对话规则（生活化问题开头、顺着学生回答讲、插话与“懂了，继续／没太懂”）。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard cb1d59c3         # 主线：已合入 113-MF06 原型（未接线）
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读：要用用户数据就用 sqlite3 mode=ro 的 backup 复制到任务目录再用，不改原件、不改用户进度。不要启动、结束或重启用户的软件和源代码版。
E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。产物在 D:\MentorFlow\.local\test-tmp\class114\，中间产物用完即删，只留 report.md 和必要的小文件；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限或要动用户数据才停。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明）。
花费：替身模型离线做完；最后允许一次真实模型核对，上限 ¥2，用模拟工具真实模式（参数同以往：--live --account-id mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932 --preset-root D:\MentorFlowData\account-shell；Key 不读、不显示、不保存）。
同时在跑（第一百一十四批四份并行，各改各的文件，不要碰别人的范围）：
  - MF-06（tools）：修完重切方法并跑完第 5～8 模块，只改 planner_agents.py 的新函数和 points 脚本；
  - MF-07（library）：把新结构换进用户计划的工具，改 course/restructure.py（新）、store.py 的校验、scripts/plan_restructure.py（新）；
  - MF-01（server 工位）：新式点按小要点出题，改 course/point_materials.py、course/question_bank.py；
  - MF-03（classroom）：课堂按小要点多轮讲、显示资料导航，改 app/classroom.py、course/teacher_agent.py、课堂前端。
  

【第一百一十四批共用数据约定（四份任务相同，任何一份都不能自行改动；要改先写进回执由架构师统一定）】
  - 新式知识点仍存在 points 表，payload 必填字段不变：
    - id、module_id、position、title、summary、minutes、chunk_ids；
    - summary 写掌握目标，minutes 等于各小要点分钟之和，chunk_ids 为各小要点出处的并集，按出现顺序。
  - points 新增可选字段：
    - structure: "concept_v1"（有它就是新式点）；
    - goal: 字符串，同 summary；
    - subpoints: 列表，每项 {id, title, minutes, chunk_ids, notes}：
      - id 形如 "<点id>-s1"、"<点id>-s2"；
      - minutes 为正整数；
      - chunk_ids 只能是本计划已有的 source_chunks id；
      - notes 是讲义，每条 {text, chunk_id}，chunk_id 必须属于这个小要点的 chunk_ids。
  - modules 新增可选字段：
    - navigation: 列表，每项 {id, title, chunk_ids, reason}；
    - 只供查看，不讲、不考、不算时长。
  - question_bank 新增可选字段 subpoint_id：题目对应的小要点，必须属于该题 point 的 subpoints。
  - 旧式点（没有 structure 字段）的行为一律不变。用户计划前面已学的部分仍是旧式点，新旧两种会在同一计划里共存。
  - 样例数据：D:\MentorFlow\.local\test-tmp\points113\new-structure.json 第 5 模块剩余部分。
    - 里面 points[].subpoints[].bullets 对应这里的 notes，refs 对应 chunk_ids，cN 别名要换成真实 chunk id，见其 paragraphs 表。
    - 不要改这个文件，复制一份再用。

背景（CURRENT_STATE [BUG-86]）：
  - 用户的计划“ai-agent学习”（id 029b076315034c00bbd6842a5c9c31d9，JavaGuide，541 个旧点）原来按篇幅切点，约每 1000 字一个点，书单也成了点。
  - 用户定：书单等放模块开头的资料导航，不讲不考不算时长；一个大知识点 20～40 分钟、3～8 个小要点，讲课多轮对话、不走固定公式；原文提炼成带出处的讲义；从用户当前学到的位置往后全部重切，已学和已跳过的保留；改完才发 2.0.15。
  - 113-MF06 的第二轮原型：第 5 模块剩余 44 点 → 7 个大点、43 个小要点、29 条导航，书单 17/17 进导航，讲义没有书籍简介，总时长 259→241 分。
  - 用户看过对照网页，觉得样子不错。
  - 用户想尽快用新结构学习，并在换之前看到重排后的总时长、每天目标和逾期是否合理。
  - 用户对现在讲课的原话：“每一个知识点全部都是给了一个影子，我回答一下，然后他再下一段里面再出一句话，再给一个问题，然后这个点就结束了，永远是这样，太公式了。”要的是一个点里多轮对话。

要做
  1. 新式点（有 structure: concept_v1）按小要点讲：
     - 一个大点依次讲它的小要点，每个小要点以讲义 notes 和对应原文为依据；
     - 老师顺着学生的回答决定下一步：答对就往深一层或换个角度追问一句，答错或不知道就换个讲法再讲，学生说“懂了”就进下一个小要点；
     - 一个小要点可以有几轮，不设固定轮数；不能每个小要点都套“举例 → 提问 → 讲一段 → 结束”的同一模板，开场方式在提问、举例、对比、从上一个小要点接过来之间变化；
     - 大点讲完给一两句收束，进入下一点。
     - 课堂位置记住讲到第几个小要点，换设备、重开都接着（课堂位置已随账号同步，105-MF03）。
  2. 界面：
     - 讲新式点时显示“小要点 2/5”这类进度；
     - 当前小要点的讲义可展开看，每条能点开原文；
     - 模块开始时显示这个模块的资料导航（折叠的一块，“资料导航（N 条）”，展开能看条目和原文），不讲、不出题、不算时长。
  3. 时长：沿用现有记时长的方式，大点讲完按点记，不改账本与 store；需要按小要点记的话写进回执作为后续建议。
  4. 旧式点（没有 structure 字段）讲法完全不变：用户计划前面已学的部分是旧式点。
  5. 测试（文件名带 114）：
     - 新式点逐个小要点推进；
     - 学生答错时同一小要点再讲一轮；
     - “懂了，继续”进下一小要点；
     - 重开后从同一小要点接着；
     - 资料导航显示且不进出题和时长；
     - 讲义出处可点开；
     - 旧式点照旧。
     - 前端用现有浏览器测试写法。
  6. 真实核对（≤¥2）：用约定样例的 2 个大点各讲一遍，模拟学生有对有错有“没懂”，看是否多轮、是否套模板、讲的是否都在讲义和原文里。回执写对话摘录和花费。
  7. 只改 app/classroom.py、course/teacher_agent.py、课堂前端相关文件和 tests/refactor/。不改 store.py、出题代码、计划流程。前端改了要编一次网页确认能编过，不提交 dist。

完成标准：第 5 条测试通过；第 6 条核对完成；最后全量一次。花费不超过 ¥2。
回执：写到 D:\MentorFlow\.local\receipts\114-MF03.md。表格列：基线 ／ 提交 ／ 多轮怎么做（怎么避免固定公式）／ 界面（小要点进度、讲义、资料导航）／ 时长怎么记 ／ 新增测试 ／ 真实核对摘录与花费 ／ 全量 ／ 本地最终占用。
给用户只回一行“114-MF03 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\class114\report.md。
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
