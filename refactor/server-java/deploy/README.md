# Java 后端部署操作

本目录的操作在生产服务器本机执行。Java 服务复用现有数据库和只读配置；应用容器启动时不跑数据库迁移，迁移通过下文独立维护命令执行。任何 Compose 命令都必须把 BUG-45 网络 override 作为最后一个 `-f` 文件。当前 Python backend 已停止、edge upstream 指向正式 `java-backend:8080`；保留 Python 镜像、容器和 Compose 定义供探针比较与回退。

219 批会话、身份查询、请求体与同步契约修复的构建、检查及回退清单见 [RELEASE219.md](RELEASE219.md)。该清单是部署准备，不代表已经上线。

## RAG 上线迁移（MF-156）

先备份数据库，再从本次发布源码目录运行 `migrate.sh`。基础
`mentorflow-slim-backend:MF-76` 镜像只装运行依赖，按 DEPLOY.md 不包含 Alembic；因此
脚本基于该现有镜像构建一个可复用的 `mentorflow-migration-tool:MF-156` 辅助镜像，只额外
安装已记录的 `alembic==1.18.5`，不会改写基础镜像。默认使用
`mentorflow-e1-preview_internal` 网络和 `mentorflow-e1-preview_oidc_proxy_secrets` secrets
卷；迁移密码只在容器内从 `MF_MIGRATION_PASSWORD_FILE` 指定的路径读取，绝不写入 `.env`
或命令文件。部署前先只读查看卷里有哪些文件名（命令不输出文件内容）：

```sh
docker run --rm --entrypoint /bin/sh \
  -v mentorflow-e1-preview_oidc_proxy_secrets:/run/secrets:ro \
  mentorflow-slim-backend:MF-76 -c 'find /run/secrets -maxdepth 1 -type f -printf "%f\\n"'
```

按实际列出的迁移密码文件设置路径；如名字不同，显式导出，例如
`export MF_MIGRATION_PASSWORD_FILE=/run/secrets/<实际迁移密码文件名>`。不要 `cat` 文件或把密码写进 shell history。脚本默认路径
`/run/secrets/postgres_migrator_password` 仅作为可配置缺省值，会在容器内检查文件存在且非空。
源码的
`refactor/server` 只读挂载为 Alembic 工作目录，脚本会先打印 `current`，执行
`upgrade head`，再打印一次 `current`：

```sh
cd /opt/mentorflow/releases/<release>/refactor/server-java/deploy
sh migrate.sh upgrade
```

`migrate.sh current` 可单独查看版本。若部署环境中的基础镜像、工具镜像、网络、secrets
卷或 secret 路径不同，可用 `MIGRATION_IMAGE`、`MIGRATION_TOOL_IMAGE`、
`MIGRATION_NETWORK`、`MIGRATION_SECRETS_VOLUME` 或 `MF_MIGRATION_PASSWORD_FILE` 覆盖；
数据库主机/库名/迁移角色名目前按生产约定固定为
`postgres:5432/mentorflow` 与 `mentorflow_migrator`。脚本使用一次性 `docker run` 加入
现有 internal 网络，不调用 Compose，因此 BUG-45 要求的 `network45.override.yml` 不适用于
该命令；所有 Compose 命令仍由 `java-compose.sh` 解析配置，并把 network45 override 放在
最后一层。

顺序：备份数据库 → `sh migrate.sh upgrade` 并确认前后版本 → 在一次性测试库运行
`sh rag-verify.sh`（不要对正式库运行验证脚本）→ 从本次 release 源码构建 Python fallback 镜像，重建
Python backend 与 OIDC proxy 并确认健康 → 构建并启动 Java → 比对探针 → 仅当 upstream 状态不是 Java 时再切换。
迁移向后兼容且只新增 RAG 表/清理逻辑；失败时先按数据库备份恢复，再按现有
Java 回退步骤恢复应用版本。迁移不是应用容器启动时自动执行。

## 准备与启动 Java

