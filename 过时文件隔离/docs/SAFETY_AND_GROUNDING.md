# 安全与 Grounding

## 本地路径

只接受用户提交的 allowed root 内路径。拒绝盘符根目录、Windows 系统/依赖/凭据目录、隐藏文件、`.git`、`.venv`、`node_modules`、symlink/junction 和不支持格式；限制深度、文件数、单文件/总大小。Preview 返回随机 ID、相对路径、size、mtime、hash；导入时重新校验 token、选择集合、路径、存在性和文件变化。

## URL

只允许 HTTP(S)，DNS 解析后拒绝 private、loopback、link-local、reserved、multicast、unspecified 地址。每次重定向重新校验，限制响应大小和 Content-Type，清理 script/style/nav/footer/header/noscript/iframe，短正文失败并保存 final URL、抓取时间、Content-Type 和快照 hash。

## Grounding

引用必须匹配当前 Workspace 的 source/chunk，页码范围和 quote 必须与原文一致。Critic 失败时降级为 `insufficient`，不保存伪引用。`allow_external_knowledge` 默认关闭；打开时 supplemental 单独显示，不能伪装成来源证据。

## Provider 与错误

Key 只允许通过本机终端的隐藏输入写入 Windows Credential Locker；浏览器和 HTTP API 只流转 `mfcred_...` 引用。Key 不写入数据库、日志、localStorage、ProviderSession 响应或验收 artifact；Gemini 使用请求头而不是 URL。启动时若发现旧版 `mentorflow_api_key_v1:*` 浏览器项，前端只枚举键名并删除，不读取或迁移值，并要求用户轮换密钥。MiniMax 教师语音与私有克隆工具也只接受 Credential Locker 引用，不读取项目密钥文件或明文环境变量。ProviderSession 只在后端进程内存中存在，真实 Workspace 缺少 verified session 时拒绝生成，失败时不回退 Fake。Provider 错误使用统一 code/message/details/request_id；所有响应增加 `X-Request-ID`。Tutor 不声称访问用户电脑，也不把无关问题强行绑定当前课程。

## 测试状态隔离

会创建 Workspace、来源、计划或本地 preview 的 TestClient/验收脚本必须同时切换独立的 `app.db`、`mentorflow_v2.db` 和主动教学上传目录，临时状态统一位于 `E:\Codex\Temp`。脚本退出时先关闭 TestClient 和临时 ORM engine，再恢复进程内原路径；验证时应比较活动数据库执行前后的 SHA-256。只切换旧 `app.db` 不算隔离，因为 FastAPI lifespan 和部分接口仍会访问 ORM 数据库。标准 `scripts/test.ps1` 连迁移也只对临时 ORM 库执行，并在结束时强制检查活动库哈希。Playwright 使用独立端口、禁止复用已有服务，前端代理只指向本次测试启动的临时后端。旧的 Provider-free、课程专用现场预演已禁用，避免在预期的 `428 PROVIDER_VERIFICATION_REQUIRED` 之前写入半成品状态。
