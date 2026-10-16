# Java 后端部署操作

本目录的操作在生产服务器本机执行。Java 服务复用现有数据库和只读配置；Java 不跑数据库迁移。任何 Compose 命令都必须把 BUG-45 网络 override 作为最后一个 `-f` 文件。切换期间保留现有 Python 镜像、容器和 Compose 定义。

## 准备与启动 Java

把发布源码放到服务器的 release 目录后，在本目录执行以下准备。`BACKEND_CONTAINER` 是当前 Python 容器名；truststore 目录使用现有 TLS 材料目录。`VERSION` 使用本次源码版本号。

```sh
cd /opt/mentorflow/releases/<release>/refactor/server-java/deploy
sh prepare-config.sh mentorflow-e1-preview-backend-1 /opt/mentorflow/state/java-shadow/trust
sh build.sh VERSION
```

`prepare-config.sh` 从运行中的 Python 容器读取其环境变量和挂载路径，并写出 `.env`、`.java.env` 与 `compose.generated.yml`。数据库密码只在容器启动时从既有 secrets 卷读取，不写入这些文件。检查生成文件属于本次预期后，先在当前运维 shell 指向现有 Compose `--env-file`：

```sh
export BACKEND_ENV=/服务器上现有的/compose.env
sh java-compose.sh config
sh java-compose.sh up
```

Compose 运行容器的 labels 可用于确认项目名、配置文件组和工作目录：

```sh
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' mentorflow-e1-preview-backend-1
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' mentorflow-e1-preview-backend-1
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' mentorflow-e1-preview-backend-1
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.environment_file" }}' mentorflow-e1-preview-backend-1
```

`environment_file` 标签是否存在取决于 Compose 版本和创建方式；用它辅助查找，再从原运维命令或 shell 环境确认实际环境文件并设置 `BACKEND_ENV`，不要猜路径或打印文件内容。标签若列出多个路径，不要把逗号列表直接当成一个文件路径。`java-compose.sh` 会从容器 labels 保留全部当前配置层（含 session/MF overlays），去除已存在的 BUG-45 项后追加 `compose.yml`、`compose.generated.yml` 与 network override。旧 `BACKEND_ENV` 和 deploy `.env` 都会显式传给 Compose；BUG-45 必须是最后一个 `-f`。脚本只执行 `up -d --no-deps java-backend java-oidc`，不会启动或重建其它服务。

Java 服务没有 Docker `HEALTHCHECK`。先确认容器状态为 `running`，再用保留的 Python 镜像临时运行 readiness 与契约探针；若 Java 尚未就绪，探针会失败，等启动完成后重跑。这样探针在项目内部网络中执行，不依赖服务器主机能解析容器别名：

```sh
PYTHON_IMAGE=$(docker inspect --format '{{.Config.Image}}' mentorflow-e1-preview-backend-1)
docker inspect --format '{{.State.Status}}' mentorflow-java-backend
docker run --rm --network mentorflow-e1-preview_internal \
  -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python \
  "$PYTHON_IMAGE" /tmp/probe.py
```

它会检查 readiness，并对比 `http://java-backend:8080` 与 `http://backend:8000` 的未认证和无效令牌请求；别名不同时通过 `--java` / `--python` 传入地址。探针只读。仅当 readiness 返回 200 且响应契约全部匹配时继续。

## 切换与回退

将 `cutover.sh`、`probe.py` 与本 README 一起放在本次 deploy 目录。当前 edge 可能仍指向旧影子 Java (`mentorflow-java-shadow:8080`)；切到正式容器时脚本只会改写唯一的账号核心 upstream 块。`cutover.sh java` 可从旧影子 Java 或 Python upstream 切到正式 `java-backend:8080`：

```sh
sh cutover.sh java
docker run --rm --network mentorflow-e1-preview_internal \
  -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python \
  "$PYTHON_IMAGE" /tmp/probe.py
```

脚本只接受唯一 upstream 块中的一个已知 server 行，先通过 Docker 在 edge 容器内运行 `nginx -t`，成功后 reload。它用 `cat > edge.conf` 写回原文件 inode，供现有 bind mount 继续引用。`nginx -t` 或 reload 失败时，脚本自动恢复原文件内容并尝试重新检查、加载旧配置。

需要回退时，Python 仍在运行：

```sh
sh cutover.sh python
docker run --rm --network mentorflow-e1-preview_internal \
  -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python \
  "$PYTHON_IMAGE" /tmp/probe.py
```

只有正式 Java 已接管 upstream，且真实桌面客户端的登录、同步、上课流程均正常后，才停用 Python（可选）：

```sh
sh stop-python.sh
```

这个脚本先确认 edge 唯一 upstream 正指向正式 `java-backend`，否则拒绝停机；随后从现有 Python 容器的 Compose labels 动态还原项目名、工作目录和完整文件组，再执行 `stop oidc-loopback-proxy backend`，保留两个容器、镜像和 Compose 定义。Compose 文件组中已有的 BUG-45 文件会去重，并把 `/opt/mentorflow/state/bug45-20261010/network45.override.yml` 作为最后一个 `-f` 参数。脚本使用 `PYTHON_ENV_FILE`，未设置时沿用已导出的 `BACKEND_ENV`；若服务使用 profile，设置逗号分隔的 `PYTHON_COMPOSE_PROFILES`。执行 stop 和 restore 时应保留这些 shell 环境变量。

停用后如需恢复，用一条命令启动现有 Python 服务、等待其健康，再切回 Python：

```sh
sh restore-python.sh
```

恢复过程中若 Python 未健康或 OIDC loopback proxy 未运行，脚本停止且 edge 保持原 upstream，便于排查；两者就绪后才调用 `cutover.sh python`。如切回遇到 nginx 校验或 reload 错误，cutover 脚本会自动恢复切换前的 edge 配置。

## 上线核对

`probe.py` 不创建账号、不改数据、不发邮件。运行前先从 Python 容器取得镜像名并保存到当前 shell 的 `PYTHON_IMAGE`，停用后仍用该镜像启动一次性探针容器：

```sh
PYTHON_IMAGE=$(docker inspect --format '{{.Config.Image}}' mentorflow-e1-preview-backend-1)
docker run --rm --network mentorflow-e1-preview_internal \
  -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python \
  "$PYTHON_IMAGE" /tmp/probe.py --java-only
```

它比较 live/startup 和代表性认证、目录、同步响应，忽略 request ID、耗时和检查时间等动态字段。Python 与 Java 的 email readiness 都必须是 `ready=true, code=EMAIL_TLS_AUTH_READY`；停用 Python 后的 `--java-only` 仍会要求 Java 保持此结果。native refresh 探针携带 `X-Forwarded-Proto: https` 来模拟 edge 转发；它只送无效 token，不代表有效凭证的认证测试。`--java-only` 只请求 Java，不会 exec 到已停容器。探针要求 readiness 的 `status` 为 `ready`、`started` 为 true，`postgres` 与 `keycloak_discovery` 必须存在且 ready；无效 JSON 或字段也会失败。服务器特有的网络连通、容器健康、Nginx reload 和真实登录仍需在服务器验证；本机脚本测试不能替代这些检查。
