# 资料问答检索接口

这些接口只上传、保存、检索文字，不接收原文件，不调用大模型。回答生成仍由客户端直连用户选择的模型。模型向量在 Java 进程内计算。

## 身份与隔离

沿用正式 Java API 的 Bearer 登录和 `X-MentorFlow-Organization-ID`、`X-MentorFlow-Workspace-ID`。只能使用已核实的 HUMAN 账号。资料所有权按已验证的 issuer + subject 隔离，而不是按计划隔离；同账号可以跨计划复用资料。数据库同时启用 FORCE RLS，每次事务设置该账号的作用域。客户端传来的任何账号字段都不决定所有权。

统一错误格式：`{"error":{"code":"...","message":"...","details":{},"request_id":"..."}}`。所有结果不缓存。

## 上传文字块

`POST /api/enterprise/rag/v1/documents/{doc_id}/chunks`

```json
{"title":"资料标题","source":"https://example.org/book","total_chunks":2,
 "chunks":[{"seq":0,"text":"第一段正文","page":1,"section":"第一章"}]}
```

客户端自己给稳定的 `doc_id`，按同一个 ID 分批上传。`seq` 从 0 开始，最终覆盖 `0..total_chunks-1`。`page`、`section` 可省略。单次 HTTP 请求的完整 UTF-8 JSON（含元数据、空白）最多 **9 MiB = 9437184 字节**；超过返回 413 `RAG_REQUEST_TOO_LARGE`。客户端应留出 JSON 开销，不按原文字数直接顶满上限。

相同文档、相同序号、相同正文重复上传是幂等的。全部块齐全前是 `uploading`，不参与检索；齐全后是 `ready`。完整资料按内容 SHA-256 在账号内去重。不同输入 ID 的相同资料合并到一个 canonical 文档，保留别名供后续检索、重传和删除使用。客户端应保存上传返回的 canonical ID；不要把输入 ID 和最终 ID 必须相同作为假设。

上传响应：

```json
{"document_id":"doc-a","requested_document_id":"this-import-id",
 "status":"ready","received_chunks":2,"total_chunks":2,"deduplicated":true,
 "source_layout_complete":true}
```

去重按序号连接各块的原始 UTF-8 正文计算 SHA-256，不包含标题、来源和页码，也不依赖块边界。不同分块方式的相同全文也只保存一份；别名保留每个原始块的摘要，以支持丢失响应后的整批或部分重传，不重复保存正文。普通去重响应的 `received_chunks` 和 `total_chunks` 是 canonical 块数；已提升 ID 的重放/列表继续沿用269的例外，按其原来源 manifest 块数报告。客户端用 `status=ready` 判断正文完成，并保留 `requested_document_id` 用于重试；布局另看新字段。已完成 ID 的正文不能被另一个内容覆盖，返回409 `RAG_DOCUMENT_ID_CONFLICT`。

323 起另外保存每个逻辑来源自己的块顺序、页码/章节及指向共享正文的 UTF-8 字节区间，不复制正文和向量。摘要计算没有 trim、Unicode 归一化或分隔符。`source_layout_complete` 只说明本次 requested ID 的完整来源布局已经核验，不能由 canonical 的 ready/块数代替。

支持来源布局的服务器宣布 `source_layout_v1` capability。旧别名和旧提升文档没有保存过原位置；客户端有完整原块时，仍分批 POST 到原 `/documents/{requested_id}/chunks`，正文与既有摘要清单一致后只幂等补来源布局，不重新 embedding。每批沿用原请求上限；完整 `0..total_chunks-1` 落库并核验前，回执为 `source_layout_complete:false`。冲突返回409且保留原资料。无原块时不推测位置、不重写资料或历史回答。

## 列表、删除与检索

`GET /api/enterprise/rag/v1/documents`：返回本账号资料及入库状态，包含已接收块数、总块数与 canonical ID。

```json
{"documents":[{"document_id":"doc-a","title":"资料标题","source":"https://example.org/book",
 "total_chunks":2,"received_chunks":2,"status":"ready",
 "created_at":"2026-10-16T00:00:00Z","aliases":["this-import-id"],
 "source_layouts":[{"requested_document_id":"doc-a","total_chunks":2,"complete":true},
                   {"requested_document_id":"this-import-id","total_chunks":1,"complete":false}]}],
 "capabilities":["append_only_chunk_repair_v1","source_layout_v1"]}
```

`DELETE /api/enterprise/rag/v1/documents/{doc_id}`：只删除该逻辑来源。删除别名时解除该别名及其布局；删除 canonical 且还有别名时，按 ID 顺序提升一个剩余别名，保留共享正文/向量及剩余来源各自布局。只有最后一个逻辑来源被删除才清除共享资料。提升不代表把旧主文档的位置赋给新主来源。

`POST /api/enterprise/rag/v1/search`

```json
{"document_ids":["doc-a","doc-b"],"query":"事务的原子性是什么意思？","k":5,
 "source_layout_v1":true}
```

```json
{"passages":[{"document_id":"doc-a","seq":0,"text":"正文原文",
 "page":"1","section":"事务","score":0.83}]}
```

