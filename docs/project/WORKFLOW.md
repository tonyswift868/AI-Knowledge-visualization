# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读开头的「文档维护规则」和「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 开头三节和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-12）：2.0.14 已发布（非必须，含安装程序），服务器 MF-76。用户 13:36 在登录窗口点更新成功（约 43 秒，Updater 换新，清理 2.16 GB，[BUG-79]），第一轮同步拉 574 条 14 秒（[BUG-82] 每页偏慢，是用户代理网络所致，用户定暂不处理）。2.0.15 筹备：[BUG-84] 已合入；[BUG-85] 第一轮已合入（79%），第二轮 110-MF02 只测不改；用户定先不发版。下一步：用户走通注册（[ACC-02]）→ 发给朋友。无在途任务，员工全部空闲。全量里 loading86、self_test81、status88 三个浏览器测试满载时偶发失败（单独跑通过），记为已知不稳定测试。

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
| MF-02 | 110-MF02 已起草，待用户转发（[BUG-85] 第二轮：热词识别模型只测不改） | client |
| MF-03 | 空闲 | classroom |
| MF-04 | 空闲 | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 空闲 | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 无在途任务。用户已更新到 2.0.14 → 用户走通注册（[ACC-02]）→ 发给朋友。
- 108-MF02 作废（用户定：慢是用户自己的代理网络造成的，正常用户没问题）。
- 109-MF02 已合入（2d66cf0b，[BUG-85] 第一轮，术语认对 79%）。用户定：先做第二轮，先不发版。
- 110-MF02（client）：[BUG-85] 第二轮，比较热词识别模型，只测不改。已起草，待转发。用户可选：把自己录的 wav（文件名=原话）放到 .local/test-tmp/asr110/user-recordings/。
- 之后：109-MF02 合入 → 本机发版前总验收（MF-04；冻结程序里语音输入能用、2.0.14 → 2.0.15 软件内更新、全量）→ 用户说可以推送 → 发 2.0.15（含 [BUG-84]、[BUG-85]）。

## 第一百一十批任务原文（2026-10-13，基线 b0c68756）

### [110-MF02] client｜语音识别第二轮：热词模型只测不改（[BUG-85]）

~~~text
【MentorFlow 第一百一十批｜MF-02（工位 client）｜语音识别第二轮：只测不改产品，比较 2～3 个能在识别时按术语加权（热词）的本机识别模型，看能不能把术语认对率从 79% 提到 90% 以上（BUG-85）】

现成项目与许可证（第一节）
  - 只评估现成的本机识别方案，不自己训练、不改产品代码。候选（先查清许可证，GPL/AGPL 和禁止商用的直接排除，回执写明每个的许可证）：
    a. sherpa-onnx（Apache-2.0，项目已在用 1.12.31）的 transducer 中英模型 + 热词：modified_beam_search + hotwords，例如官方中英双语 zipformer 模型；查清中文热词需要的 modeling unit（cjkchar+bpe）和 bpe 词表。
    b. FunASR（MIT）的 SeACo-Paraformer 热词定制，或 sherpa-onnx 里能跑的带热词 paraformer，看哪个能在 Windows、Python 3.12 上不装 PyTorch 直接用 onnx 跑。模型权重的许可证单独查清。
    c. 你查到的其他同类方案，可选一个，例如 Whisper 系列用 initial prompt 塞术语；只有体积和速度在本机能接受时才测。
  - 基线是现在的 SenseVoice + 109-MF02 的纠正（①+②）。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard b0c68756         # 主线：已合入 109-MF02（第一轮纠正）
提交：只提交评估脚本（放 tests/refactor/asr/，文件名带 110），不改 refactor/ 下任何产品代码，不加产品依赖。
  git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读；E 盘只读运行，不写不复制，现有 SenseVoice 模型照 model_manager 的位置只读使用；不碰 D:\Kristina*。先读 AGENTS.md。
下载：候选模型和评估用的 Python 包一律下到 D:\MentorFlow\.local\test-tmp\asr110\ 下（Python 包装到这个目录里的独立虚拟环境，不动 D:\MentorFlow\.venv）。优先国内镜像（ModelScope、hf-mirror），用户走代理、外网慢。单个模型超过 1 GB 先不下，写进回执。测完全部删除，只留 report.md 和结果表。删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问。花费：基线 ①+② 要重跑时用真实模型，上限 ¥0.5，用模拟工具真实模式 --live --account-id mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932 --preset-root D:\MentorFlowData\account-shell，Key 不读、不显示、不保存。同时在跑：无。

背景（CURRENT_STATE [BUG-85]）：
  - 第一轮（109-MF02，已合入未发布）是识别完再改字：pypinyin 按本课词表近似替换，可疑时再调模型。
  - 结果：40 句合成语音、52 个术语，认对 29→41（78.9%），误换 0，说完平均 1 秒出字。
  - 卡点：声音一开始就被认成常见词（“向量那个”→“下料来个”），事后改不回。要上 90% 可能得在识别时就按术语加权。
  - 用户定（2026-10-13）：做第二轮，先只测、不发版；真要换模型，新模型下载要先问用户。
  - 第一轮的测试材料在 D:\MentorFlow\.local\test-tmp\asr109\（sentences.json、corpus.json、text-cases.json、report.md），评估脚本 tests/refactor/asr/asr109_evaluate.py。

要做
  1. 测试音频：
     - 用第一轮的 40 句（含两句用户真实例子的原意句：“向量那个。”和 GraphRAG／向量 RAG 那句）；
     - 用 Windows 自带中文语音合成，生成两种语速、两个可用的中文声音（如有），再各加一份轻微背景噪声版本。
     - 如果 D:\MentorFlow\.local\test-tmp\asr110\user-recordings\ 里有用户自己录的 wav（文件名就是原话），一并加入，单独报结果。
     - 合成语音比真人干净，回执注明。
  2. 每个候选：
     - 词表用第一轮同样的方法按本课生成，作为热词传入；
     - 测术语认对率、字错误率、误把口语认成术语的次数；
     - 测模型大小、首次加载时间、这台电脑上录音结束到出字的时间（平均和最慢）、内存占用。
     - 中英混说的术语（GraphRAG、Chunk、Agent）单独列。
     - 热词能不能每次录音时按课动态换（不重启、不重编译），必须写清。
  3. 同一套音频上，再跑一遍候选 + 第一轮 ①（+②）的组合，看叠加后的结果。
  4. 打包可行性（只调查不打包）：每个候选要打进软件需要哪些文件和依赖、多大、能不能被现有 PyInstaller 打包、是否需要额外运行库。
  5. 给出建议：
     - 换哪个，还是继续用现在的；
     - 达到 90% 的组合是哪种；
     - 要用户下载多大的模型；
     - 换的话产品要改哪些地方，预计几天。
     - 只建议，不改产品。

完成标准：每个候选都有第 2 条的完整数据（装不上、跑不起来的写清原因和证据）；第 5 条有明确建议；花费 ≤¥0.5。达不到就写“部分完成”。
回执：写到 D:\MentorFlow\.local\receipts\110-MF02.md。表格列：
  基线 ／ 提交 ／ 候选与许可证 ／ 术语认对率（基线、各候选、各组合）／ 误认 ／ 模型大小与加载时间 ／ 出字时间 ／ 热词能否按课动态换 ／ 打包可行性 ／ 建议 ／ 花费 ／ 本地最终占用。
给用户只回一行“110-MF02 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\asr110\report.md。
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