把发布源码放到服务器的 release 目录后，在本目录执行以下准备。`BACKEND_CONTAINER` 是当前 Python 容器名；truststore 目录使用现有 TLS 材料目录。`VERSION` 使用本次源码版本号。

```sh
cd /opt/mentorflow/releases/<release>/refactor/server-java/deploy
sh prepare-config.sh mentorflow-e1-preview-backend-1 /opt/mentorflow/state/java-shadow/trust
sh build.sh VERSION
```

`prepare-config.sh` 从保留的 Python 容器（即使已停止也可 inspect）读取其环境变量和挂载路径，并写出 `.env`、`.java.env` 与 `compose.generated.yml`。数据库密码只在容器启动时从既有 secrets 卷读取，不写入这些文件。检查生成文件属于本次预期后，先在当前运维 shell 指向现有 Compose `--env-file`：

当前 edge 已指向正式 Java，`java-compose.sh up` 会重建正在接流量的
`mentorflow-java-backend`（及其共享网络命名空间的 OIDC proxy），因此必须在架构师批准的
重启窗口内执行；Java 健康检查期间可能短暂不可用。数据库已迁移到当前源码的 0045，旧 MF-76
Python 镜像的 canonical migration head 是 0043，直接启动可能因版本不匹配拒绝服务。先从本次
release 根目录用现有 `refactor/server/Dockerfile` 构建一个独立 fallback tag，再只重建 Python
backend 与 OIDC proxy；这一步不改变 edge upstream，也不影响 MF-76 镜像或数据库：

```sh
cd /opt/mentorflow/releases/<release>
docker build -f refactor/server/Dockerfile -t mentorflow-slim-backend:MF-156-python-fallback .
cd refactor/server-java/deploy
sh python-compose.sh recreate
docker inspect --format '{{.State.Status}}' mentorflow-e1-preview-backend-1
docker inspect --format '{{.State.Status}}' mentorflow-e1-preview-oidc-loopback-proxy-1
```

`recreate` 使用静态 `compose.python-fallback.yml` 覆盖 backend image，保留容器标签里的所有
Compose 配置层，并把 network45 override 作为最后一层；只执行
`up -d --no-deps --force-recreate backend oidc-loopback-proxy`。确认 Python readiness 健康后再配置并启动 Java：

```sh
# 若 fallback 容器在 paired probe 前又停止，才运行这条启动已更新容器的命令：
sh python-compose.sh start
```

```sh
export BACKEND_ENV=/服务器上现有的/compose.env
sh java-compose.sh config
sh java-compose.sh up
```

如 fallback 容器之后停止，可在启动 Java 前用 `python-compose.sh start` 重启已更新的 backend
与 OIDC proxy；它沿用完整 Compose 文件组且不切 edge。迁移后不要用它启动旧 MF-76 backend。
不要用 `restore-python.sh` 做启动对照，因为它会将 edge 切回 Python。

Compose 运行容器的 labels 可用于确认项目名、配置文件组和工作目录：

```sh
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' mentorflow-e1-preview-backend-1
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' mentorflow-e1-preview-backend-1
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' mentorflow-e1-preview-backend-1
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.environment_file" }}' mentorflow-e1-preview-backend-1
```

`environment_file` 标签是否存在取决于 Compose 版本和创建方式；用它辅助查找，再从原运维命令或 shell 环境确认实际环境文件并设置 `BACKEND_ENV`，不要猜路径或打印文件内容。标签若列出多个路径，不要把逗号列表直接当成一个文件路径。`java-compose.sh` 会从容器 labels 保留全部当前配置层（含 session/MF overlays），去除已存在的 BUG-45 项后追加 `compose.yml`、`compose.generated.yml` 与 network override。旧 `BACKEND_ENV` 和 deploy `.env` 都会显式传给 Compose；BUG-45 必须是最后一个 `-f`。脚本只执行 `up -d --no-deps java-backend java-oidc`，不会启动或重建其它服务。