只检索显式选择的文档；未选文档不参与。`document_ids`保留实际requested/remote_upload_id，不把别名折叠成canonical。共享正文的多个来源分别映射自己的逻辑块，不固定first requested。已核布局返回该来源原始seq/完整块text/page/section；不同分块方式映射后同一逻辑块去重。

先由pgvector取语义候选，再与关键词混合排序；逻辑块继承共享候选分数，不为每个别名重算向量。HNSW是否使用由PostgreSQL优化器选择，关键词使用规范化字符二元组。分数仅供相对排序，不是正确率，正文不由模型改写。

只有列表已宣布能力后，新客户端才发送`source_layout_v1:true`。该历史来源缺少可证布局时，保留所选ID和正文，无法证实的seq/page/section为null，并明确标记：

```json
{"document_id":"legacy-alias","seq":null,"text":"已存正文片段",
 "page":null,"section":null,"score":0.83,"source_layout_status":"missing"}
```

客户端显示出处未知，有原块才安全回填。不能证明属于该来源的片段不冒称其原文，可用`source_warnings`明确标记证据缺口；资料仍在列表，不静默换成主来源位置。

未选择新协议的旧调用保持原响应类型：有完整布局时返回正确来源的非负整数seq；真缺布局时仍沿旧协议兼容路径，不新增409或突然使已有资料不可查询。该旧路径的历史未知出处仍受原限制，**不能称已获得完整来源修复**；修复经新版客户端和显式新能力生效。不得以默认页码或伪造回填宣称原位置已经恢复。

`k` 默认 5，范围 1–100；一次可选最多 500 个文档 ID。空选择返回空结果，未知或其他账号的 ID 不参与检索。候选池为 `max(50,k*10)`，最终分数为 `0.8 * ((cosine+1)/2) + 0.2 * bigram_jaccard`，再取前 k 段。原文保留上传时的边缘空白；`page` 和 `section` 返回文字或 null，数字页码转成文字。每份资料最多 100000 块，每块正文最多 2000000 个 Java 字符；这不取代 9 MiB 请求上限。

## 运行与迁移

当前新增前向修订`0048_rag_source_chunks`。新表仅存来源布局、摘要与正文区间，带完整scope外键、FORCE RLS和账号闭包清理/完成检查。0047普通原canonical可回填自身真实布局；旧alias和带promoted manifest的来源即使正文摘要相同也不从旧主块借页码，保持未知待原块核验。回退应用保留加法表；严格要求0047的旧Python备用镜像不能直接接0048，需同批新源码重建并验证备用服务，不等于旧镜像已兼容。

Alembic 管理 RAG 表；Java 启动不自动迁移。架构师上线时先备份数据库，使用现有迁移镜像/命令执行 `alembic upgrade head`，再构建 Java 镜像。迁移只检查 `vector` 扩展已存在，缺少时明确报错，绝不以应用角色执行 `CREATE EXTENSION`。本批以已安装 pgvector 0.8.7 的 PostgreSQL 17 为生产环境。模型是 Maven 官方模块自带的量化 ONNX 资源，构建镜像时下载；首次检索/上传在进程内加载。根据本批报告的实测内存调整容器上限，并为本地 native 库缓存准备可写临时目录。

正式存储是 `vector(512)`，索引为 HNSW `vector_cosine_ops`；使用 JDBC 的 pgvector SQL，不添加第二套连接池或存储库依赖。查询设置 `hnsw.iterative_scan=strict_order`，过滤账号和选定文档时继续扫描候选，减少过滤后结果不足。HNSW 是近似检索，不保证与全量精确检索完全相同。

本机若没有 pgvector，仅显式的 Alembic 测试参数 `-x rag_vector_backend=local-test` 建立 bytea 测试表；对应的 Spring `rag154-test` 实现只存在于测试源码，不打进发布包，生产没有 bytea 回退。一次性 pgvector 测试库可运行 `deploy/rag-verify.sh`，验证正式 JDBC 存储、查询、RLS、删除与迁移回退；用法见本批报告。

依赖固定为 `dev.langchain4j:langchain4j-embeddings-bge-small-zh-v15-q:1.22.0-beta32`（LangChain4j Apache-2.0，BGE 模型 MIT）。[官方模型说明](https://docs.langchain4j.dev/apidocs/dev/langchain4j/model/embedding/onnx/bgesmallzhv15q/BgeSmallZhV15QuantizedEmbeddingModel.html) 建议资料块不超过约 512 tokens 以保证效果，这不是本接口的硬性截断线。模型及 tokenizer 随 JAR 提供，运行时不需要访问大模型服务。

只读容器根目录下应将 `DJL_CACHE_DIR`、`ENGINE_CACHE_DIR`、`HF_HOME`、`HF_HUB_CACHE` 指向可写 `/tmp` 子目录，并保留可写 `java.io.tmpdir`。模型文件已打包，native 库仍需解压。现有 Compose `/tmp` 为 128 MiB（见 `deploy/compose.yml`），供 native 库解压、缓存和 JVM 临时文件使用；容量调整沿用部署流程，不在本机修改服务器。
