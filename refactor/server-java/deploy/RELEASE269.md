# Java 269 发布步骤：共享正文资料的独立删除

本清单用于 269-MF01 合入、相关测试通过并由架构师安排上线后执行。发布 Java 镜像 `mentorflow-java-server:269`，并先应用兼容性迁移 0047。应用回退到 Java 226 时保留 0047，不做 downgrade。Python 226 fallback 的启动检查仍硬编码 canonical schema 0046，迁移后不能在 0047 数据库启动；它也没有 RAG documents API，不能作为本次回退服务。本次应用回退目标只有 Java 226。需要 Python fallback 时，须另行重建为识别 0047 的版本并完成验证后再考虑。

## 发布边界与备份

先从审定提交导出并传输源码归档，保留 `refactor/server` 与 `refactor/server-java` 相对路径；解压到新目录 `/opt/mentorflow/releases/java-profile-269`。不得覆盖 237 或其它既有 release。确认 `refactor/server-java/deploy/build.sh`、`prepare-config.sh`、`java-compose.sh`、`probe.py` 在归档中。`network45.override.yml` 不在代码归档；沿用服务器已经安装的 `/opt/mentorflow/state/bug45-20261010/network45.override.yml`，检查它存在且不要覆盖。

```sh
set -eu
umask 077
RELEASE_ROOT=$(pwd)
DEPLOY="$RELEASE_ROOT/refactor/server-java/deploy"
STATE=/opt/mentorflow/state/release269
test "$RELEASE_ROOT" = /opt/mentorflow/releases/java-profile-269
test -f "$DEPLOY/build.sh"
test -f "$DEPLOY/java-compose.sh"
test -f /opt/mentorflow/state/bug45-20261010/network45.override.yml
mkdir -p "$STATE"

# 留下现有 Java 镜像标签作为可回退副本；226 原标签也继续保留。
docker image tag mentorflow-java-server:226 mentorflow-java-server:rollback269
docker inspect --format '{{.Image}}' mentorflow-java-backend > "$STATE/java-before-image-id"
docker inspect --format '{{.Id}}' mentorflow-java-backend > "$STATE/java-before-container-id"
docker inspect --format '{{.State.StartedAt}}' mentorflow-java-backend > "$STATE/java-before-started-at"

# 先只读确认当前库与迁移版本，再对业务库 mentorflow 备份；不要依赖容器默认库变量。
docker exec mentorflow-e1-preview-postgres-1 psql -XAt -U postgres -d mentorflow \
  -c "SELECT current_database()||':'||version_num FROM alembic_version"
# 预期为 mentorflow:0046_account_profiles，否则停止并查明版本。
test "$(docker exec mentorflow-e1-preview-postgres-1 psql -XAt -U postgres -d mentorflow \
  -c "SELECT current_database()||':'||version_num FROM alembic_version")" = "mentorflow:0046_account_profiles"
# pg_dump 输出逻辑归档到权限 600 的本地状态目录，不回显数据。
docker exec mentorflow-e1-preview-postgres-1 pg_dump -U postgres -d mentorflow -Fc \
  > "$STATE/mentorflow-before269.dump"
test -s "$STATE/mentorflow-before269.dump"
docker exec -i mentorflow-e1-preview-postgres-1 sh -ec \
  'pg_restore --list' < "$STATE/mentorflow-before269.dump" > "$STATE/backup-contents.txt"
test -s "$STATE/backup-contents.txt"

# 留存运行配置和当前 Compose 状态供回退核对；禁止把 env 内容写入回执。
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' mentorflow-java-backend > "$STATE/compose-project"
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' mentorflow-java-backend > "$STATE/compose-files"
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' mentorflow-java-backend > "$STATE/compose-working-dir"
```

确认当前库和 Alembic 版本为预期值、备份非空且 `pg_restore --list` 成功后继续。若查询结果不符，停止并核实实际数据库；不要猜库名或账号。0047 只新增资料分块清单表，应用回退不需要恢复数据库；保留表可让新旧应用共用 schema。

## 构建并检查 269

在包含 `refactor/server-java` 的本次源码根目录运行现有构建流程。当前部署环境文件已记录为 `/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env`；验证文件存在后使用。新版本只换 Java 镜像，不能从停止的 Python fallback 重生成 Java 环境。应从当前 Java Compose labels 找到旧 deploy 目录，并复制它生成的运行配置。

```sh
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.environment_file" }}' mentorflow-e1-preview-backend-1
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' mentorflow-java-backend
```

从返回的逗号分隔配置路径里找现有 `refactor/server-java/deploy/compose.yml`，设为 `OLD_DEPLOY`，并复制生成配置。以下代码会验证单个路径匹配，不打印运行环境内容：

