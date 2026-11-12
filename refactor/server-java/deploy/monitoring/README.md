# MentorFlow 本机监控 Compose

本目录是 385-MF06 的唯一配置来源，部署 Uptime Kuma（HTTP/TLS/容器告警）和 Beszel（主机及 Docker 资源历史）。服务只监听服务器回环地址，桌面端通过 SSH 隧道访问；不新增公网端口。维护 owner：MF-06，变更需由架构师安排维护窗口。

## 固定镜像

镜像使用官方 GHCR，按 `linux/amd64` 固定 tag 和 digest。版本/摘要于 2026-11-12 从官方 package 页面核对：

| 服务 | 固定引用 | 依据 |
| --- | --- | --- |
| Uptime Kuma | `ghcr.io/louislam/uptime-kuma:2.5.10@sha256:79e2757483d270d07e183cb7054198d8b75a5ac510a5e90a450567c3a019f514` | [官方 package](https://github.com/louislam/uptime-kuma/pkgs/container/uptime-kuma) |
| Beszel Hub | `ghcr.io/henrygd/beszel/beszel:0.21.1@sha256:dd149010e30f4ba5876b6a65eb7ba3026f8c10d14dc07808d6324b02d477d93d` | [官方 package](https://github.com/henrygd/beszel/pkgs/container/beszel%2Fbeszel/1429915881?tag=0.21.1) |
| Beszel Agent | `ghcr.io/henrygd/beszel/beszel-agent:0.21.1-alpine@sha256:e1db7d0fdfb380cfbab1f5eac7a08c51d72b12dd1b1ab5517eddfa0a2b14fd39` | [官方 package](https://github.com/henrygd/beszel/pkgs/container/beszel%2Fbeszel-agent) |

这些 digest 是架构特定的 amd64 digest；MF-01 安装前必须在目标主机执行 `docker manifest inspect` 或等价校验，确认 registry 返回的 digest 与此文件一致，失败就停止，不改成 `latest`。

## 端口、卷和权限

- Kuma：`127.0.0.1:3001`，数据卷 `mentorflow_monitoring_uptime_kuma_data`。
- Beszel Hub：`127.0.0.1:8090`，卷 `mentorflow_monitoring_beszel_data` 和 `mentorflow_monitoring_beszel_socket`。
- Beszel Agent：host network 仅为采集主机指标，配置使用 Unix socket，不监听新的 TCP 端口；安装脚本会比较安装前后的完整 `ss -ltnp`，只允许新增 `127.0.0.1:3001` 和 `127.0.0.1:8090`；卷 `mentorflow_monitoring_beszel_agent_data`。Kuma 和 Agent 都以只读方式挂载 `/var/run/docker.sock`。注意：Unix Docker socket 即使文件以 `:ro` 挂载，daemon API 仍可能具备高权限；这是当前监控 Docker 指标的明确风险边界，不能把 `:ro` 当成权限隔离。
- 每个容器均带 source、purpose、listen 和 owner label。Compose `down` 默认保留 named volumes。

Beszel Agent 的受限 env 文件由 MF-01 在服务器创建，路径通过 `BESZEL_AGENT_ENV_FILE` 指定，至少需要 `TOKEN` 和 `KEY` 两个键。值不进仓库、不进回执、不打印日志。不要把 Kuma 登录密码、通知 token、SMTP 密码写入 Compose。

## 监控项来源与安装

`kuma-monitors.json` 是 377 现场七个容器、Java 三个健康接口和 IP/两个域名证书的可审计清单，共 13 项。健康/容器 60 秒、失败重试 2 次、15 秒超时；证书 15 分钟检查，IP 证书提前 3 天提醒，域名证书提前 14 天提醒。前者在短周期证书续期失败时尽快发现，后者给外部域名证书留下维护窗口。

Uptime Kuma 的监控项和通知路由保存在其 SQLite 数据库，当前没有稳定、受支持的 declarative import API；因此本任务不伪造 API 客户端，也不把登录凭据写入脚本。MF-01 按清单在 Kuma UI 中建立监控项并导出备份；每次变更先更新此 JSON，再在 UI 中逐项核对。校验清单：

```sh
python3 validate-kuma-manifest.py
```

Kuma Docker monitors 依赖 socket 只读挂载和上表中的精确容器名。证书监控应保持 TLS 校验开启；IP 项检查 `https://89.45.45.28/` 的证书 SAN，不要把 `Ignore TLS` 打开。

告警 destination 是 SMTP 邮件。当前 Java `NativeEmailHealthProbe` 只探测 identity email transport，不能作为告警发件器；仓库和 377 证据没有可直接复用的 SMTP 发件键值，因此需要用户另提供以下键名，由 MF-01 写入受限 secret 文件：`MF_ALERT_SMTP_HOST`、`MF_ALERT_SMTP_PORT`、`MF_ALERT_SMTP_USERNAME`、`MF_ALERT_SMTP_PASSWORD_FILE`、`MF_ALERT_SMTP_FROM`、`MF_ALERT_SMTP_TO`。Beszel 的主机/容器告警设置见 `beszel-settings.md`。Healthchecks.io 外部检查的键名同样只保留：`HEALTHCHECKS_HEARTBEAT_URL`、`HEALTHCHECKS_BACKUP_URL`、`HEALTHCHECKS_CERT_URL`。

## 首次登录

桌面脚本免的是 SSH 交互登录，不会打印或读取私钥内容。Kuma 和 Beszel 的应用登录不在 Compose 中绕过：首次分别完成管理员初始化后，浏览器保存会话即可。Kuma 可在首次登录后的 Security 页面手动关闭内置认证，但这会把安全边界完全交给 SSH 隧道，只能由架构师在现场确认后操作；Beszel 的 `DISABLE_PASSWORD_AUTH` 只用于 OAuth/OIDC，`AUTO_LOGIN` 需要已确认的用户邮箱，本阶段均不写入。

## 升级、备份、恢复和卸载

升级前在服务器维护窗口内备份四个 named volumes，并保留当前 compose、manifest 和镜像 digest；再改 tag/digest，执行 `docker compose config`、`docker compose pull`、逐项健康及通知恢复测试。回滚时恢复旧 compose/digest，先停服务再按同样顺序启动。Kuma 数据库备份应包含 UI 监控项和通知路由；Beszel Hub/Agent 数据也要一起备份。

`docker compose down` 只移除容器和网络，不删除卷。彻底卸载必须由架构师明确批准后再执行 `docker volume rm mentorflow_monitoring_uptime_kuma_data mentorflow_monitoring_beszel_data mentorflow_monitoring_beszel_socket mentorflow_monitoring_beszel_agent_data`；删除前应确认备份可读。任何备份命令不得把文件内容或 secret 值写入终端/回执。

## 访问边界

第一阶段只走桌面 SSH 隧道，不能把 3001/8090 映射公网，也不在本任务创建用户、密钥、Cloudflare DNS 或 Access。第二阶段若改为子域名，必须另有认证、origin TLS、审计和防匿名泄露设计。

桌面脚本按任务要求复用现有 `mentorflow-admin` 管理员钥匙；377 证据显示该账号有 `NOPASSWD: ALL`，本任务不创建新账号或钥匙，因此 MF-01 应把快捷方式和私钥文件权限限制在运维用户，并把隧道仅用于回环转发。后续若要降低权限，应另派账号/授权收敛任务。
