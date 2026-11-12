# Beszel 可复核设置

这是 Beszel Hub 首次初始化和 UI 变更的无密钥清单。Hub 把用户、系统和告警规则保存在自己的数据卷中；本文件记录应保持的设置，方便 MF-01 逐项核对并把 Hub 数据卷纳入备份。

## Hub 与 Agent

- Hub URL：`http://127.0.0.1:8090`，与 `compose.yml` 的 `APP_URL` 一致。
- Agent：本机系统名使用 `mentorflow-server`；连接使用同机 Unix socket `/beszel_socket/beszel.sock`，不要为 Agent 另开公网端口。
- Agent 采集主机 CPU、内存、磁盘和 Docker 容器；Docker socket 只读挂载。Agent 的 `KEY`、`TOKEN` 只从服务器受限 env 文件读取。
- 7 个现有运行容器由 Kuma 清单负责存活告警；Beszel 负责资源历史和容器资源占用，不重复创建同一套 HTTP 监控。

## 告警

- 邮件通道：启用 SMTP；服务器受限文件只保留 `MF_ALERT_SMTP_HOST`、`MF_ALERT_SMTP_PORT`、`MF_ALERT_SMTP_USERNAME`、`MF_ALERT_SMTP_PASSWORD_FILE`、`MF_ALERT_SMTP_FROM`、`MF_ALERT_SMTP_TO` 这些键名和值，不能进仓库或日志。
- 必须启用：Agent/主机离线、容器停止（针对 377 清单中的 7 个运行容器）。恢复通知也开启，便于区分短暂故障和已恢复状态。
- CPU、内存、磁盘阈值暂不预设目标；先按装前/装后测量结果和 Beszel 默认采集观察。若 MF-01 后续调整阈值，先改本文件再在 UI 保存，并把变更纳入 Hub 数据卷备份。

## 登录结论

本阶段不把 `AUTO_LOGIN`、`TRUSTED_AUTH_HEADER` 或 `DISABLE_PASSWORD_AUTH` 写进 Compose：它们会绕过或改变 Hub 身份校验，并且需要一个已确认的用户身份/OIDC 配置。首次进入 Hub 创建/使用架构师指定的现有运维用户，在浏览器保存会话或密码管理器条目；不要为桌面图标另建服务器账号。

Beszel 没有适合本阶段的“无条件关闭认证”配置；`DISABLE_PASSWORD_AUTH` 只适用于已配置 OAuth/OIDC，`AUTO_LOGIN` 虽可为已确认的单一用户绕过登录，但会把安全边界完全交给 SSH 隧道。本阶段不写入用户邮箱或这些开关。若未来采用 OIDC/自动登录，必须另派任务审计回调地址和用户创建策略。

## 可重复方式

Beszel 的系统、用户和告警规则由 Hub 数据卷保存，没有稳定受支持的仓库配置导入接口。本清单与 Hub 数据卷备份一起作为复核依据；每次 UI 变更都应同步本文件并做一次恢复演练。
