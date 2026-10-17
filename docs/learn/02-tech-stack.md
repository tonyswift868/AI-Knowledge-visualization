# 技术栈问答：从桌面窗口一路讲到更新

这份卡片回答四件事：技术是什么、项目为什么用它、它具体在哪、面试官可能再问什么。**“为什么用”只写仓库和状态文档能证明的理由；没有选型记录就明说“未发现比较证据”。**不要把个人推测讲成团队决策。

代码行号和状态证据以仓库 `D:/MentorFlow-office/library/repo` 的基线 `958e7668f642eb19d7eb717be582c88ceff7af40` 为准；状态文档是 [`docs/project/CURRENT_STATE.md`](../project/CURRENT_STATE.md)。

频率是复习优先级，不是统计结论。这里把 JavaGuide 的 Java、数据库、框架、系统设计与 AI/RAG 主题作为分类参照；它不是 MentorFlow 面试题的频率调查。标“判断”的都是按 Java + AI 副项目场景给出的判断。[JavaGuide](https://github.com/Snailclimb/JavaGuide)

## 先用一段话讲完整

**一句话：** MentorFlow 是一款 Python 桌面壳承载 React 学习界面的 AI 学习软件；桌面负责学习流程、用户本地数据和用户自己的模型 Key，Java 服务器负责账号、同步和资料检索，PostgreSQL 保存服务器数据。[CURRENT_STATE.md:9,227,285,600-604]

**三句话：** PySide6 的 Qt WebEngine 显示 React 页面，前端通过 QWebChannel 和 Python 宿主通信。Python 仍负责客户端学习业务和模型调用；服务器的 FastAPI 后端已迁移为 Java 21 + Spring Boot，Keycloak 管身份，PostgreSQL 管服务器数据。资料问答在 Java 服务里用 LangChain4j 和中文向量模型找资料片段，片段存在 pgvector，模型回答仍由客户端直连用户选择的模型服务。[ARCH-01,ARCH-09,RAG-01]

**深一层：** 这是两种运行环境并存的架构：客户端主流程以 Python 为中心；服务器当前在线的是 Java，旧 Python 服务停流量并留作回退。迁移目标有用户明确的求职背景（Java + AI），同时要求尽量保持客户端 HTTP 契约不变。资料问答只把文字传服务器，原始文件不上传；服务器返回原文片段，客户端用自己的模型 Key 生成答案。[CURRENT_STATE.md:285-306,595-606]

## 1. 桌面窗口和网页界面：PySide6、Qt WebEngine、React、Vite

### PySide6 与 Qt WebEngine

- **一句话：** PySide6 是 Qt 的 Python 绑定，Qt WebEngine 则让桌面窗口里显示一个网页视图。[Qt for Python 文档](https://doc.qt.io/qtforpython-6/)
- **三句话：** MentorFlow 外层是 Windows 桌面程序，里面的学习主界面由 React 绘制。Qt WebEngine 把本地构建好的网页显示在 Qt 窗口中；QWebChannel 把页面请求交给 Python 宿主处理。仓库描述了这种实现，没有找到与其他桌面框架的选型比较。[`window.py:270-300`](../../refactor/learning/app/window.py#L270)，[`bridge.py:1-3,73`](../../refactor/learning/app/bridge.py#L1)，[CURRENT_STATE.md:227-228]
- **深一层：** WebEngine 页面不是普通浏览器标签：代码创建 `QWebEngineProfile`，把 HTTP 缓存设为内存、不持久化 Cookie，同时为网页 storage 和 cache 指定应用数据目录，并用拦截器限制加载来源；QWebChannel 注册名为 `mf` 的宿主对象供页面调用。[`window.py:270-300`](../../refactor/learning/app/window.py#L270)
- **比喻：** Qt 窗口像教室的房间，WebEngine 是房间里的电子白板，React 负责白板画面，Python 宿主像控制台。对应关系是窗口容纳视图、视图呈现页面、页面通过桥发起宿主请求；比喻不表示它们是独立进程，也不表示网页可以不经宿主直接改本地数据库。
- **频率：偶尔问（判断）**——JavaGuide 的核心主题不是桌面 Qt；但讲项目架构时，面试官可能问“为什么桌面软件里有网页”和“前后端怎么通信”。
- **追问：** React 页面怎样调用 Python？答：通过 QWebChannel 注册的 `mf` 对象，按 `{id, method, params}` 发请求，由宿主注册处理器并返回响应或事件。[`bridge.py:1-3`](../../refactor/learning/app/bridge.py#L1)

### React 与 Vite

- **一句话：** React 用组件和状态描述界面；Vite 负责本地开发服务和构建前端资源。[React 文档](https://react.dev/learn)，[Vite 文档](https://vite.dev/guide/)
- **三句话：** 当前依赖锁定 React 19.2.7、Vite 8.1.4。React 页面代码在 `refactor/learning/web`，构建结果再由桌面程序本地加载。记录能证明项目用了这套工具，但没有找到选择 React/Vite 时和其他前端方案的比较。[`package.json:6-8,21-22`](../../refactor/learning/web/package.json#L6)，[CURRENT_STATE.md:227]
- **深一层：** 前端只是界面层；计划和课堂的业务编排在 Python `app/`，学习领域的数据和规则在 `course/`。这层次能解释为什么桌面页面没有直接承担学习账本或模型决策。[CURRENT_STATE.md:233-235]
- **比喻：** React 像一块由小部件组成、会随状态更新的白板；Vite 像白板的工作台和打包机。比喻不说明 React 会替你设计业务，也不说明 Vite 是运行时后端。
- **频率：偶尔问（判断）**——JavaGuide 的 Java 后端主线不覆盖 React/Vite；本项目 UI 架构特殊，准备一两句就够。
- **追问：** React 与 Python 怎么分工？答：React 负责显示和交互，Python 宿主负责业务处理与本地状态写入；通过桥传递请求和事件。

## 2. 后端演进：FastAPI 到 Java 21 + Spring Boot，身份交给 Keycloak

### Python 与 FastAPI（旧服务）

- **一句话：** FastAPI 是 Python 的 Web API 框架；它曾经承载 MentorFlow 服务器接口，现在旧服务停流量，保留作回退。[FastAPI 项目入口](../../refactor/server/app.py#L97)，[CURRENT_STATE.md:285,290]
- **三句话：** 旧服务器接口入口是 `refactor/server/app.py`，规模和接口范围见 ARCH-09。改成 Java 的用户决策背景是求职方向 Java + AI，AI 提示词和客户端编排继续留在 Python；不是因为有证据证明 FastAPI 不好。没有找到 FastAPI 与其他 Python 框架的选择比较。[CURRENT_STATE.md:286-306]
- **深一层：** 面试里要区分“客户端 Python 仍在用”和“旧 Python 服务器停流量”：前者是产品架构的一部分，后者是服务器回退实现。现有记录说 Python 镜像和 Compose 定义保留，切回依赖部署目录的恢复脚本；不要说已经删掉。[CURRENT_STATE.md:285]
- **比喻：** FastAPI 是旧柜台的服务台规则，Java 是接手同一业务的新柜台。两边要遵守相同 HTTP 约定；这个比喻不表示两个服务同时承接线上流量。
- **频率：偶尔问（判断）**——JavaGuide 框架主题更偏 Java；面试官可能追问迁移前后如何兼容。不要花时间讲 FastAPI 内部细节。
- **追问：** 为什么不能直接删掉旧服务？答：项目记录把它保留作回退；是否退场还要满足回滚与数据库兼容条件，当前没有“可以删除”的证据。

### Java 21 与 Spring Boot

- **一句话：** Java 是服务器实现语言，Spring Boot 提供构建和运行 Java Web 服务的框架基础。[Spring Boot 文档](https://docs.spring.io/spring-boot/reference/)
- **三句话：** 当前服务器 Java 项目使用 Java 21、Spring Boot 3.5.16；代码在 `refactor/server-java`。用户定了把适合 Java 的服务器业务改成 Java，原因是求职方向 Java + AI；没有找到 Spring Boot 与其他 Java 框架的实测选型比较。[`pom.xml:4-15`](../../refactor/server-java/pom.xml#L4)，[CURRENT_STATE.md:285-306]
- **深一层：** Java 服务承接账号、同步、目录与 RAG 接口；当前生产记录是 Java 正式在线，Python 服务停流量作回退。此前切换时真实客户端遇到代理协议识别问题：Java 未还原 `X-Forwarded-Proto=https`，认证接口回 403；修复后，CURRENT_STATE 记录客户端自动登录、同步、上课恢复，20 分钟访问日志中的 refresh、session、me、workspaces、changes、bulk 都是 200。注册、找回、改密、改邮箱、注销的发邮件流程当时仍未在线真用。[CURRENT_STATE.md:285]
- **比喻：** Spring Boot 像服务器的骨架和接线板，具体业务仍由项目自己的 Java 类实现。它不等于数据库，也不自动证明业务正确；迁移兼容仍要靠契约核对和实际流量证据。
- **频率：常被问（判断；JavaGuide 有 Java、框架、后端系统设计主题）**。这是一道优先级判断，没有面试频率统计。[JavaGuide](https://github.com/Snailclimb/JavaGuide)
- **追问：** 迁移成功的证据是什么？答：切换前用相同 HTTP 契约比较两版；修复代理协议问题后，记录了真实客户端登录、同步、上课与 20 分钟接口日志；同时如实说邮件账号流程尚未在线验证。[CURRENT_STATE.md:285-306]

### Keycloak、OIDC 与 JWT

- **一句话：** Keycloak 是本项目的身份服务；OIDC 让应用用约定的协议取得身份信息，JWT 是一种装载声明并可签名验证的令牌格式。[Keycloak 文档](https://www.keycloak.org/documentation)，[RFC 7519](https://www.rfc-editor.org/rfc/rfc7519)
- **三句话：** Keycloak 在服务端和邮箱验证码流程中提供身份能力；Java API 使用 OAuth2 Resource Server 依赖处理认证入口。[`pom.xml:11-12`](../../refactor/server-java/pom.xml#L11)，[CURRENT_STATE.md:285,504-518]。Keycloak 为什么被选用、与其他身份服务比较过什么，未发现选型记录。
- **深一层：** JWT 是编码后的声明集合，编码本身不是加密；接收方要验证签名和声明，才能信任其来源及有效范围。还要注意，本项目桌面常用的 `mfn1_a_` 原生访问令牌是随机不透明令牌，由服务器会话校验，不是拿来按 JWT 解码的。[`java-backend-rewrite.md`](../interview/java-backend-rewrite.md)，[RFC 7519](https://www.rfc-editor.org/rfc/rfc7519)
- **比喻：** JWT 像盖过防伪章的通行证：能读到上面的声明，不代表这些字已经保密；验签用于确认章对不对。比喻不表示“有签名就加密”，也不表示所有令牌都是 JWT。
- **频率：常被问（判断；JavaGuide 的认证、Web、安全和后端主题可作分类参照）**。项目特定的 Keycloak 配置属于偶尔问；不是统计出来的频率。
- **追问：** JWT 签名和加密有什么区别？答：签名主要用于验证完整性和来源，JWT 内容通常可读取；需要保密还得用加密机制。要再区分 Keycloak JWT 与本项目原生不透明令牌。

## 3. 数据存储：SQLite 本地库、PostgreSQL、pgvector 与行级安全

### SQLite 与 WAL

- **一句话：** SQLite 是应用可直接使用的本地关系数据库；WAL 是 Write-Ahead Log（预写日志）模式，先把改动记入日志，再按检查点合并回主数据库文件。[SQLite WAL 文档](https://www.sqlite.org/wal.html)
- **三句话：** MentorFlow 用 SQLite 保存本地学习数据，也把资料问答会话留在本机。两处代码启用 WAL：课程数据库和 RAG 本地库。[`store.py:170-174`](../../refactor/learning/course/store.py#L170)，[`rag.py:102-112`](../../refactor/learning/app/rag.py#L102)。没有找到 SQLite 与其他本地数据库的选型比较。
- **深一层：** WAL 让读写可以采用日志文件配合主库的方式工作，但会带来 `-wal`、`-shm` 等伴随文件与 checkpoint 生命周期；不能把 WAL 简化成“没有锁”或“所有并发都自动安全”。[SQLite WAL 文档](https://www.sqlite.org/wal.html)
- **比喻：** 主数据库像正式账本，WAL 像先写下、稍后誊回账本的流水页。对应的是日志与 checkpoint；比喻不代表日志永不增长、也不代表直接复制单个数据库文件就一定是完整备份。
- **频率：偶尔问（判断；JavaGuide 有数据库主题，但桌面 SQLite/WAL 是项目细节）**。
- **追问：** WAL 就是多线程锁吗？答：不是；它是 SQLite 的日志模式，解决读写组织方式，事务与连接使用仍需正确管理。

### PostgreSQL 与 pgvector

- **一句话：** PostgreSQL 是服务器关系数据库；pgvector 是它的扩展，可把向量和业务数据放在同一数据库里并按向量距离检索。[PostgreSQL 文档](https://www.postgresql.org/docs/17/)，[pgvector 项目](https://github.com/pgvector/pgvector)
- **三句话：** 线上数据库是 PostgreSQL 17，RAG 使用 pgvector 0.8.7。项目把 512 维 embedding 存在 `vector(512)` 列，并用余弦距离检索；RAG 文档、片段和别名表属于服务器数据库。[`RAG_API.md:60-68`](../../refactor/server-java/RAG_API.md#L60)，[CURRENT_STATE.md:596,604-606]。用户明确决定安装 pgvector；没有找到和其他向量库的正式比较。[CURRENT_STATE.md:606]
- **深一层：** RAG 检索先按语义距离找候选，再把关键词重合并入排序；项目有 HNSW 余弦索引，但候选经过账号和所选文档过滤，且近似索引不保证结果等同于全量精确检索。一次 MDN 中文小样本的前 5 结果命中率是 20/20，记录明确说这是小样本，不能推广为普遍准确率。[`RAG_API.md:56,64`](../../refactor/server-java/RAG_API.md#L56)，[CURRENT_STATE.md:596]
- **比喻：** embedding 像把一段话放在语义地图上的坐标，pgvector 帮忙找附近的坐标。对应的是向量距离；比喻不表示向量是关键词、附近一定正确，也不表示数据库能知道回答是否可信。
- **频率：常被问（判断；JavaGuide 的数据库和 AI/RAG 主题都覆盖相关概念）**。pgvector 的具体操作符和 HNSW 参数属于偶尔问。
- **追问：** 向量相似度分数是答案正确率吗？答：不是；项目 API 文档把它定义为本次检索的相对排序分数，回答仍需看返回原文。[`RAG_API.md:56`](../../refactor/server-java/RAG_API.md#L56)

### RLS 行级安全

- **一句话：** PostgreSQL RLS（Row-Level Security）用策略限制某个数据库角色能看到或修改哪些行。[PostgreSQL 17 RLS 文档](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)
- **三句话：** RAG 表启用了 RLS 并设置 `FORCE`；项目把账号归属和所选文档范围用于检索隔离。[`0044_rag_documents.py:142-143`](../../refactor/server/migrations/versions/0044_rag_documents.py#L142)，[`RAG_API.md:5-10,56`](../../refactor/server-java/RAG_API.md#L5)，[CURRENT_STATE.md:596]
- **深一层：** 没有适用策略时，开启 RLS 后默认拒绝普通行访问；表所有者通常能绕过策略，`FORCE ROW LEVEL SECURITY` 可让表所有者也受策略约束。它不能替代普通 SQL 权限，也不会保护超级用户或 `BYPASSRLS` 角色。[PostgreSQL 17 RLS 文档](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)
- **比喻：** SQL 权限像是否能进档案室，RLS 像进门后每份档案上的可见名单。比喻不表示 RLS 能防住超级用户，也不表示策略写错仍然安全。
- **频率：偶尔问（判断；JavaGuide 数据库/安全主题可参照，具体 PostgreSQL RLS 深度属于项目细节）**。
- **追问：** 为什么还要 `FORCE`？答：PostgreSQL 默认让表所有者绕过 RLS；强制模式让所有者执行普通访问时也受策略约束。

## 4. AI 请求和 RAG：DeepSeek 直连、LangChain4j、bge-small-zh

### DeepSeek 直连

- **一句话：** DeepSeek 是项目当前采用的模型服务之一；桌面端用用户自己的 API Key 直接调用，MentorFlow 服务器不做代理转发。[DeepSeek API 文档](https://api-docs.deepseek.com/)，[CURRENT_STATE.md:600-604]
- **三句话：** 客户端 provider 注册默认包含 `deepseek-v4-flash`，连接传输层识别 DeepSeek 官方主机并调用对应 API。服务器的职责是检索资料原文；客户端把用户的 Key 用于模型问答。项目记录说用户定继续用 DeepSeek，未来可考虑更便宜的 GPT；未发现完整模型对比报告。[`registry.py:20-24`](../../refactor/learning/connections/providers/registry.py#L20)，[`transport.py:43-46`](../../refactor/learning/connections/transport.py#L43)，[CURRENT_STATE.md:439,600-604]
- **深一层：** “直连”说明请求从客户端发给模型服务，不代表 Key 明文存盘；项目把 Key 放在本机 Windows 凭据存储的事实见 [CURRENT_STATE.md:527]。要分别讲清模型推理、服务器检索、Key 管理三件事。
- **比喻：** 客户端像学生拿自己的会员卡去图书馆旁的老师处提问，服务器先帮忙从指定资料架找原文。对应是客户端持 Key 调模型、服务器提供检索；比喻不表示服务器看不到上传的文字，也不表示模型只会返回原文。
- **频率：常被问（判断；JavaGuide 有 AI 应用开发与 RAG 主题）**；DeepSeek API 参数属于偶尔问。
- **追问：** 为什么模型请求不经过自家 Java 服务？答：用户定模型继续由客户端用自己的 Key 直连，服务器仅提供资料检索；没有记录表明这是经供应商性能比较后选出的方案。[CURRENT_STATE.md:600-604]

### LangChain4j 与 bge-small-zh

- **一句话：** LangChain4j 是 Java 侧 AI 应用库；bge-small-zh-v1.5 是把中文文本变成向量的 embedding 模型。本项目在服务器进程内完成向量化，不调用云端 embedding 接口。[LangChain4j RAG 文档](https://docs.langchain4j.dev/tutorials/rag/)
- **三句话：** Maven 依赖是量化的 `langchain4j-embeddings-bge-small-zh-v15-q`；RAG 使用 512 维向量。上传文档和问题都会生成向量，再由数据库按距离找片段。已能证明用了 LangChain4j 的模型模块；没有找到 LangChain4j 与 Spring AI、或 BGE 与其他 embedding 模型之间的正式对比。[`pom.xml:6,15`](../../refactor/server-java/pom.xml#L6)，[`RAG_API.md:62-68`](../../refactor/server-java/RAG_API.md#L62)
- **深一层：** embedding 是文本的数值表示，便于按语义邻近找候选；它不是答案本身，也不是语言模型。仓库记录的 MDN 小样本前 5 命中 20/20，但这不足以证明所有资料都能同样检索准确。[CURRENT_STATE.md:596,604]
- **比喻：** embedding 像给句子贴一组“意思坐标”，LangChain4j 是 Java 项目调用这类组件的工具箱。对应关系是模型产向量、程序把向量写库并检索；比喻不表示坐标能完整表达语义或自动回答问题。
- **频率：偶尔问（判断；JavaGuide 的 AI/RAG 主题相关，但具体库和模型是项目细节）**。
- **追问：** embedding 模型和生成模型一样吗？答：不一样；embedding 把文本映射为向量用于检索，生成模型根据提示生成文字。本项目的模型调用仍由客户端直接完成。[CURRENT_STATE.md:600-604]

## 5. 安装与更新：PyInstaller、Inno Setup、GitHub Actions、Ed25519

### PyInstaller 与 Inno Setup

- **用途与位置：** PyInstaller 把 Python 程序冻结成可交付目录，见 [`scripts/build_learning_installer.py:143-175`](../../scripts/build_learning_installer.py#L143)；Inno Setup 生成 Windows 安装程序，脚本见 `refactor/desktop/packaging/inno/MentorFlow.iss`。**基本不问（判断：JavaGuide 的 Java、数据库、AI 主题不以桌面打包为重点）；知道用途即可，不列入要背内容，也不出考题。**

### GitHub Actions 与 Ed25519 更新签名

- **一句话：** GitHub Actions 按工作流自动化构建步骤；Ed25519 是一种数字签名算法，用私钥签名、公钥验签。[GitHub Actions 文档](https://docs.github.com/actions)，[RFC 8032](https://www.rfc-editor.org/rfc/rfc8032)
- **三句话：** 项目的 `mf-build`、`mf-publish` 工作流只允许人工 `workflow_dispatch`；正式发布中的签名在本机完成，私钥不复制到 runner。客户端使用内置公钥验签并校验更新清单。[`mf-build.yml:1-6`](../../.github/workflows/mf-build.yml#L1)，[`mf-publish.yml:1-6`](../../.github/workflows/mf-publish.yml#L1)，[`update_protocol.py:13,146`](../../refactor/desktop/delivery/update_protocol.py#L13)，[CURRENT_STATE.md:567-569]
- **深一层：** 数字签名用于验证签名内容是否匹配持有的公钥、以及签名后内容有没有被改；签名不加密内容。项目用 Ed25519 更新清单签名，具体为何选它而不选其他签名算法，未发现比较记录。[RFC 8032](https://www.rfc-editor.org/rfc/rfc8032)
- **比喻：** 私钥像只有发布者能用的印章，公钥像任何人都能拿来验印的印模。对应是签名与验证；比喻不表示公钥能伪造签名，也不表示签名文件被加密。
- **频率：偶尔问（判断；JavaGuide 的安全/系统设计主题相关，项目更新签名属于可展示的工程细节）**。
- **追问：** 如果签名有效，能证明软件没有漏洞吗？答：不能；它证明清单和签名相符，不能证明代码安全或内容本身正确。

## 基础概念速记：遇到这些词先讲人话

| 词 | 小白版 | 官方出处 |
|---|---|---|
| JWT | 带声明、可签名验证的令牌格式；可读不等于加密，也不是本项目所有访问令牌的格式。 | [RFC 7519](https://www.rfc-editor.org/rfc/rfc7519) |
| SQLite WAL | 数据库用追加日志组织写入，再通过 checkpoint 合并；不等于“永远不锁”。 | [SQLite WAL](https://www.sqlite.org/wal.html) |
| pgvector | PostgreSQL 扩展，提供向量列和相似度检索能力。 | [pgvector README](https://github.com/pgvector/pgvector) |
| RLS | 数据库策略按行过滤可见或可写范围；表所有者默认可能绕过，`FORCE` 可改变这一点。 | [PostgreSQL 17 RLS](https://www.postgresql.org/docs/17/ddl-rowsecurity.html) |
| Ed25519 | EdDSA 中的一种签名方案；私钥签、公钥验，不负责加密。 | [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032) |
| embedding | 把文本映射成数字向量，供相似度检索使用；它不是生成答案的模型。 | [LangChain4j RAG](https://docs.langchain4j.dev/tutorials/rag/) |

## “为什么选它”现有证据边界

可以直接引用的用户决策有：求职方向 Java + AI，所以服务器适合的业务迁 Java、AI 编排与模型请求继续在 Python/客户端；模型用用户自己的 Key 直连；RAG 向量存储决定安装 pgvector；更新日常默认只发布增量文件、不重编安装程序。[CURRENT_STATE.md:286-306,567,600-606]

下列对比证据**未发现**：PySide6 对比其他桌面框架、React/Vite 对比其他前端、FastAPI 初始选型、Spring Boot 对比其他 Java 框架、Keycloak 对比其他身份服务、SQLite 对比其他本地库、PostgreSQL/pgvector 对比其他向量库、DeepSeek 对比其他模型的完整评测、LangChain4j 对比 Spring AI、bge 对比其他 embedding 模型、PyInstaller/Inno Setup 的工具对比、Ed25519 对比其他签名算法。面试时说“项目采用了它，目前代码这样实现；我手头没有选型比较证据”，比补造一个理由可靠。

## 参考的官方概念资料

- [Qt for Python / PySide6](https://doc.qt.io/qtforpython-6/)
- [React Learn](https://react.dev/learn) 与 [Vite Guide](https://vite.dev/guide/)
- [Spring Boot Reference](https://docs.spring.io/spring-boot/reference/) 与 [Keycloak Documentation](https://www.keycloak.org/documentation)
- [PostgreSQL 17 Row Security](https://www.postgresql.org/docs/17/ddl-rowsecurity.html) 与 [pgvector](https://github.com/pgvector/pgvector)
- [SQLite Write-Ahead Logging](https://www.sqlite.org/wal.html)
- [JSON Web Token, RFC 7519](https://www.rfc-editor.org/rfc/rfc7519) 与 [EdDSA, RFC 8032](https://www.rfc-editor.org/rfc/rfc8032)
- [LangChain4j RAG](https://docs.langchain4j.dev/tutorials/rag/) 与 [DeepSeek API](https://api-docs.deepseek.com/)
- [PyInstaller](https://pyinstaller.org/en/stable/) 与 [Inno Setup](https://jrsoftware.org/isinfo.php)

## 6. 把工具链补齐：Java请求、SQL和构建分别谁负责

**一句话：** Spring Security处理认证过滤，Spring JDBC执行数据库SQL，Maven管理依赖和构建；这三者不是同一层。[Security官方](https://docs.spring.io/spring-security/reference/servlet/oauth2/resource-server/jwt.html)、[JDBC官方](https://docs.spring.io/spring-framework/reference/data-access/jdbc/core.html)、[Maven官方](https://maven.apache.org/what-is-maven.html)

**三句话：** 项目显式配置SecurityFilterChain，接资源服务器和自己的兼容解码器，保留既有认证边界。[`IdentitySecurity.java:42-84`](../../refactor/server-java/src/main/java/win/clannad/mentorflow/identity/IdentitySecurity.java#L42) Spring JDBC的JdbcTemplate和事务管理器在同步/RAG仓储中使用，沿用既有表、SQL和事务，不用JPA自动建表。[`SyncRepository.java:59-81,97-133`](../../refactor/server-java/src/main/java/win/clannad/mentorflow/sync/SyncRepository.java#L59)、[`RagRepository.java:58-98`](../../refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagRepository.java#L58) Maven按pom打包Java服务，Docker构建也调用mvn package；未找到这些工具各自与替代方案的正式比较，不把“沿用SQL”说成JDBC全面优于ORM。[`pom.xml:4-22`](../../refactor/server-java/pom.xml#L4)、[`Dockerfile:1-5`](../../refactor/server-java/Dockerfile#L1)

**深一层：** 本项目批量写入是外层事务+NESTED保存点，捕获单条业务SyncFailure后仅撤回该条，数据库/未捕获异常撤回外层；这一层不会被认证过滤器或构建工具代替。[`SyncRepository.java:97-161`](../../refactor/server-java/src/main/java/win/clannad/mentorflow/sync/SyncRepository.java#L97)

**比喻：** Security是门口查证件的工作人员，JDBC是按业务指令查写账本的柜员，Maven是开店前装配工具的工厂；分别对应请求认证、SQL访问、构建。边界：查证件不代替业务权限/行级策略，柜员不自动知道业务对错，Maven不负责线上事务。

**频率：常被问（判断；本稿优先级，非统计）**。追问一句：“认证过了为什么还可能读不到记录？”答：项目还按数据库成员关系和事务作用域处理，不是验签成功就能读所有行。[`IdentitySecurity.java:79-84`](../../refactor/server-java/src/main/java/win/clannad/mentorflow/identity/IdentitySecurity.java#L79)、[`SyncRepository.java:164-183`](../../refactor/server-java/src/main/java/win/clannad/mentorflow/sync/SyncRepository.java#L164)

## 7. 部署边界：nginx、Docker Compose和迁移

**一句话：** nginx把入口请求转给后端，Docker把服务放进容器运行，Compose描述这一组容器怎样一起工作；Alembic另外维护数据库版本。[nginx官方](https://nginx.org/en/docs/beginners_guide.html)、[Compose官方](https://docs.docker.com/compose/)、[Alembic官方](https://alembic.sqlalchemy.org/en/latest/)

**三句话：** 本项目曾在edge终止TLS后转内部HTTP，Java要恢复原始HTTPS协议才能让原生认证正常；生产配置内容以ARCH-09记录为据，本稿不假装读到了线上完整edge.conf。[CURRENT_STATE[ARCH-09]] Java用Dockerfile构建JAR/运行镜像，用Compose描述backend与OIDC回环代理等运行关系。[`Dockerfile:1-18`](../../refactor/server-java/Dockerfile#L1)、[`deploy/compose.yml:3-58`](../../refactor/server-java/deploy/compose.yml#L3) Java启动不跑迁移，Alembic沿用既有链由独立步骤执行；未找到与其他代理/编排/迁移工具的横向评测。[`RAG_API.md:60-66`](../../refactor/server-java/RAG_API.md#L60)、[`deploy/migrate.sh:1-23`](../../refactor/server-java/deploy/migrate.sh#L1)

**深一层：** 健康接口能回答服务/依赖是否可用，不替代带有效凭证的真实客户端链路；代理协议故障与RAG迁移权限故障分别见ARCH-09与BUG-107，不把代码成功启动当整个功能完成。[CURRENT_STATE[ARCH-09,BUG-107]]

**比喻：** nginx像接待台，容器像带固定装备的工作间，Compose像各工作间的接线图，Alembic像账本格式的版本变更记录；比喻不表示容器互不影响，也不表示切换镜像会自动回滚数据库。

**频率：偶尔问（判断；本稿优先级，非统计）**。追问一句：“回滚Java为什么不能恢复旧数据库快照？”答：那会覆盖新会话轮换、同步写入和已完成的注销；代码/路由回退与数据倒带要分开。[`CUTOVER148.md`](../../refactor/server-java/CUTOVER148.md)

## 只知道有这回事，不背不考的辅助组件

- **TypeScript：** 前端静态类型/构建工具，项目有真实类型注解与构建依赖；未找到与纯JS的比较，作为Java副项目只知道用途（基本不问，判断）。[`package.json:20-25`](../../refactor/learning/web/package.json#L20)、[`bridge.ts:5-11`](../../refactor/learning/web/src/bridge.ts#L5)
- **QWebChannel：** 页面和Python宿主之间的现成通信桥，项目注册mf对象；未找到其他桥接方案比较，细节不考（基本不问，判断）。[`window.py:282-299`](../../refactor/learning/app/window.py#L282)、[`bridge.py:1-3`](../../refactor/learning/app/bridge.py#L1)
- **HTTPX/asyncio：** Python模型传输用异步HTTP客户端和标准库任务/超时机制；未找到与requests/aiohttp的比较，库API不考（基本不问，判断）。[`providers/_http.py:84-100`](../../refactor/learning/connections/providers/_http.py#L84)、[`transport.py:63-133`](../../refactor/learning/connections/transport.py#L63)
- **ONNX Runtime：** 项目离线OCR通过RapidOCR包装器使用的模型推理依赖，不能把依赖包当成自己实现推理引擎；未找到后端对比，算子/参数不考（基本不问，判断）。[`ocr.py:1-8,48-65`](../../refactor/learning/course/ocr.py#L1)、[`requirements-account-shell.txt:43-46`](../../refactor/desktop/requirements-account-shell.txt#L43)