```sh
export BACKEND_ENV=/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env
test -f "$BACKEND_ENV"
JAVA_CONFIG_FILES=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' mentorflow-java-backend)
OLD_DEPLOY=
OLD_DEPLOY_MATCHES=0
old_ifs=$IFS
IFS=,
set -f
for config_file in $JAVA_CONFIG_FILES; do
  case "$config_file" in */refactor/server-java/deploy/compose.yml)
    OLD_DEPLOY=${config_file%/compose.yml}
    OLD_DEPLOY_MATCHES=$((OLD_DEPLOY_MATCHES + 1))
    ;;
  esac
done
set +f
IFS=$old_ifs
test "$OLD_DEPLOY_MATCHES" -eq 1
test -n "$OLD_DEPLOY"
test -f "$OLD_DEPLOY/.env"
test -f "$OLD_DEPLOY/.java.env"
test -f "$OLD_DEPLOY/compose.generated.yml"
cp "$OLD_DEPLOY/.env" "$DEPLOY/.env"
cp "$OLD_DEPLOY/.java.env" "$DEPLOY/.java.env"
cp "$OLD_DEPLOY/compose.generated.yml" "$DEPLOY/compose.generated.yml"
sed "s#^MF_JAVA_DEPLOY_DIR=.*#MF_JAVA_DEPLOY_DIR=$DEPLOY#" "$DEPLOY/.env" > "$STATE/269.env"
cat "$STATE/269.env" > "$DEPLOY/.env"
```

先在备份成功后执行迁移 `0047_rag_promoted_manifests`。现有 `migrate.sh` 会以 `mentorflow-slim-backend:226-python-fallback` 作为构建依赖的基础镜像，并挂载本次发布包里的新 `refactor/server` Alembic 源码，构建一次性迁移工具镜像；该 Python 镜像仅用于工具构建，绝不能在 0047 库上作为应用启动。脚本打印升级前后 revision。升级前必须是 `0046_account_profiles`，升级后必须是 `0047_rag_promoted_manifests`；否则停止。该迁移只新增 `rag_document_upload_manifests`，用账号范围和文档 ID 保存被提升 alias 自己的分块数及摘要，带有账号 RLS 和指向 rag_documents 的级联外键；不改现有资料、分段或向量行。

```sh
cd "$DEPLOY"
export MIGRATION_IMAGE=mentorflow-slim-backend:226-python-fallback
export MIGRATION_TOOL_IMAGE=mentorflow-migration-tool:MF-269
export MIGRATION_NETWORK=mentorflow-e1-preview_internal
docker image inspect "$MIGRATION_IMAGE" >/dev/null
sh migrate.sh current
sh migrate.sh upgrade
sh migrate.sh current
sh build.sh 269
```

`build.sh` 构建 `mentorflow-java-server:269`，核对镜像内 JAR 与构建 JAR 的 SHA-256，并更新该发布目录 `.env` 中的 `MF_JAVA_VERSION=269`。审阅生成的 Compose 配置，确认既有运行参数、挂载、`MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS=0` 及 BUG-45 网络配置仍在；不要打印含秘密的环境值。

## 重建与健康检查

执行前确认线上入口仍指向 Java，并确认旧镜像标签 `mentorflow-java-server:226` 存在。现有 `java-compose.sh` 从正在运行的 backend labels 还原完整 Compose 文件组，并将 `/opt/mentorflow/state/bug45-20261010/network45.override.yml` 作为最后一个 `-f` 文件。它只请求 Java 服务，不启动 Python 或执行 remove-orphans。

```sh
cd "$DEPLOY"
sh cutover.sh status
sh java-compose.sh config
sh java-compose.sh up
docker inspect --format '{{.State.Status}}' mentorflow-java-backend
docker inspect --format '{{.State.Status}}' mentorflow-java-oidc
```

`java-compose.sh up` 请求 `java-backend` 与 `java-oidc`。OIDC loopback proxy 使用 `network_mode: service:java-backend`，所以必须核对它是否已随 backend 使用新的网络命名空间；不要假设无需重建。记录 backend 容器 ID，并验证 OIDC 的 network mode 精确指向它：

```sh
BACKEND_ID=$(docker inspect --format '{{.Id}}' mentorflow-java-backend)
OIDC_MODE=$(docker inspect --format '{{.HostConfig.NetworkMode}}' mentorflow-java-oidc)
printf 'backend=%s oidc-network=%s\n' "$BACKEND_ID" "$OIDC_MODE"
test "$OIDC_MODE" = "container:$BACKEND_ID"
```

如果该检查失败，先停止后续验证；移除无状态的旧 OIDC proxy 容器，再通过现有 wrapper 按 Compose 定义重建它：

```sh
docker rm -f mentorflow-java-oidc
sh java-compose.sh up
BACKEND_ID=$(docker inspect --format '{{.Id}}' mentorflow-java-backend)
OIDC_MODE=$(docker inspect --format '{{.HostConfig.NetworkMode}}' mentorflow-java-oidc)
test "$OIDC_MODE" = "container:$BACKEND_ID"
```

会话和刷新令牌摘要存于 PostgreSQL；短期访问令牌不持久化，backend 重启后客户端可能需要用刷新令牌换取新访问令牌，但不要求用户重新输入密码。

