# 资料问答 RAG：从上传到回答

## 1. 检索增强生成：把资料放进回答上下文


RAG（检索增强生成）可以先从你选定的资料里找出相关段落，再把问题和段落交给语言模型组织回答；LangChain4j 官方把它概括为把相关信息检索出来并注入提示词。[概念出处：LangChain4j《RAG》](https://docs.langchain4j.dev/tutorials/rag/) **（一句话层）**


普通聊天主要靠模型已有的训练知识回答，RAG 则在回答前增加一次针对资料的检索。[概念出处：LangChain4j《RAG》](https://docs.langchain4j.dev/tutorials/rag/) MentorFlow 的资料问答由客户端把问题发给 Java 服务检索，客户端再把检索段落和问题交给模型网关；Java 服务本身负责检索，不负责生成最终回答。[客户端调用与组装：`refactor/learning/app/rag.py:639-700`；服务检索入口：`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagController.java:37-40`；基线 `958e7668`]。“增强”不是保证正确，而是给模型更多可核对的上下文；官方说明这种方式是为了降低幻觉概率，不能把它当作保证。[概念出处：LangChain4j《RAG》](https://docs.langchain4j.dev/tutorials/rag/) **（三句话层）**


这里的 embedding（嵌入向量）是把文本变成一串数字，让系统可以按语义距离比较问题和资料段落；LangChain4j 将向量检索描述为先把文本变成向量，再按余弦相似度或其他距离排序。[概念出处：LangChain4j《RAG》](https://docs.langchain4j.dev/tutorials/rag/) MentorFlow 用 LangChain4j 的量化 BGE small zh v1.5 模型，在 Java 进程里计算 512 维向量，模型第一次被调用时才加载。[实现：`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagEmbedding.java:8-36`；依赖与模型说明：`refactor/server-java/RAG_API.md:68`；基线 `958e7668`] 这不是 Java 调用云端 embedding API；项目资料说明该模型随 Maven 模块提供，运行时不需访问大模型服务。[`refactor/server-java/RAG_API.md:62-68`；基线 `958e7668`] **（深一层）**

**比喻**
像开卷考试：先从指定课本里翻出相关页，再把题目和这些页交给会写答案的人整理。课本对应被选中的资料，翻页对应检索，整理答案对应语言模型生成。这个比喻不成立的地方是：计算机不是按书页标题准确翻找，向量检索只是在计算表示之间的接近程度，本实现之后还做关键词重排；找到的段落可能不充分，模型也可能误读，所以答案仍需看引用原文。[检索机制：`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:195-229`；客户端引用展示：`refactor/learning/web/src/pages/Rag.tsx:283-323`；基线 `958e7668`]

**面试频率分级：常被问（判断；只表示本稿优先级，不是统计）**。这是 Java + AI 副项目的主链路概念，能解释“怎么回答资料问题”比记实现类名更值得先掌握；本标注是学习优先级判断，没有用外部面经统计支持。

### RAG 是什么？它为什么可能减少幻觉？
- 参考要点：RAG 在生成回答前先检索相关资料段落，再把段落放进问题上下文里让模型回答。[概念出处：LangChain4j《RAG》](https://docs.langchain4j.dev/tutorials/rag/)
- 参考要点：MentorFlow 的客户端把问题和检索段落交给模型网关生成回答，Java 服务负责资料检索。[`refactor/learning/app/rag.py:639-700`；`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagController.java:37-40`；基线 `958e7668`]
- 参考要点：检索提供额外上下文，可能降低幻觉概率，但不保证正确。[概念出处：LangChain4j《RAG》](https://docs.langchain4j.dev/tutorials/rag/)

## 2. 上传与入库：本机抽文字，服务器分块存储


用户选文件后，客户端在本机抽取文字、保存文字块，再把文字块分批上传给 Java 服务；原始文件不上传。[文件抽取与本地保存：`refactor/learning/app/rag.py:569-600`；界面提示：`refactor/learning/web/src/pages/Rag.tsx:221-234`；服务接收分块：`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:35-49`；基线 `958e7668`] **（一句话层）**


客户端支持 PDF、DOCX、PPTX、EPUB、XLSX、Markdown、TXT、CSV、JSON、HTML 等文件，并通过 `extract_file` 抽取文本和页码、章节信息。[`refactor/learning/app/rag.py:569-615`；基线 `958e7668`] 文本块先保存在用户本机的 `rag.sqlite3`，文档问答会话也存本机 SQLite。[`refactor/learning/app/rag.py:102-143`、`refactor/learning/app/rag.py:280-281`；基线 `958e7668`] 入库请求通过已登录账户的传输层访问 `/api/enterprise/rag/v1`，服务端只接受经过认证的 HUMAN 账户上下文。[`refactor/learning/app/rag.py:68-99`、`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagController.java:14-26,41-46`；基线 `958e7668`] **（三句话层）**


分块的 `seq` 从 0 开始，客户端为了适应请求体限制可分批重传；服务端只有在收到从 0 到 `total_chunks - 1` 的完整连续集合后才把文档状态设为 `ready`。[协议说明：`refactor/server-java/RAG_API.md:13-31`；完成条件：`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:91-116`；基线 `958e7668`] 服务端单次 JSON 请求体上限为 9 MiB，超限返回 413。[`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagController.java:18,49-62`；基线 `958e7668`] 同一账户内，完整文档会按分块正文计算 SHA-256；相同正文可复用已有内容并保留请求 ID 别名。[`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:98-116,148-167`；基线 `958e7668`] **（深一层）**

**比喻**
像把一份讲义在本地复印成带页码的小卡片，再分几次送到资料室；资料室确认卡片编号齐全后，才把整份讲义标成可查。卡片对应文本块，编号对应 `seq`，收齐对应 `ready`。这个比喻不成立的地方是：真实系统还会算向量、校验账户并处理重试和去重；它不是简单把文件放进一个公共文件柜。[本地文字块及上传：`refactor/learning/app/rag.py:474-528,569-600`；完整性与去重：`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:52-116`；基线 `958e7668`]

**面试频率分级：常被问（判断；只表示本稿优先级，不是统计）**。数据从哪里来、是否上传原文件、怎样处理大文件，是讲清系统边界和隐私边界的直接问题；本标注是判断，不代表面经统计结果。

### 上传文件时，原文件会到服务器吗？
- 参考要点：客户端在本机调用 `extract_file` 抽取文件文字并保存分块。[`refactor/learning/app/rag.py:569-600`；基线 `958e7668`]
- 参考要点：客户端把文字块分批上传到 Java 服务。[`refactor/learning/app/rag.py:474-528`；基线 `958e7668`]
- 参考要点：界面说明原文件不会上传，上传的是抽取后的文字分块。[`refactor/learning/web/src/pages/Rag.tsx:221-234`；基线 `958e7668`]

## 3. 检索：embedding、top-k 与 pgvector


用户提问时，服务将问题转成向量，在用户选中的资料中找语义接近的段落，再混合关键词相似度排序，返回前 k 段。[`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:195-229`；基线 `958e7668`] **（一句话层）**


top-k 是“最多取排名靠前的 k 个结果”这个参数名；在本项目中 API 默认 k=5、最大 100，客户端提问时实际传 `k=8`。[服务默认值和上限：`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:22-24,195-200,221-229`；客户端参数：`refactor/learning/app/rag.py:648-651`；基线 `958e7668`] 服务先让 pgvector 按余弦距离找较大的候选集合，再以 80% 归一化余弦相似度与 20% 字符二元组 Jaccard 重排，最终只返回前 k 段。[`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:203-229,380-394`；`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/PgVectorStore.java:43-53`；基线 `958e7668`] pgvector 是 PostgreSQL 的向量扩展，可存向量并按余弦距离等运算符找近邻；项目表使用 512 维向量和 HNSW 余弦索引。[官方说明：pgvector README](https://github.com/pgvector/pgvector)；项目说明：`refactor/server-java/RAG_API.md:64`；基线 `958e7668`] **（三句话层）**


HNSW 是一种近似最近邻索引，通常用速度换取部分召回率；pgvector 官方说明近似索引可能和精确检索给出不同结果。[官方说明：pgvector README 的 Indexing/HNSW](https://github.com/pgvector/pgvector) 项目文档明确指出 HNSW 近似检索不保证和全量精确检索完全一致，过滤账户和所选资料时配置 iterative scan 以减少过滤后结果不足。[`refactor/server-java/RAG_API.md:56,64`；基线 `958e7668`] 因此检索分数只是这一轮候选的相对排序依据，不是答案正确率；服务返回数据库原文，客户端据此构造提示词并给引用编号。[`refactor/server-java/RAG_API.md:56`；`refactor/learning/app/rag.py:652-681`；基线 `958e7668`] **（深一层）**

**比喻**
像先用“意思相近”找出一摞可能有用的卡片，再用卡片和问题共享的关键词重新排队，最后只递给答题者前几张。向量近邻对应第一轮候选，关键词重排对应第二轮评分，top-k 对应最后递出的数量。这个比喻不成立的地方是，排序分数不是“答对概率”，而且近似索引可能漏掉某些本来相关的卡片。[实现与相对分数说明：`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:212-229`、`refactor/server-java/RAG_API.md:56,64`；基线 `958e7668`]

**面试频率分级：常被问（判断；只表示本稿优先级，不是统计）**。RAG 面试讲解中，“怎么检索、top-k 是什么、为什么用向量库”能展示候选召回与答案生成的边界；本标注是判断，没有外部面经统计支持。

### embedding 是什么？
- 参考要点：embedding 是模型把文本转换成数字向量的表示方式，系统比较问题向量和资料向量的距离来做语义检索。[概念出处：LangChain4j《RAG》](https://docs.langchain4j.dev/tutorials/rag/)
- 参考要点：MentorFlow 使用 BGE small zh v1.5 模型生成 512 维向量。[`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagEmbedding.java:19-23`；`refactor/server-java/RAG_API.md:68`；基线 `958e7668`]
- 参考要点：Java 进程首次调用时才加载这个模型。[`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagEmbedding.java:8-10,25-36`；基线 `958e7668`]

### top-k=8 的意思是系统只查八段吗？
- 参考要点：不是；服务先取一批向量候选，再进行混合重排，最后最多返回 k 段。[`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:212-229`；基线 `958e7668`]
- 参考要点：客户端提问时实际传入 `k=8`。[`refactor/learning/app/rag.py:648-651`；基线 `958e7668`]
- 参考要点：服务端 API 默认 k 为 5、允许的最大值为 100。[`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:22-24,195-200`；基线 `958e7668`]

### 向量检索和关键词检索怎么配合？
- 参考要点：服务先按 pgvector 余弦距离取向量候选。[`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/PgVectorStore.java:43-53`；基线 `958e7668`]
- 参考要点：候选再按归一化余弦相似度与字符二元组 Jaccard 按 80:20 混合排序。[`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:212-229,380-394`；基线 `958e7668`]
- 参考要点：最终返回前 k 段，分数用于本次检索的相对排序，不代表正确率。[`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:221-229`；`refactor/server-java/RAG_API.md:56`；基线 `958e7668`]

## 4. 生成回答、引用与已知边界


客户端把问题和检索段落交给模型网关生成回答，并把段落编号映射成可点击引用；没有检索到段落时，提示词会明确带上“没有检索到相关段落”。[`refactor/learning/app/rag.py:672-700`；`refactor/learning/web/src/pages/Rag.tsx:283-323,326-352`；基线 `958e7668`] **（一句话层）**


提示词要求模型只依据问题和提供的段落回答事实，引用使用 `[1]` 这类编号，资料没有答案时明确说明；资料外常识须用“资料外补充：”标出。[`refactor/learning/app/rag.py:34-38`] 回答、引用段落和会话保存在本机 `rag.sqlite3`，问答会话不进入课程同步日志。[`refactor/learning/app/rag.py:1-6,102-143,235-257`] 用户可点击引用查看对应原文块、页码和章节信息。[`refactor/learning/web/src/pages/Rag.tsx:283-323,326-352`] **（三句话层）**


RAG 服务按账户作用域存资料；Java 控制器从已验证的账户上下文取得 HUMAN 身份，数据库仓储在事务中设置账户作用域，并使用 PostgreSQL 行级安全策略保护 RAG 表。[`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagController.java:41-46`；`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagRepository.java:63-98`；`refactor/server-java/RAG_API.md:7`；基线 `958e7668`] PostgreSQL 官方说明，行级安全策略可以按用户限制哪些行可读写；具体是否生效仍取决于表策略与数据库角色配置。[官方说明：PostgreSQL 17 Row Security](https://www.postgresql.org/docs/17/ddl-rowsecurity.html) 项目状态记录显示，RAG 表迁移曾因迁移角色缺少 `account_closure` 模式的 `CREATE` 权限而失败；修复后在 2026-10-17 重跑迁移成功，三张 RAG 表启用并强制 RLS。[`docs/project/CURRENT_STATE.md:1101-1109`（BUG-107）；`docs/project/CURRENT_STATE.md:595-606`（RAG-01）；基线 `958e7668`] 当前样章基线下，资料问答客户端随 2.0.21 发布、服务器迁移就绪，但 CURRENT_STATE 仍写着“待用户亲测”，所以不能把功能描述成已由用户验收。[`docs/project/CURRENT_STATE.md:13,595-606`；基线 `958e7668`] **（深一层）**

**比喻**
像答题者交卷时在每条答案旁标上讲义页码，阅卷人可以点页码检查原文。引用编号对应返回段落，原文面板对应可复核材料。这个比喻不成立的地方是：页码标记由模型按提示生成，提示本身不能保证模型每次都引用正确；界面能显示被检索段落，但不能证明整段回答都被它充分支持。[提示约束：`refactor/learning/app/rag.py:34-38`；引用展示：`refactor/learning/web/src/pages/Rag.tsx:283-323`；基线 `958e7668`]

**面试频率分级：偶尔问（判断；只表示本稿优先级，不是统计）**。本章把账户隔离、检索失败和上线状态列为可选深入题；这是学习路径判断，没有证据说明副项目固定问到哪一层。

### 为什么不能说这个 RAG 一定不会幻觉？
- 参考要点：RAG 把检索到的相关原文放入上下文，目的是让模型使用这些资料回答。[概念出处：LangChain4j《RAG》](https://docs.langchain4j.dev/tutorials/rag/)
- 参考要点：项目提示模型在资料没有答案时明确说明，并要求引用编号对应提供的段落。[`refactor/learning/app/rag.py:34-38`；基线 `958e7668`]
- 参考要点：检索结果和引用可供用户核对；这个流程发送检索上下文并保存模型回答，代码中没有对最终回答做语义正确性验证。[`refactor/learning/app/rag.py:652-700`；`refactor/learning/web/src/pages/Rag.tsx:283-323`；基线 `958e7668`]

## 学习路线：不用背清单
知道 HNSW 是近似索引即可，不必背图结构参数；项目也明确承认近似检索不等同精确检索。[官方说明：pgvector README](https://github.com/pgvector/pgvector)；项目说明：`refactor/server-java/RAG_API.md:64`；基线 `958e7668`] 上传接口有请求体和分块数量限制，知道“存在上限，具体数值和字段可查实现”即可，不用背数值或 SQL 列名。[`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagController.java:18,49-62`；`refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java:22-24`；基线 `958e7668`] 记住 RLS 用来限制账户行访问，并能讲清 BUG-107 的原因与结果即可，不必背迁移脚本的每条 SQL。[PostgreSQL 官方说明](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)；`docs/project/CURRENT_STATE.md:1101-1109`（BUG-107）；基线 `958e7668`]
