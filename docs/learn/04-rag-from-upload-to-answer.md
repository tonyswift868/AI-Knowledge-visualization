# 资料问答 RAG：从上传到回答

事实核验基线：`e61fd00d`。

本章按“资料室的一次跑腿”来学：先把讲义收进来，再整理入架，接着替读者找相关卡片，最后把原文交给答题者并带回可核对的出处。面试频率是本稿的学习优先级判断，不是统计调查；下面只标“常被问、偶尔问、基本不问”。

## 1. 检索增强生成：先找资料，再组织回答｜常被问

先查资料，再由模型回答。RAG 可以理解成给回答加一道“先查资料”的工序：系统从你选定的材料里找相关段落，把它们和问题一起交给语言模型组织答案。[R01] **（一句话层）**

在 MentorFlow 里，客户端把问题送到 Java 服务找资料，再由客户端把找回的段落连同问题交给模型网关；Java 服务负责查找，不负责生成最终回答。[R02] 这能让模型回答时多一份可检查的依据，也可能减少凭空发挥；但“给了资料”不等于“答对了”。检索到的段落可能不够，模型也可能读错或答偏。[R03] **（三句话层）**

再往里看，系统会把文字变成一串数字，叫 embedding（嵌入向量），这样问题和资料段落就能按语义接近程度比较。MentorFlow 用 LangChain4j 提供的量化 BGE 中文小模型，在 Java 进程内算出 512 维向量；模型第一次需要时才加载，运行时不必调用云端 embedding 服务。[R04] **（深一层）**

把自己想成资料室的跑腿编辑：你先按读者的问题找几张相关卡片，再把卡片递给答题者。这个场景能说明“检索在前、生成在后”，但系统不是按书名或页标题精准翻找；向量只是数字表示之间的接近程度，后面还会做关键词重排，找来的内容也未必充分。[R05]

**你可以这样跟面试官说：**“RAG 先检索原文，再把原文和问题交给模型。Java 做检索，客户端调用模型生成回答；多了依据仍不保证回答正确，最后还得核对原文。”

复述这一步时，先抓住这些原文关系：

RAG 先找相关资料段落，再把段落放进问题上下文供模型回答。 MentorFlow 的客户端把问题和检索段落交给模型网关生成回答，Java 服务负责检索。 检索增加了回答依据，可能减少幻觉，但不能保证正确。

### RAG 是什么？它为什么可能减少幻觉？

- RAG 先找相关资料段落，再把段落放进问题上下文供模型回答。[R01]
- MentorFlow 的客户端把问题和检索段落交给模型网关生成回答，Java 服务负责检索。[R02]
- 检索增加了回答依据，可能减少幻觉，但不能保证正确。[R03]

### 学习路线：本节出处

