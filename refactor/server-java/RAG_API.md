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
 "status":"ready","received_chunks":2,"total_chunks":2,"deduplicated":true}
```

去重按序号连接各块的原始 UTF-8 正文计算 SHA-256，不包含标题、来源和页码，也不依赖块边界。不同分块方式的相同全文也只保存一份；别名保留每个原始块的摘要，以支持丢失响应后的整批或部分重传，不重复保存正文。响应中的 `received_chunks` 和 `total_chunks` 是 canonical 文档的块数；客户端用 `status=ready` 判断完成，并继续保留本次 `requested_document_id` 用于重试。已完成 ID 的正文不能被另一个内容覆盖，返回 409 `RAG_DOCUMENT_ID_CONFLICT`。

## 列表、删除与检索

`GET /api/enterprise/rag/v1/documents`：返回本账号资料及入库状态，包含已接收块数、总块数与 canonical ID。

```json
{"documents":[{"document_id":"doc-a","title":"资料标题","source":"https://example.org/book",
 "total_chunks":2,"received_chunks":2,"status":"ready",
 "created_at":"2026-10-16T00:00:00Z","aliases":["this-import-id"]}]}
```

`DELETE /api/enterprise/rag/v1/documents/{doc_id}`：删除 canonical ID 时删除该资料、文字块、向量及其别名；删除别名时只解除这次导入的别名，保留原资料。多个计划复用的是同一份资料，客户端删除 canonical 资料前应告知这一点。

`POST /api/enterprise/rag/v1/search`

```json
{"document_ids":["doc-a","doc-b"],"query":"事务的原子性是什么意思？","k":5}
```

```json
{"passages":[{"document_id":"doc-a","seq":0,"text":"正文原文",
 "page":"1","section":"事务","score":0.83}]}
```

只检索显式选择的文档；未选文档不参与。先由 pgvector 按余弦距离取语义候选，再与关键词重合混合排序；已建 HNSW 余弦索引，具体是否使用该索引由 PostgreSQL 优化器选择。关键词使用规范化后的字符二元组。分数用于本次检索的相对排序，不是正确率。返回的是数据库原文，不是模型改写。找不到时返回空 `passages`，客户端据此说明资料没有找到答案。

`k` 默认 5，范围 1–100；一次可选最多 500 个文档 ID。空选择返回空结果，未知或其他账号的 ID 不参与检索。候选池为 `max(50,k*10)`，最终分数为 `0.8 * ((cosine+1)/2) + 0.2 * bigram_jaccard`，再取前 k 段。原文保留上传时的边缘空白；`page` 和 `section` 返回文字或 null，数字页码转成文字。每份资料最多 100000 块，每块正文最多 2000000 个 Java 字符；这不取代 9 MiB 请求上限。

## 运行与迁移

Alembic 管理 RAG 表；Java 启动不自动迁移。架构师上线时先备份数据库，使用现有迁移镜像/命令执行 `alembic upgrade head`，再构建 Java 镜像。迁移只检查 `vector` 扩展已存在，缺少时明确报错，绝不以应用角色执行 `CREATE EXTENSION`。本批以已安装 pgvector 0.8.7 的 PostgreSQL 17 为生产环境。模型是 Maven 官方模块自带的量化 ONNX 资源，构建镜像时下载；首次检索/上传在进程内加载。根据本批报告的实测内存调整容器上限，并为本地 native 库缓存准备可写临时目录。

正式存储是 `vector(512)`，索引为 HNSW `vector_cosine_ops`；使用 JDBC 的 pgvector SQL，不添加第二套连接池或存储库依赖。查询设置 `hnsw.iterative_scan=strict_order`，过滤账号和选定文档时继续扫描候选，减少过滤后结果不足。HNSW 是近似检索，不保证与全量精确检索完全相同。

本机若没有 pgvector，仅显式的 Alembic 测试参数 `-x rag_vector_backend=local-test` 建立 bytea 测试表；对应的 Spring `rag154-test` 实现只存在于测试源码，不打进发布包，生产没有 bytea 回退。一次性 pgvector 测试库可运行 `deploy/rag-verify.sh`，验证正式 JDBC 存储、查询、RLS、删除与迁移回退；用法见本批报告。

依赖固定为 `dev.langchain4j:langchain4j-embeddings-bge-small-zh-v15-q:1.22.0-beta32`（LangChain4j Apache-2.0，BGE 模型 MIT）。[官方模型说明](https://docs.langchain4j.dev/apidocs/dev/langchain4j/model/embedding/onnx/bgesmallzhv15q/BgeSmallZhV15QuantizedEmbeddingModel.html) 建议资料块不超过约 512 tokens 以保证效果，这不是本接口的硬性截断线。模型及 tokenizer 随 JAR 提供，运行时不需要访问大模型服务。

只读容器根目录下应将 `DJL_CACHE_DIR`、`ENGINE_CACHE_DIR`、`HF_HOME`、`HF_HUB_CACHE` 指向可写 `/tmp` 子目录，并保留可写 `java.io.tmpdir`。模型文件已打包，native 库仍需解压。现有 Compose `/tmp` 为 64 MiB；建议结合报告实测为缓存和普通临时文件留出余量，架构师上线时配置，不在本机修改服务器。
