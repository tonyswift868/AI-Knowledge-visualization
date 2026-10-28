# 226 账号资料功能部署与应用回退

状态：234-MF01 已获用户部署授权，源码固定从 `149b53ed` 以 `git -c core.autocrlf=false archive` 导出；解包后先检查全部 `.sh` 为 LF 并执行 `sh -n`，本机运行 `tests/refactor/deploy219/test_server_deploy219.py`。BUG-189、BUG-191 仅改应用代码，没有增加迁移，以下镜像标签仍沿用 `226`。实际执行结果以 234-MF01 回执为准。迁移新增 `0046_account_profiles`。该表含用户昵称、签名和头像，不得通过常规 downgrade 删除。

234 当前结果（2026-10-29）：0046 已迁移；候选 Java 226 和 Python 226 探针曾通过，但执行连接被远端关闭，按任务失败分支回退。最终 Java 为 `rollback226`（与上线前 219 同一镜像 ID），0046 和资料表保留，新 Python 226 备用已停止。`restore-python.sh` 的实际启动报 `backend-tls-ready is missing dependency runtime-secrets-ready`，因此不能把语法检查通过当成备用恢复可用；本次没有越界修改 Compose 依赖。按本节第 4 节的直接旧 Java 应用回退恢复后，探针 9/9、公网三健康检查 200。下一次执行前须另行解决备用启动配置；不要重跑本次迁移或覆盖本次备份。

只通过任务指定的 SSH 命令行执行；普通 SSH 用户无 Docker 权限时使用服务器已有的 `sudo -n`，不改 sudo/Docker 权限、不输入或输出密码。下面各步共用已核实的环境变量。

## 发布范围

- Java 新镜像须包含 `/api/enterprise/account/profile[ /avatar ]` 接口。
- Python fallback 必须同时包含 profile 接口，并接受 schema revision `0046_account_profiles`。当前旧 fallback 严格拒绝非 `0045_account_closure_rag`，**不可在迁移后作为备用容器启动**。
- 旧 Java 镜像的回退兼容依据：Java 启动路径没有读取或强制校验 `alembic_version` 的逻辑（源码检索 `refactor/server-java/src/main/java` 无 `0045`、`alembic_version` 或 revision gate）；本次迁移只新增 `account_profiles` 表及注销清理钩子，不改旧 Java 所用既有表。因此可回退 Java 应用镜像而保留 0046。此结论不适用于旧 Python fallback。
- 数据库 `mentorflow` 从 `0045_account_closure_rag` 升级到 `0046_account_profiles`；新增表由迁移接入账号关闭 guard、清理和完成检查。
- 头像落在现有 PostgreSQL backup 内。不新增卷、Keycloak realm 项或网络；Compose 操作继续通过本目录 `java-compose.sh` / `python-compose.sh` / `migrate.sh`，由其带上现行 network45 override。

## 0. 只读确认部署位置并备份

使用服务器现存 release 工作流；不猜部署目录、备份位置或密钥路径。先确认当前 backend 容器的名字仍是 `mentorflow-e1-preview-backend-1`，然后照 RELEASE219 第 1 节只读查看其 Compose project、environment file、config files 与 mounts，并查看 Java 容器标签。根据这些值和既有运维配置设置 `BACKEND_CONTAINER`、`BACKEND_ENV`、`OLD_DEPLOY`、`TRUST_DIR`；`TRUST_DIR` 必须是挂到 `/run/mentorflow/trust` 的主机目录。还要核对迁移 wrapper 实际读取的 migrator secret volume/path。值不能唯一确定时停止并报告；不要打印 `.env` 或 secret 内容。

234 只读定位确认：现有 Compose 的 `migrate_secrets:/run/secrets:ro`（基础配置第 189 行）对应卷 `mentorflow-e1-preview_migrate_secrets`，其中已有 `postgres_migrator_password`。脚本默认的 OIDC proxy 卷仅有 TLS 文件，不能照默认运行。以下全部迁移调用须沿用脚本已有覆盖参数（不新增、复制或修改凭据）：

```sh
export MIGRATION_SECRETS_VOLUME=mentorflow-e1-preview_migrate_secrets
export MF_MIGRATION_PASSWORD_FILE=/run/secrets/postgres_migrator_password
```

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
docker inspect --format '{{.Image}}' "$BACKEND_CONTAINER" > "$STATE/python-image-id"
docker image tag "$(cat "$STATE/python-image-id")" mentorflow-slim-backend:rollback226
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
cp /opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf "$STATE/edge.conf"
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

随后等待 Python 容器 healthy，运行现有 `probe.py` 对比两端（9/9、双方 READY true、email matches true）。234 不注册账号、不读取或改写生产用户资料。profile 读写、头像上传的登录后合同已有本机隔离测试；生产阶段只做无令牌端点认证状态检查，登录后的 200 留待用户使用后按路径/状态码统计，不把无令牌探针称为资料读写验收。

再按 RELEASE219 第 2–3 节准备 Java 新 image 与配置，通过 `sh java-compose.sh config` 校验后启动 Java 并执行已有 readiness/业务探针及无令牌 profile 认证检查。使用命令行，所有 Compose 命令仅经包装脚本，避免漏 network45 override。

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

核实正在运行的镜像标签和 JAR（SHA 与镜像标签相同）、数据库版本和表存在；只读元数据，不查询用户行：

```sh
docker inspect --format '{{.Config.Image}} {{.Image}}' mentorflow-java-backend
docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }} {{ index .Config.Labels "org.mentorflow.jar.sha256" }}' mentorflow-java-server:226
docker exec mentorflow-java-backend sha256sum /app/mentorflow-server.jar
docker exec "$POSTGRES_CONTAINER" psql -U postgres -d mentorflow -Atc "SELECT version_num FROM alembic_version; SELECT to_regclass('public.account_profiles');"
docker inspect --format '{{.Config.Image}} {{.State.Health.Status}}' "$BACKEND_CONTAINER"
sh -n restore-python.sh
sh stop-python.sh
docker inspect --format '{{.State.Status}}' "$BACKEND_CONTAINER"
```

只有两端探针和公网健康检查通过后才停备用；`stop-python.sh` 校验 upstream 为 Java，随后通过带 network45 的 wrapper 停 backend 与 OIDC proxy，保留容器及镜像供 `restore-python.sh` 启用。不实际执行回退来验收备用。启动 Java 前至 ready 恢复期间按秒采样三个公网 health 的状态码，记录不可用样本和时间精度；不把 Docker 命令耗时冒充真实不可用时长。登录后的资料读取/保存/头像上传只从访问日志汇总路径和状态码，不输出用户内容、账号或令牌。

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
