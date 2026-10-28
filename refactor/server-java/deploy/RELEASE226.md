# 226 账号资料功能部署与应用回退

状态：操作清单，**本次未上线、未连接服务器**。生产步骤须在此批合并并通过统一验收后按架构师任务执行。迁移新增 `0046_account_profiles`。该表含用户昵称、签名和头像，不得通过常规 downgrade 删除。

## 发布范围

- Java 新镜像须包含 `/api/enterprise/account/profile[ /avatar ]` 接口。
- Python fallback 必须同时包含 profile 接口，并接受 schema revision `0046_account_profiles`。当前旧 fallback 严格拒绝非 `0045_account_closure_rag`，**不可在迁移后作为备用容器启动**。
- 旧 Java 镜像的回退兼容依据：Java 启动路径没有读取或强制校验 `alembic_version` 的逻辑（源码检索 `refactor/server-java/src/main/java` 无 `0045`、`alembic_version` 或 revision gate）；本次迁移只新增 `account_profiles` 表及注销清理钩子，不改旧 Java 所用既有表。因此可回退 Java 应用镜像而保留 0046。此结论不适用于旧 Python fallback。
- 数据库 `mentorflow` 从 `0045_account_closure_rag` 升级到 `0046_account_profiles`；新增表由迁移接入账号关闭 guard、清理和完成检查。
- 头像落在现有 PostgreSQL backup 内。不新增卷、Keycloak realm 项或网络；Compose 操作继续通过本目录 `java-compose.sh` / `python-compose.sh` / `migrate.sh`，由其带上现行 network45 override。

## 0. 只读确认部署位置并备份

使用服务器现存 release 工作流；不猜部署目录、备份位置或密钥路径。先确认当前 backend 容器的名字仍是 `mentorflow-e1-preview-backend-1`，然后照 RELEASE219 第 1 节只读查看其 Compose project、environment file、config files 与 mounts，并查看 Java 容器标签。根据这些值和既有运维配置设置 `BACKEND_CONTAINER`、`BACKEND_ENV`、`OLD_DEPLOY`、`TRUST_DIR`；`TRUST_DIR` 必须是挂到 `/run/mentorflow/trust` 的主机目录。还要核对迁移 wrapper 实际读取的 migrator secret volume/path。值不能唯一确定时停止并报告；不要打印 `.env` 或 secret 内容。

```sh
set -eu
umask 077
STATE=/opt/mentorflow/state/release226
mkdir -p "$STATE"
test ! -e "$STATE/java-image-id"
BACKEND_CONTAINER=mentorflow-e1-preview-backend-1
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$BACKEND_CONTAINER"
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.environment_file" }}' "$BACKEND_CONTAINER"
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' "$BACKEND_CONTAINER"
docker inspect --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}' "$BACKEND_CONTAINER"
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' mentorflow-java-backend
docker inspect --format '{{.Image}}' mentorflow-java-backend > "$STATE/java-image-id"
docker image tag "$(cat "$STATE/java-image-id")" mentorflow-java-server:rollback226
```

After verifying the output, export the resolved host paths in the same shell; do not put secret values in shell history:

```sh
: "${BACKEND_ENV:?set to the verified current Compose environment file}"
: "${OLD_DEPLOY:?set to the verified current Java deploy directory}"
: "${TRUST_DIR:?set to the verified host directory mounted at /run/mentorflow/trust}"
test -f "$BACKEND_ENV"
test -f "$OLD_DEPLOY/.env"
test -d "$TRUST_DIR"
export BACKEND_ENV
cp "$OLD_DEPLOY/.env" "$STATE/previous-java.env"
cp "$OLD_DEPLOY/.java.env" "$STATE/previous-java-runtime.env"
cp "$OLD_DEPLOY/compose.generated.yml" "$STATE/previous-compose.generated.yml"
cp /opt/mentorflow/state/bug45-20261010/network45.override.yml "$STATE/network45.override.yml"
test ! -e "$STATE/mentorflow-before226.dump"
POSTGRES_CANDIDATES=$(docker ps --filter label=com.docker.compose.project=mentorflow-e1-preview \
  --filter label=com.docker.compose.service=postgres --format '{{.Names}}')
[ "$(printf '%s\n' "$POSTGRES_CANDIDATES" | sed '/^$/d' | wc -l)" -eq 1 ] || {
  echo 'Could not uniquely identify the production PostgreSQL container; stop and inspect Compose labels.' >&2
  exit 1
}
POSTGRES_CONTAINER=$POSTGRES_CANDIDATES
```

Verify the identified PostgreSQL container belongs to the production Compose
project and its image is the running DB image.
`pg_dump` runs inside that container, so the host needs no PostgreSQL client
and no guessed `MENTORFLOW_BACKUP_DATABASE_URL`. Check the tool and connection
first; if either fails, stop and use the already approved backup procedure.