Java 服务没有 Docker `HEALTHCHECK`。先确认容器状态为 `running`，再用保留的 Python 镜像临时运行 readiness 与契约探针；若 Java 尚未就绪，探针会失败，等启动完成后重跑。这样探针在项目内部网络中执行，不依赖服务器主机能解析容器别名。

```sh
PYTHON_IMAGE=$(docker inspect --format '{{.Config.Image}}' mentorflow-e1-preview-backend-1)
docker inspect --format '{{.State.Status}}' mentorflow-java-backend
docker run --rm --network mentorflow-e1-preview_internal \
  -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python \
  "$PYTHON_IMAGE" /tmp/probe.py
```

它会检查 readiness，并对比 `http://java-backend:8080` 与 `http://backend:8000` 的未认证和无效令牌请求；别名不同时通过 `--java` / `--python` 传入地址。探针只读。仅当 readiness 返回 200 且响应契约全部匹配时继续。

## 切换与回退

将 `cutover.sh`、`probe.py` 与本 README 一起放在本次 deploy 目录。当前 edge 已指向正式
`java-backend:8080`。用 `sh cutover.sh status` 确认；若探针后仍显示 `java`，无需再切流。
只有状态不是 `java` 且两侧探针均通过时才运行 `cutover.sh java`，脚本会从支持的影子
Java 或 Python upstream 切到正式容器；若已经指向 Java 会报告已是目标状态：

```sh
edge_state=$(sh cutover.sh status)
printf 'Current edge upstream: %s\\n' "$edge_state"
if [ "$edge_state" != java ]; then sh cutover.sh java; fi
docker run --rm --network mentorflow-e1-preview_internal \
  -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python \
  "$PYTHON_IMAGE" /tmp/probe.py
```

脚本只接受唯一 upstream 块中的一个已知 server 行，先通过 Docker 在 edge 容器内运行 `nginx -t`，成功后 reload。它用 `cat > edge.conf` 写回原文件 inode，供现有 bind mount 继续引用。`nginx -t` 或 reload 失败时，脚本自动恢复原文件内容并尝试重新检查、加载旧配置。

需要回退时，先确认当前源码构建的 Python fallback backend 与 OIDC proxy 都为 `running`
且探针健康（按上面的 `python-compose.sh recreate` 与探针步骤启动/检查），再切回 Python：

```sh
sh cutover.sh python
docker run --rm --network mentorflow-e1-preview_internal \
  -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python \
  "$PYTHON_IMAGE" /tmp/probe.py
```

当前 Python 已停止。完成 Java upstream 和真实桌面客户端登录、同步、上课流程核验后，
如需停用为对比而启动的 Python，可选执行：

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

`probe.py` 不创建账号、不改数据、不发邮件。运行前先从保留的 Python 容器取得镜像名并保存到当前 shell 的 `PYTHON_IMAGE`，然后用该镜像启动一次性探针容器：

```sh
PYTHON_IMAGE=$(docker inspect --format '{{.Config.Image}}' mentorflow-e1-preview-backend-1)
docker run --rm --network mentorflow-e1-preview_internal \
  -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python \
  "$PYTHON_IMAGE" /tmp/probe.py --java-only
```

它比较 live/startup 和代表性认证、目录、同步响应，忽略 request ID、耗时和检查时间等动态字段。Python 与 Java 的 email readiness 都必须是 `ready=true, code=EMAIL_TLS_AUTH_READY`；`--java-only` 只请求 Java，不会 exec 到已停容器。native refresh 与 closure/prepare 探针携带 `X-Forwarded-Proto: https` 来模拟 edge 转发，避免先被 HTTPS 入口校验拒绝而掩盖身份契约；它们只送无效 token，不代表有效凭证的认证测试。探针要求 readiness 的 `status` 为 `ready`、`started` 为 true，`postgres` 与 `keycloak_discovery` 必须存在且 ready；无效 JSON 或字段也会失败。服务器特有的网络连通、容器健康、Nginx reload 和真实登录仍需在服务器验证；本机脚本测试不能替代这些检查。