| 编号 | 具体说法 | 出处 |
|---|---|---|
| R01 | RAG 把检索到的相关信息注入提示上下文 | [LangChain4j RAG](https://docs.langchain4j.dev/tutorials/rag/) |
| R02 | 客户端发起检索并组装模型请求；Java 控制器提供检索入口 | `refactor/learning/app/rag.py:639-700`；`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagController.java:37-40` |
| R03 | RAG 的目标是改善依据质量，不保证模型回答正确 | [LangChain4j RAG](https://docs.langchain4j.dev/tutorials/rag/) |
| R04 | embedding 把文本变成向量以便比较；项目使用量化 BGE small zh v1.5、512 维向量和进程内按需加载 | [LangChain4j RAG](https://docs.langchain4j.dev/tutorials/rag/)；`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagEmbedding.java:8-36`；`refactor/server-java/RAG_API.md:62-68` |
| R05 | 服务先取检索候选并重排；客户端显示返回原文引用 | `refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:195-229`；`refactor/learning/web/src/pages/Rag.tsx:283-323` |

## 2. 上传与入库：本机抽文，分批入库｜常被问

本机抽文，文字分批入库。用户选中文件后，客户端在本机抽取文字，切成带页码或章节信息的文本块，再把这些文字块分批送到 Java 服务；原始文件不上传。[R06] **（一句话层）**

支持的格式包括 PDF、DOCX、PPTX、EPUB、XLSX、Markdown、TXT、CSV、JSON 和 HTML。抽出的块与问答会话保存在本机 SQLite。[R07] 分批送达时，每块带从 0 开始的序号；服务端收齐一份资料的全部连续编号后，才把状态标成“可检索”。[R08] **（三句话层）**

同一账号里，正文相同的完整资料可以按 SHA-256 去重。[R08] 上传需要已登录的人类用户账号。服务端按身份签发方和该用户的唯一标识确定账号范围，因此不同学习计划可以共用同一账号的资料。[R09] **（深一层）**

想象你是资料室的收件编辑：先把讲义复印成带页码的小卡片，分几趟送去；编号齐全后才贴上“可查”标签。这个比喻只说明抽文字、分块、收齐的顺序。实际服务还会校验登录、处理重传和去重，也会生成向量；它不是一个所有人都能翻的公共文件柜。[R10]

**你可以这样跟面试官说：**“原文件留在本机，客户端抽取并分块，只把文字分批交给 Java。服务确认块编号齐全后才允许检索，还会按账号隔离和按正文去重。”

复述这一步时，先抓住这些原文关系：

客户端在本机抽取文件文字并保存分块。 客户端把抽取后的文字块分批上传给 Java 服务。 界面明确说明原文件不会上传，上传的是文字分块。

### 上传文件时，原文件会到服务器吗？

- 客户端在本机抽取文件文字并保存分块。[R06]
- 客户端把抽取后的文字块分批上传给 Java 服务。[R06]
- 界面明确说明原文件不会上传，上传的是文字分块。[R11]

### 学习路线：本节出处

| 编号 | 具体说法 | 出处 |
|---|---|---|
| R06 | 文件在客户端抽取、分块，并分批上传文字 | `refactor/learning/app/rag.py:474-528,569-615`；`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:35-49` |
| R07 | 支持格式列表；文本块和问答会话保存在本机 SQLite | `refactor/learning/app/rag.py:102-143,235-257,280-281,569-615` |
| R08 | 序号从 0 开始；完整连续块变为 ready；完整资料按正文 SHA-256 去重 | `refactor/server-java/RAG_API.md:20-22,31`；`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:91-116,148-167` |
| R09 | 上传要求已登录的人类用户；按身份签发方与用户标识确定账号范围，计划之间可复用 | `refactor/learning/app/rag.py:68-99`；`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagController.java:14-26,41-46`；`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:332-342`；`refactor/server-java/RAG_API.md:7` |
| R10 | 客户端本地抽取和保存、服务端完整性与去重处理 | `refactor/learning/app/rag.py:474-528,569-600`；`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:52-116` |
| R11 | 用户界面说明仅上传抽取文字，不上传原文件 | `refactor/learning/web/src/pages/Rag.tsx:221-234` |

## 3. 检索：候选、重排与返回｜常被问

读者提问时，Java 服务把问题转成向量，在用户明确选中的资料里找语义接近的段落，再结合关键词重排，最后返回前 k 段。[R12] **（一句话层）**

top-k 的意思是“最多取排在前面的 k 段”；本项目客户端实际传入 k=8，服务 API 默认 k=5、允许的最大值是 100。[R13] pgvector 先按余弦距离找一批向量候选，之后服务把归一化余弦相似度和字符二元组关键词重合度按 80:20 混合排序。[R14] pgvector 是 PostgreSQL 的向量扩展，项目使用 512 维向量和 HNSW 余弦索引。[R14] **（三句话层）**

HNSW 是近似近邻索引：它帮助更快找候选，但结果不保证和逐条精确比较完全相同。最终分数只表示这一轮候选的相对排序，不是“答对概率”；服务返回的是数据库里的原文段落。[R15] **（深一层）**

在资料室里，这像先按“意思相近”挑一摞卡片，再看问题和卡片共享哪些词，重新排队后只递上前几张。这个场景解释了两轮排序和 top-k；它不表示分数等于正确率，也不表示近似索引永远不会漏掉相关内容。[R16]

**你可以这样跟面试官说：**“系统先用向量距离找候选，再结合关键词重排，最终返回前 k 段。客户端传 k=8，服务默认5、最大100；这些是数量和排序规则，不是正确率。”

复述这一步时，先抓住这些原文关系：

embedding 是把文本转换成数字向量，供系统比较问题与资料的语义距离。 MentorFlow 使用 BGE small zh v1.5 生成 512 维向量。 Java 进程第一次需要模型时才加载它。

不是；服务先找候选、再重排，最终最多返回 k 段。 客户端提问时传入 k=8。 服务默认 k=5，允许的最大值为 100。

pgvector 先按余弦距离找一批语义候选。 服务再按语义相似度与字符二元组关键词重合度混合排序。 返回前 k 段；分数只表示本次排序，不表示答案正确率。

### embedding 是什么？

- embedding 是把文本转换成数字向量，供系统比较问题与资料的语义距离。[R04]
- MentorFlow 使用 BGE small zh v1.5 生成 512 维向量。[R04]
- Java 进程第一次需要模型时才加载它。[R04]

### top-k=8 的意思是系统只查八段吗？

- 不是；服务先找候选、再重排，最终最多返回 k 段。[R14]
- 客户端提问时传入 k=8。[R13]
- 服务默认 k=5，允许的最大值为 100。[R13]

### 向量检索和关键词检索怎么配合？

- pgvector 先按余弦距离找一批语义候选。[R14]
- 服务再按语义相似度与字符二元组关键词重合度混合排序。[R14]
- 返回前 k 段；分数只表示本次排序，不表示答案正确率。[R15]

### 学习路线：本节出处

| 编号 | 具体说法 | 出处 |
|---|---|---|
| R12 | 问题向量检索选中文档，并经混合排序返回结果 | `refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:195-229` |
| R13 | 服务默认 k=5、最大 100；客户端实际传 k=8 | `refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:22-24,195-200`；`refactor/learning/app/rag.py:648-651` |
| R14 | pgvector 余弦距离候选、80:20 混合排序、512 维向量与 HNSW 索引 | `refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:203-229,380-394`；`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/PgVectorStore.java:43-53`；`refactor/server-java/RAG_API.md:64`；[pgvector](https://github.com/pgvector/pgvector) |
| R15 | HNSW 属近似检索；分数是相对排序，返回数据库原文 | `refactor/server-java/RAG_API.md:56,64`；`refactor/learning/app/rag.py:652-681`；[pgvector HNSW](https://github.com/pgvector/pgvector) |
| R16 | 服务先取向量候选并以关键词重排，近似结果与精确结果可能不同 | `refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:212-229,380-394`；`refactor/server-java/RAG_API.md:56,64` |

## 4. 生成回答、引用与校验边界｜常被问

带回引用，核对原文再信。客户端把问题和检索段落交给模型网关；模型回答里可以用编号标记段落，用户点编号就能查看相应原文、页码和章节。没有找到段落时，客户端会告诉模型当前没有检索结果。[R17] **（一句话层）**

提示词要求回答事实只依据问题和提供的段落，资料没有答案就明确说；如补充常识，必须标成“资料外补充”。回答、引用段落与问答会话保存在本机 SQLite。[R18] 系统没有对生成答案做语义正确性验证。[R20] **（三句话层）**

资料按已验证账号隔离。数据库的行级安全策略（RLS）会把每行的账号范围与当前事务的账号范围对照，只允许匹配的资料行通过；迁移为三张 RAG 表启用并强制执行这类策略。RAG 当前仍标记为待用户亲测，因此不能把代码实现说成用户已经验收。[R19] **（深一层）**

资料室的答题者交卷时，会在答案旁标出卡片编号；读者点编号能检查原文。这解释了引用的用途，却不能证明答案每句话都被原文支持：编号由模型按提示生成，提示不能保证它每次都标对；显示原文也不等于完成了答案正确性验证。[R20]

**你可以这样跟面试官说：**“客户端把检索段落交给模型，回答中的引用能点开看原文。提示词要求有依据地答，但系统没有替用户证明每句话都正确，所以引用要核对。”

复述这一步时，先抓住这些原文关系：

RAG 会把找到的资料段落放入上下文，让模型据此回答。 项目提示模型资料没有答案时要说明，并要求引用编号对应已提供的段落。 用户可以核对检索原文，但最终回答没有经过语义正确性验证。

### 为什么不能说这个 RAG 一定不会幻觉？

- RAG 会把找到的资料段落放入上下文，让模型据此回答。[R01]
- 项目提示模型资料没有答案时要说明，并要求引用编号对应已提供的段落。[R18]
- 用户可以核对检索原文，但最终回答没有经过语义正确性验证。[R20]

### 学习路线：本节出处

| 编号 | 具体说法 | 出处 |
|---|---|---|
| R17 | 客户端组装模型请求，并提供可点击引用和无检索结果提示 | `refactor/learning/app/rag.py:672-700`；`refactor/learning/web/src/pages/Rag.tsx:283-323,326-352` |
| R18 | 提示词规定依据、引用、无答案和资料外补充；会话保存在本机 | `refactor/learning/app/rag.py:34-38,102-143,235-257` |
| R19 | RAG 服务给事务设置账号范围；迁移对三张表启用并强制应用按账号范围匹配的 RLS 策略；项目记录用户验收仍待完成 | `refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagController.java:41-46`；`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagRepository.java:63-98`；`refactor/server/migrations/versions/0044_rag_documents.py:142-146`；[RAG-01]；[PostgreSQL 17 RLS](https://www.postgresql.org/docs/17/ddl-rowsecurity.html) |
| R20 | 提示约束回答和引用方式，但最终回答无语义正确性校验 | `refactor/learning/app/rag.py:34-38,652-700`；`refactor/learning/web/src/pages/Rag.tsx:283-323` |

## 学习路线：不用背清单｜基本不问

记住四步就够：本机抽文字，分块送入库，按问题检索原文，模型组织回答并附引用。HNSW 的图结构参数、接口请求上限、数据库字段和迁移 SQL 都不必背；知道近似检索可能漏项、分数不是正确率、引用仍需核原文即可。[R08][R15][R19]