```sh
docker inspect --format '{{.Config.Image}}' "$POSTGRES_CONTAINER"
docker exec "$POSTGRES_CONTAINER" pg_dump --version
docker exec "$POSTGRES_CONTAINER" pg_dump -U postgres -d mentorflow --schema-only >/dev/null
docker exec "$POSTGRES_CONTAINER" pg_dump -U postgres -d mentorflow -Fc \
  --no-owner --no-privileges > "$STATE/mentorflow-before226.dump"
test -s "$STATE/mentorflow-before226.dump"
docker exec -i "$POSTGRES_CONTAINER" pg_restore --list < "$STATE/mentorflow-before226.dump" \
  > "$STATE/mentorflow-before226.list"
chmod 600 "$STATE/mentorflow-before226.dump" "$STATE/mentorflow-before226.list"
sha256sum "$STATE/mentorflow-before226.dump" > "$STATE/mentorflow-before226.sha256"
```

Preserve current image tags, runtime env files,
generated Compose files and network45 override as in RELEASE219. **Do not
automatically restore this dump during application rollback.**

## 1. 构建候选版本与迁移工具

从已审定的源码 checkout 根目录执行。本批候选镜像 tag 固定为 `226`，不得改指旧 fallback。

```sh
set -eu
RELEASE_ROOT=$(pwd)
test -f "$RELEASE_ROOT/refactor/server/Dockerfile"
cd "$RELEASE_ROOT"
docker build -f refactor/server/Dockerfile \
  -t mentorflow-slim-backend:226-python-fallback .
cd "$RELEASE_ROOT/refactor/server-java/deploy"
sh prepare-config.sh "$BACKEND_CONTAINER" "$TRUST_DIR"
sh build.sh 226
sh java-compose.sh config
```

确认 `build.sh` 对 Java JAR 完成源包/最终镜像 SHA-256 一致校验；确认 Python image 中的应用代码和 schema helper 已识别 0046。现有 `python-compose.sh` 只支持 `start|stop|recreate`，没有 `config` 动作，不要调用不存在的命令。`java-compose.sh config` 可先校验 Java Compose 层；Python fallback 的 `recreate` 只在迁移后、应用变更获准时运行。使用现有 `migrate.sh` 构建一次性迁移工具镜像；它从本批 backend image 与 migration.Dockerfile 组合，不会自动升级数据库：

```sh
MIGRATION_IMAGE=mentorflow-slim-backend:226-python-fallback \
MIGRATION_TOOL_IMAGE=mentorflow-migration-tool:226 sh migrate.sh current
```

此时应报告 `0045_account_closure_rag`。若不是此 revision，停止并让架构师查清迁移状态；不要强行 downgrade。

## 2. 执行迁移并核实

```sh
MIGRATION_IMAGE=mentorflow-slim-backend:226-python-fallback \
MIGRATION_TOOL_IMAGE=mentorflow-migration-tool:226 sh migrate.sh upgrade
MIGRATION_IMAGE=mentorflow-slim-backend:226-python-fallback \
MIGRATION_TOOL_IMAGE=mentorflow-migration-tool:226 sh migrate.sh current
```

最后一条必须显示 `0046_account_profiles`。迁移包含新增 `account_profiles` 表、应用账号范围 RLS、注销服务删除和注销完成检查；不搬动已有资料、不改变课程数据。确认当前 Java 和新版 Python 都已构建完成且新版 Python fallback 能识别 0046 后，才继续应用切换。

如果迁移失败：不要继续重启/切流。保持现有 Java 服务运行，保存迁移日志供定位。迁移脚本本身在事务中失败时由 PostgreSQL 回滚当前迁移；先以 `sh migrate.sh current` 查询 DB 状态，再按架构师指定方案处理。不要把 dump 自动恢复当成普通失败回退。

## 3. 先准备新版 Python fallback，再切 Java

按 RELEASE219 第 2–3 节确认的配置准备 Python fallback，确保其 image tag 为 `mentorflow-slim-backend:226-python-fallback`，不能引用旧 0045 镜像。生成仅替换 backend image 的临时 override，再执行现有 `python-compose.sh recreate`（它本身会应用 overlay 并重建 backend 与共享网络命名空间的 OIDC proxy；没有独立 config 校验动作）：

```sh
cd "$RELEASE_ROOT/refactor/server-java/deploy"
printf 'services:\n  backend:\n    image: mentorflow-slim-backend:226-python-fallback\n' \
  > "$STATE/python226.override.yml"
export PYTHON_FALLBACK_OVERRIDE="$STATE/python226.override.yml"
sh python-compose.sh recreate
```

随后确认服务 healthy 并用发布批准的隔离探针验证：profile 空资料读取、profile 版本写入/读回、归属隔离、avatar GET 私有 ETag 与错误条件、closure 清理检查。测试身份和数据不得用真实账号；若现有探针未覆盖这些合同，先不切流，回报架构师补齐。

再按 RELEASE219 第 2–3 节准备 Java 新 image 与配置，通过 `sh java-compose.sh config` 校验后启动 Java 并执行已有 readiness/业务探针，以及与新版 Python 相同的账号资料 HTTP 合同。使用命令行，所有 Compose 命令仅经包装脚本，避免漏 network45 override。未得架构师明确上线指令时不得运行本节生产命令。

