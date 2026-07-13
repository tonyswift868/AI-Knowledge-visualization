# 安全与 Grounding

## 本地路径

只接受用户提交的 allowed root 内路径。拒绝盘符根目录、Windows 系统/依赖/凭据目录、隐藏文件、`.git`、`.venv`、`node_modules`、symlink/junction 和不支持格式；限制深度、文件数、单文件/总大小。Preview 返回随机 ID、相对路径、size、mtime、hash；导入时重新校验 token、选择集合、路径、存在性和文件变化。

## URL

只允许 HTTP(S)，DNS 解析后拒绝 private、loopback、link-local、reserved、multicast、unspecified 地址。每次重定向重新校验，限制响应大小和 Content-Type，清理 script/style/nav/footer/header/noscript/iframe，短正文失败并保存 final URL、抓取时间、Content-Type 和快照 hash。

## Grounding

引用必须匹配当前 Workspace 的 source/chunk，页码范围和 quote 必须与原文一致。Critic 失败时降级为 `insufficient`，不保存伪引用。`allow_external_knowledge` 默认关闭；打开时 supplemental 单独显示，不能伪装成来源证据。

## Provider 与错误

Key 不写入数据库、日志、localStorage、ProviderSession 响应或验收 artifact；Gemini 使用请求头而不是 URL。ProviderSession 只在后端进程内存中短期存在，真实 Workspace 缺少 verified session 时拒绝生成，失败时不回退 Fake。Provider 错误使用统一 code/message/details/request_id；所有响应增加 `X-Request-ID`。Tutor 不声称访问用户电脑，也不把无关问题强行绑定当前课程。