Java 容器没有 Docker `HEALTHCHECK`。容器 running 不是就绪条件。最多尝试 20 轮，每轮最多约 10 秒（`probe.py --timeout 1` 的 9 个契约请求及 readiness 请求），轮间等待 5 秒。只有 `probe.py --java-only --timeout 1` 返回 0（9 个契约状态符合且 readiness ready）才通过；达到 20 轮仍失败即停止并回退。

```sh
PYTHON_IMAGE=$(docker inspect --format '{{.Config.Image}}' mentorflow-e1-preview-backend-1)
ready=0
for attempt in $(seq 1 20); do
  if docker run --rm --network mentorflow-e1-preview_internal \
    -v "$DEPLOY/probe.py:/tmp/probe.py:ro" --entrypoint python \
    "$PYTHON_IMAGE" /tmp/probe.py --java-only --timeout 1 > "$STATE/probe269.log" 2>&1; then
    cat "$STATE/probe269.log"
    ready=1
    break
  fi
  sleep 5
done
test "$ready" -eq 1 || { cat "$STATE/probe269.log"; exit 1; }
# 仅请求无凭据健康 GET；此主机检查沿用既有 curl -k 发布核验方式。
curl -k --fail --silent --show-error https://89.45.45.28/health/live
curl -k --fail --silent --show-error https://89.45.45.28/health/startup
curl -k --fail --silent --show-error https://89.45.45.28/health/ready
```

验证 269 删除接口行为时使用专用测试账号和合成资料，按另行授权的验收步骤执行；不要用用户账号或用户资料。该验证不属于本部署步骤的自动化命令。

## 回退到 Java 226

失败时保留 269 容器日志及状态，然后将发布目录配置切回 226 并用同一 Compose wrapper 重建 Java pair。**不要 downgrade 0047，也不要从备份恢复数据库作为常规应用回退**；否则会丢掉上线后的数据。0047 是加表且 Java 226 可启动时可留在库里。

Java 226 不实现新的逐份删除语义。若 269 已把共享内容的一份别名提升为主文档，226 的旧 DELETE 仍可能连同其它 alias 一起清理。回退 226 后，必须先让新客户端停止提供资料删除操作或回退客户端版本，并告知用户暂停删除资料；待 Java 269 恢复后再开放删除。恢复版本前不可声称资料删除兼容。0047 的 manifest 表不能令 226 获得新删除语义；因此这里是明确的回退限制，不可省略。

```sh
cd "$DEPLOY"
sed 's/^MF_JAVA_VERSION=.*/MF_JAVA_VERSION=226/' .env > "$STATE/rollback.env"
cat "$STATE/rollback.env" > .env
sh java-compose.sh config
sh java-compose.sh up
docker inspect --format '{{.State.Status}}' mentorflow-java-backend
docker inspect --format '{{.State.Status}}' mentorflow-java-oidc
BACKEND_ID=$(docker inspect --format '{{.Id}}' mentorflow-java-backend)
OIDC_MODE=$(docker inspect --format '{{.HostConfig.NetworkMode}}' mentorflow-java-oidc)
if [ "$OIDC_MODE" != "container:$BACKEND_ID" ]; then
  docker rm -f mentorflow-java-oidc
  sh java-compose.sh up
  BACKEND_ID=$(docker inspect --format '{{.Id}}' mentorflow-java-backend)
  OIDC_MODE=$(docker inspect --format '{{.HostConfig.NetworkMode}}' mentorflow-java-oidc)
  test "$OIDC_MODE" = "container:$BACKEND_ID"
fi
PYTHON_IMAGE=$(docker inspect --format '{{.Config.Image}}' mentorflow-e1-preview-backend-1)
ready=0
for attempt in $(seq 1 20); do
  if docker run --rm --network mentorflow-e1-preview_internal \
    -v "$DEPLOY/probe.py:/tmp/probe.py:ro" --entrypoint python \
    "$PYTHON_IMAGE" /tmp/probe.py --java-only --timeout 1 > "$STATE/rollback-probe.log" 2>&1; then
    cat "$STATE/rollback-probe.log"
    ready=1
    break
  fi
  sleep 5
done
test "$ready" -eq 1 || { cat "$STATE/rollback-probe.log"; exit 1; }
curl -k --fail --silent --show-error https://89.45.45.28/health/live
curl -k --fail --silent --show-error https://89.45.45.28/health/startup
curl -k --fail --silent --show-error https://89.45.45.28/health/ready
```

`mentorflow-java-server:226` 原标签与 `rollback269` 均应指向原镜像；如镜像标签或生成配置与预期不同，停止，不运行清理或回退猜测。数据库备份保留在 `$STATE/mentorflow-before269.dump`；只有发现独立数据损坏并由架构师另行确认恢复方案时才考虑恢复，常规应用回退不恢复数据库。

## 演练边界

本机发布演练须检查脚本 shell 语法、构建调用和完整 Compose 参数，重点确认 `network45.override.yml` 是最后一层；本机无生产网络时停在任何连接服务器的动作之前。脚本/Compose 假替身通过只证明命令组装与失败路径，不证明真实 Docker 镜像可启动、线上健康或正式登录。
