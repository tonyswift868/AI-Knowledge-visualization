# 219 服务器修复部署清单

本清单仅作部署准备。本机验证使用 HTTP 替身和 fake Docker；没有连接服务器。
待全部合入、统一验收通过且获得上线指令后，在服务器命令行执行。复用本目录的
build / prepare-config / java-compose / python-compose / probe / cutover 工具，不另建切流机制。
源码包沿用 `git -c core.autocrlf=false archive` 导出，解包后先 `sh -n` 检查脚本，
确认 `.sh` 没有 CRLF（BUG-119）；不能把 Windows 工作区直接打包替代这一检查。

## 发布内容与回退边界

Java 修复会话认证加载刷新历史、重复注销检查与连接复用、初始化请求体读取无界；
Python 回退端同步修复适用的问题。双端清理没有受支持客户端写入的三类旧聚合类型。
不删除已有记录，不改变同步数据格式。没有数据库 schema 变更，不执行 upgrade 或 downgrade。
原生会话族和刷新令牌摘要仍在同一 PostgreSQL 表；重启后访问令牌需刷新，不要求重新密码登录。
Java 与 Python 不能分摊同一账号的受保护流量（访问令牌在各进程内存中）。

## 1. 只读定位并备份

在本次已审定、合入后的源码根目录执行；后文的 release 路径以当前目录为准。
如果旧部署目录、env 文件或 truststore 路径不能唯一确定，先查容器标签/挂载，不能猜。

```sh
set -eu
umask 077
RELEASE_ROOT=$(pwd)
test -f "$RELEASE_ROOT/refactor/server-java/deploy/build.sh"
DEPLOY="$RELEASE_ROOT/refactor/server-java/deploy"
STATE=/opt/mentorflow/state/release219
mkdir -p "$STATE"
docker inspect --format '{{.Image}}' mentorflow-java-backend > "$STATE/java-image-id"
docker inspect --format '{{.Image}}' mentorflow-e1-preview-backend-1 > "$STATE/python-image-id"
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.environment_file" }}' mentorflow-e1-preview-backend-1
docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' mentorflow-java-backend
docker inspect --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}' mentorflow-java-backend
```

根据上面输出和原运维命令设置 `BACKEND_ENV`（现有 Compose 环境文件）、
`OLD_DEPLOY`（旧 Java deploy 目录）、`TRUST_DIR`（挂载到 `/run/mentorflow/trust` 的源目录）。
标签无 environment_file 时查原运维命令，不输出环境文件/密钥内容。

```sh
: "${BACKEND_ENV:?set to the existing Compose env file}"
: "${OLD_DEPLOY:?set to the existing Java deploy directory}"
: "${TRUST_DIR:?set to the existing truststore source directory}"
test -f "$BACKEND_ENV"
test -f "$OLD_DEPLOY/.env"
test -d "$TRUST_DIR"
export BACKEND_ENV
cp "$OLD_DEPLOY/.env" "$STATE/previous-java.env"
cp "$OLD_DEPLOY/.java.env" "$STATE/previous-java-runtime.env"
cp "$OLD_DEPLOY/compose.generated.yml" "$STATE/previous-compose.generated.yml"
cp /opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf "$STATE/edge.conf"
docker image tag "$(cat "$STATE/java-image-id")" mentorflow-java-server:rollback219
docker image tag "$(cat "$STATE/python-image-id")" mentorflow-slim-backend:rollback219
cd "$DEPLOY"
sh cutover.sh status
```

预期 upstream 是 java。此时仍未改流量、未重启。不要覆盖这些备份后重做本节。

## 2. 构建两端并准备配置（不中断现有 Java）

```sh
cd "$RELEASE_ROOT"
docker build -f refactor/server/Dockerfile -t mentorflow-slim-backend:219-python-fallback .
cd "$DEPLOY"
sh prepare-config.sh mentorflow-e1-preview-backend-1 "$TRUST_DIR"
sh build.sh 219
printf 'services:\n  backend:\n    image: mentorflow-slim-backend:219-python-fallback\n' > "$STATE/python219.override.yml"
export PYTHON_FALLBACK_OVERRIDE="$STATE/python219.override.yml"
sh python-compose.sh recreate
sh java-compose.sh config
```