获得上线指令后，在 deploy 目录按以下顺序执行具体的 Java 启动、探测和切流命令。健康探针须在切流前全部通过；`probe.py --java-only` 不创建账号或写入资料：

```sh
cd "$RELEASE_ROOT/refactor/server-java/deploy"
sh java-compose.sh config
sh java-compose.sh up
docker inspect --format '{{.State.Status}}' mentorflow-java-backend
docker inspect --format '{{.State.Status}}' mentorflow-java-oidc
docker run --rm --network mentorflow-e1-preview_internal \
  -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python \
  mentorflow-slim-backend:226-python-fallback /tmp/probe.py --java-only
edge_state=$(sh cutover.sh status)
if [ "$edge_state" != java ]; then sh cutover.sh java; fi
curl --fail --silent --show-error https://89.45.45.28/health/live
curl --fail --silent --show-error https://89.45.45.28/health/startup
curl --fail --silent --show-error https://89.45.45.28/health/ready
```

随后用已批准的隔离账号执行 profile HTTP 合同探针（无工作区、版本 CAS、头像 ETag、跨账号隔离）；不要将生产用户资料作为探针。只有 Java 与外部健康检查、合同探针均通过，才宣布完成。

旧客户端可以继续使用既有登录、工作区、同步接口；新端点不存在时新客户端应按既定兼容行为显示原用户名首字母。新增资料表不影响原身份和角色字段。

## 4. 应用回退（保留 0046 与用户资料）

若新版 Java 有问题，先停止切流并查看健康检查/安全日志。将 Java 配置回到事先备份的旧 Java image/env/Compose，再经 `java-compose.sh config` 检查并 `java-compose.sh up`，按 RELEASE219 的探针验证，最后用 `cutover.sh` 恢复旧流量。数据库保持在 0046；**不要启动旧 0045 Python fallback**。若需要 Python 承载请求，只允许使用已在步骤 3 验证、兼容 0046 的新版 Python 镜像。

已切到 Java 后的明确回退命令如下。`OLD_DEPLOY` 与备份文件须在步骤 0 已核实并保留；226 Python fallback 须在步骤 3 验证过。现有 `restore-python.sh` 会启动备用端及代理、等待 healthy，成功后才切流，失败则不动 upstream。此处不能运行默认同时检查 Java 与 Python 的 probe，因为待回退的 Java 可能正处于故障状态：

```sh
cd "$RELEASE_ROOT/refactor/server-java/deploy"
export PYTHON_FALLBACK_OVERRIDE="$STATE/python226.override.yml"
sh restore-python.sh
cd "$OLD_DEPLOY"
cp "$STATE/previous-java.env" .env
sed 's/^MF_JAVA_VERSION=.*/MF_JAVA_VERSION=rollback226/' .env > "$STATE/rollback-java.env"
cat "$STATE/rollback-java.env" > .env
cp "$STATE/previous-java-runtime.env" .java.env
cp "$STATE/previous-compose.generated.yml" compose.generated.yml
sh java-compose.sh config
sh java-compose.sh up
cd "$RELEASE_ROOT/refactor/server-java/deploy"
docker run --rm --network mentorflow-e1-preview_internal \
  -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python \
  mentorflow-slim-backend:226-python-fallback /tmp/probe.py --java-only
sh cutover.sh java
```

`rollback226` 镜像标签须在步骤 0 由原 Java image ID 创建并确认存在；不得使用旧 Python `0045` 镜像。若 `cutover.sh python` 不符合恢复目标（例如 fallback 尚未健康），不要执行，改按维护流程保持服务并报告；恢复 Java upstream 的最终命令为 `sh cutover.sh java`。

迁移 0046 的 `downgrade()` 故意阻止删除 profile 表，避免用户数据损失。不要运行 `alembic downgrade 0045_account_closure_rag`。若整个服务无法在新 schema 上恢复，停止切流并报告；数据库 dump 仅用于经用户另行明确批准的数据恢复，整库恢复会丢失备份后新增的计划、进度、资料和账号资料。

回退完成标准：旧 Java health/readiness 和原登录/学习 API 恢复；网络仍使用 network45 override；profile 数据保留在 0046 表中；确认没有运行旧 Python fallback。回滚时间取决于容器启动与现有探针，本机不估生产停机秒数。

## 本机演练边界

本机没有 Docker，本次不安装 Docker、不伪称完成镜像或生产 Compose 演练。`tests/refactor/deploy226/test_account_profile226_deploy.py` 用 fake Docker 驱动真实 `java-compose.sh` 和 `migrate.sh`，检查 0045→0046 顺序、显式本批镜像 tag、secret mount 和 network45 override 最后加载；Java HTTP 合同测试使用本机隔离 PostgreSQL 和签名假身份服务。实际镜像构建、容器网络/TLS 探针仍需 Docker 隔离环境。真实生产迁移和上线另派任务、另行授权。
