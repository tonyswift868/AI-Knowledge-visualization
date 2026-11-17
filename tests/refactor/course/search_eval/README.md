# 420 找资料离线评测与显式联网实验

这里是 420-MF07 的评测工具与逐条标注，不是产品运行代码。文件名不以 `test_` 开头，普通 pytest 不会触发联网。`run420.py` 只有明确执行时才会读项目 connections 凭据并调用 DeepSeek；其他脚本离线运行。没有修改用户设置、计划或资料。

## 现成组件与出处

- 拆模块、中英文短词：借鉴 [GPT Researcher prompts.py / generate_search_queries_prompt](https://github.com/assafelovic/gpt-researcher/blob/master/gpt_researcher/prompts.py)（Apache-2.0）和 [STORM knowledge_curation.py / QuestionToQuery](https://github.com/stanford-oval/storm/blob/main/knowledge_storm/storm_wiki/modules/knowledge_curation.py)（MIT）的提示词思路，没有引入整套框架。
- URL 预筛：[courlan](https://github.com/adbar/courlan)（Apache-2.0）；正文：[trafilatura 2.2.0](https://github.com/adbar/trafilatura)（Apache-2.0），优先正文、不提取评论；PDF 沿用项目提取器。
- 逐页 0–3 判分：改编 [UMBRELA qrel_zeroshot_bing.yaml](https://github.com/castorini/umbrela/blob/main/src/umbrela/prompts/prompt_templates/qrel_zeroshot_bing.yaml)（Apache-2.0），将相关性改为教学正文、主题和入门水平匹配。
- 读页面前重排：[FlashRank](https://github.com/PrithivirajDamodaran/FlashRank) 库 Apache-2.0；`ms-marco-MultiBERT-L-12` 的[模型权重仓库](https://huggingface.co/prithivida/flashrank)标的是 **CC-BY-SA-4.0**，不能把库的许可证当成权重许可证。
- 评估参考：[Thomas 等，Large language models can accurately predict searcher preferences](https://arxiv.org/abs/2309.10621)。本实验独立子代理标注不是用户亲测金标。

## 实验边界

用户指定的 10 个主题固定在 `run420.TOPICS`，任何主题失败都继续剩余主题。A 用两条长词各取 5 个搜索结果，再走项目现有 ResearchPipeline；B/C 共用三模块中英文六条短词、每条 3 个搜索结果和项目已有可信目录，以控制两者的候选池。B 与 C 都先遵守原来源分档；C 在同一档内按多语言模型标题加摘要重排。未手写补充候选网址或白名单。

各方案有独立 HTTP 缓存，B/C 分别读取、分别逐页判分，分数 3 才采用，最多采用 5 份。C 排序完成后才读取正文。模型只能写搜索词和给已读候选判分，候选网址仅来自搜索接口或现有目录。各方案结束后才补读完整候选并集供独立标注，这部分额外读取不算进方案读取数。

盲标文件只含候选 ID、主题、网址、标题、提取正文和读取错误，不含方案、排序或模型判分。正文采用现有提取与 trafilatura 提取中较长的一份、最多 8,000 字符。标注者逐条给出能用/勉强/不能用、理由和完整资料/单篇补充/非正文。被预筛或读取失败的候选也必须标注。正文截断、同机网络时序以及一次抽样都是此次评测的局限。

`precision@5` 计算各方案候选排序 `order` 前五中的“能用”数，固定分母 5；不足 5 条也不补足分母。最终采用集合另报 selected_count、selected_precision；门槛看最终采用集合能用数至少 3，不能把从后续候选中补齐的采用结果当成排名前五命中。Cohen's kappa 使用三类、无权重；模型 3→能用、2→勉强、0/1→不能用，只对实际调用判分的页面计算，并同时报告样本数。A 没有判分模型，kappa 不适用。汇总脚本要求 10×3 全覆盖且无重复行、标签无重复且与候选池完全一致后才计算门槛。

B/C 共享拆词与搜索的实际调用，表中每个方案的费用和时间将这部分分别计入，表示独立运行估计，不能把表格费用直接相加当实扣。实际付费总额单独从 `costs.json` 相加。按 [DeepSeek 人民币峰时价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)保守计费：输入 cache miss ¥2/百万 token、输出 ¥8/百万 token；[Search](https://api-docs.deepseek.com/guides/search/)成功非空结果 ¥0.005/次。没有使用收费 Fetch。模型调用用官方 `deepseek-flash`，不修改用户预设。

时间列 `elapsed_seconds` 包含该方案真实读取判分时间、它的拆词搜索耗时及 C 的重排时间，不含下载模型和事后标注补读。原始 `topic_wall_seconds` 是执行到该方案时累计主题耗时，不能用作方案耗时。模型 load_seconds 包含首次下载；内存是当前进程工作集差值，不是峰值或最终产品增量。

## 离线复算

在工位根目录运行（不读密钥、不联网、不需要安装实验依赖）：

```powershell
& D:\MentorFlow\.venv\Scripts\python.exe -B tests/refactor/course/search_eval/summarize420.py --work tests/refactor/course/search_eval/evidence --labels tests/refactor/course/search_eval/labels.jsonl
& D:\MentorFlow\.venv\Scripts\python.exe -B tests/refactor/course/search_eval/metrics420.py
```

`evidence/blind-pool.jsonl` 只保留 ID、主题、网址、标题和读取错误，删除第三方正文；`runs.json` 保存真实搜索词、候选顺序、最终选择和逐页判分；`costs.json` 只保存调用类型、公开主题标签、用量与费用，不保存请求头、密钥、账号或 API 原始回复。离线复算只需要这些证据及 `labels.jsonl`。

## 显式重跑（会调用用户 API，不能作为日常测试）

先按 420 任务把实验依赖安装进指定临时 deps 目录，不安装到共享 venv。实际临时覆盖包精确版本见 `requirements-experiment420.txt`；它继承基线构建环境，不是完整产品锁文件。依赖解析受 `scripts/release/requirements-build.txt` 约束，没有升级 lxml 或其他原有锁定版本。现有共享 venv 的 packaging 实装 26.2，构建文件锁 26.3，因此只在临时覆盖目录补 26.3；共享 venv 保持原样。以下 work 必须是新目录；程序不支持断点续跑，并拒绝覆盖已有 costs/runs 账本。一次实验预算 ¥10，已花费用来自 costs.json，预算不包含网络/机器电费。按任务约束，不在 10 主题全过前安装产品依赖、修改产品或打包。

```powershell
& D:\MentorFlow\.venv\Scripts\python.exe -B -m pip install --no-deps --no-cache-dir --target D:\MentorFlow\.local\test-tmp\420-MF07\deps -r tests/refactor/course/search_eval/requirements-experiment420.txt
& D:\MentorFlow\.venv\Scripts\python.exe -B tests/refactor/course/search_eval/run420.py --work D:\MentorFlow\.local\test-tmp\420-MF07\experiment --deps D:\MentorFlow\.local\test-tmp\420-MF07\deps --budget 10
```

`check_runner420.py` 是假 HTTP 的 URL/正文/费用边界小测试；它只需临时 deps 和指定 work，不读取真实凭据。`check_summary420.py` 用合成数据验证完整覆盖、标注和门槛，须传本任务临时 `--work`。三份检查均不联网。