构建脚本检查源 JAR 和最终镜像 JAR 的 SHA-256 相等，输出 `Built ...:219`。
配置来源沿用保留的 Python 容器；与旧 Java 的运行配置逐项核实后再 up，特别是
`MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS=0`、身份/数据库/邮件/RAG 配置及只读挂载。
核实结果保存于 STATE，禁止把密钥内容贴到回执。
所有 Compose 操作由现有 wrapper 将 network45.override.yml 放在最后，
Python recreate 同时重建共享网络命名空间的 oidc-loopback-proxy。

## 3. 重启窗口、健康检查与切流

```sh
cd "$DEPLOY"
sh java-compose.sh up
docker inspect --format '{{.State.Status}}' mentorflow-java-backend
docker inspect --format '{{.State.Status}}' mentorflow-java-oidc
PYTHON_IMAGE=mentorflow-slim-backend:219-python-fallback
docker run --rm --network mentorflow-e1-preview_internal \
  -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python \
  "$PYTHON_IMAGE" /tmp/probe.py
edge_state=$(sh cutover.sh status)
if [ "$edge_state" != java ]; then sh cutover.sh java; fi
curl --fail --silent --show-error https://aiteacher.clannad.win/health/live
curl --fail --silent --show-error https://aiteacher.clannad.win/health/startup
curl --fail --silent --show-error https://aiteacher.clannad.win/health/ready
```

探针成功必须为 `9/9`、双方 READY true、email matches true；closure/prepare 和
native/refresh 均模拟 edge 的 HTTPS 转发头。无效令牌探针不代替真实登录。
若 Java 尚在启动，先看容器日志再重试探针；失败不能继续切流，按下一节回退。
up 同时处理 java-backend 和共享其网络的 java-oidc。当前 Java 正在接流量，期间会短暂停机；
构建在重启前完成，实际停机以 up 至 readiness 恢复计时，本机无法给出生产秒数。
上线后由用户核验已有会话刷新、同步、注销入口；不注册测试账号。

## 4. 失败回退

优先恢复原 Java 镜像，schema 未变无需数据库回退；保留本次源码/日志用于定位。
在本次部署目录，仅把镜像版本切为事前保留的 rollback219，运行配置回用旧目录：

```sh
cd "$OLD_DEPLOY"
cp "$STATE/previous-java.env" .env
sed 's/^MF_JAVA_VERSION=.*/MF_JAVA_VERSION=rollback219/' .env > "$STATE/rollback-java.env"
cat "$STATE/rollback-java.env" > .env
cp "$STATE/previous-java-runtime.env" .java.env
cp "$STATE/previous-compose.generated.yml" compose.generated.yml
sh java-compose.sh config
sh java-compose.sh up
cd "$DEPLOY"
docker run --rm --network mentorflow-e1-preview_internal \
  -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python \
  mentorflow-slim-backend:219-python-fallback /tmp/probe.py --java-only
sh cutover.sh java
```

若原 Java 也不可用，用当前 schema 兼容且已通过探针的 Python fallback：
`sh restore-python.sh` 会先启动 backend + oidc-loopback-proxy、等 healthy，之后才切到 Python。
若本次 Python 也异常，先按下面恢复原镜像，再 restore；不直接启动不兼容 schema 的老 MF-76。

```sh
cd "$DEPLOY"
printf 'services:\n  backend:\n    image: mentorflow-slim-backend:rollback219\n' > "$STATE/python-rollback219.override.yml"
export PYTHON_FALLBACK_OVERRIDE="$STATE/python-rollback219.override.yml"
sh python-compose.sh recreate
sh restore-python.sh
```

cutover.sh 在 nginx 检查/重载失败时自动原地恢复配置；不会替代应用健康回退。
回退后再次验证公网健康与用户会话。保存失败容器日志时使用权限 600 的 STATE 文件，
回执只摘错误码/状态，不贴令牌或密码。确认成功后可执行 `sh stop-python.sh`；该脚本只在
edge 指向 Java 时停 Python，不删除镜像、容器或数据。

## 本机演练入口

`tests/refactor/deploy219/test_server_deploy219.py` 使用现有151批 fake Docker，
检查构建校验、配置保密、完整 Compose 参数/network45 顺序、切流成功及失败原地回退，
另以本机 HTTP 替身复现 HTTPS 门禁条件。真实 Docker/容器网络/TLS/生产登录待上线授权后验证。
