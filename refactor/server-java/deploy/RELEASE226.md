# 226 账号资料功能：部署及应用回退（235 修订）

235 只读调查服务器、修改本地脚本并本机演练，**没有重新部署或起停容器**。后续执行须有新的上线任务。234 留下的当前状态：Java 是 `rollback226`（与旧219同一镜像），数据库已为 `0046_account_profiles`，Python226备用停止；候选 Java226/Python226及本次备份保留。新流程不把 SSH 退出码当部署结果。

## 0. 只读定位与已有 0046

通过 `ssh -F "D:/MentorFlow/.local/server/ssh_config" -o ServerAliveInterval=15 -o ServerAliveCountMax=6 mentorflow`，使用现有 `sudo -n`，不改权限。先核对容器 labels/mounts 和以下路径，不打印 env/secret 内容。234 证据的实际时间为 **2026-10-28 20:32–20:38 UTC**（客户端日期10月29日）。

```sh
BACKEND_CONTAINER=mentorflow-e1-preview-backend-1
POSTGRES_CONTAINER=mentorflow-e1-preview-postgres-1
BACKUP=/opt/mentorflow/state/release226
OLD_DEPLOY=/opt/mentorflow/releases/java-70c282ec-222/refactor/server-java/deploy
BACKEND_ENV=/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env
TRUST_DIR=/opt/mentorflow/state/java-shadow/trust
schema=$(docker exec "$POSTGRES_CONTAINER" psql -U postgres -d mentorflow -Atc 'SELECT version_num FROM alembic_version')
case "$schema" in
  0046_account_profiles)
    test "$(docker exec "$POSTGRES_CONTAINER" psql -U postgres -d mentorflow -Atc "SELECT to_regclass('public.account_profiles')")" = account_profiles
    echo '0046 already present; skip migration, retain profile data' ;;
  0045_account_closure_rag) echo 'Requires the approved backup/migration branch below' ;;
  *) echo 'Unexpected schema; stop without downgrade' >&2; exit 1 ;;
esac
docker image inspect --format '{{.Id}} {{.Size}}' mentorflow-java-server:226 mentorflow-slim-backend:226-python-fallback mentorflow-java-server:rollback226
docker image inspect --format '{{ index .Config.Labels "org.mentorflow.jar.sha256" }}' mentorflow-java-server:226
test -s "$BACKUP/mentorflow-before226.dump"
docker exec -i "$POSTGRES_CONTAINER" pg_restore --list < "$BACKUP/mentorflow-before226.dump" >/dev/null
```

当前必须走已0046分支。候选Java JAR应为 `39419dc9e31d7f60577547b2fb63aa9ee6395b556fd0ec08a61b96a7375a8077`，与234构建记录一致。直接复用234的候选镜像、备份和已完成的0046；**不覆盖备份、不重建旧tag、不再迁移、不降级**。

仅尚处0045且另有明确迁移授权的环境才需要：先按旧流程保留镜像tag、env/Compose/edge配置；在新的独立备份目录做 `pg_dump -U postgres -d mentorflow -Fc --no-owner --no-privileges`，`pg_restore --list` 核对成功后运行既有 migrate.sh upgrade。迁移脚本所需现存卷是 `mentorflow-e1-preview_migrate_secrets`，路径 `/run/secrets/postgres_migrator_password`；默认OIDC卷仅有TLS文件，不能用app密码代替。迁移依旧是0046，不因BUG-189/191新增迁移。

## 1. 可靠执行：短连接启动，后台完成后查询

现有工具不能记录跨SSH会话的最终结果，因此只加最薄的一层：`job.sh` 用系统 `nohup` 脱离连接（stdin `/dev/null`，stdout/stderr均到日志），不改 build/Compose/migrate 的行为。任务目录使用原子mkdir认领，重复start同目录只查状态，不重新部署。

服务器调用：

```sh
sh /path/to/deploy/job.sh start /path/to/state/jobs/build sh /path/to/deploy/build.sh 226
sh /path/to/deploy/job.sh status /path/to/state/jobs/build
```

每项有 `output.log`、`pid`、`started`、`exit_code`、最后写入的 `done`。只有同时有done和退出码0才是 `SUCCEEDED 0`；命令确实非零才是 `FAILED N`。RUNNING、UNKNOWN、MISSING或SSH故障都不是失败，不可回退。UNKNOWN可能是任务未启动、机器重启或被强杀，先核实，不自行重新执行。状态查询本身成功不代表任务成功。

本机统一用 `remote-job.py`（标准库subprocess调用既有ssh）：

```powershell
& D:/MentorFlow/.venv/Scripts/python.exe refactor/server-java/deploy/remote-job.py --ssh-config D:/MentorFlow/.local/server/ssh_config --runner /opt/mentorflow/releases/java-profile-retry/refactor/server-java/deploy/job.sh --job-dir /opt/mentorflow/state/release226-retry/jobs/switch start -- sh /opt/mentorflow/releases/java-profile-retry/refactor/server-java/deploy/checked-step.sh /opt/mentorflow/state/release226-retry/switch.sh /opt/mentorflow/state/release226-retry/rollback.sh
```

中断本机SSH/轮询或关掉本机脚本后，原命令保留同一 `--job-dir`，把 `start -- ...` 改为 `wait`即可重连。客户端自动带 ServerAliveInterval=15 / ServerAliveCountMax=6；SSH断开会重试查询，不调用回滚。返回0=远端成功，1=远端明确失败，2=仍未决/等待到期；即使本机返回1，也只读取远端回退结果，不能再重复回滚。

`checked-step.sh ACTION_SCRIPT ROLLBACK_SCRIPT` 在**服务器后台job内**执行：仅action真实非零才运行rollback；输出ACTION_EXIT/ROLLBACK_EXIT。回退成功也保留原失败退出码，不能掩盖失败。构建、迁移、应用切换与必要回退全部放job内；不要再把长命令通过 `ssh sh -s` 前台执行，更不要在本机 `ssh != 0` 分支里回滚。

官方依据：[GNU nohup](https://www.gnu.org/software/coreutils/manual/html_node/nohup-invocation.html)、[OpenSSH ServerAlive](https://man.openbsd.org/ssh_config)。保活只检测失联，后台进程与结果标记才解决断线后判断问题。没有新增库、常驻守护进程或部署平台。

## 2. 下一次重上的准备（获得上线任务后）

1. 从已审定的含235脚本提交用 `git -c core.autocrlf=false archive` 导出 server/server-java；放到新的 `/opt/mentorflow/releases/java-profile-retry`，不要覆盖234源码和配置。解包检查shell LF与 `sh -n`。用该目录的新job/Compose/统计工具；业务镜像仍复用已验证的226。
2. 新工作目录 `/opt/mentorflow/state/release226-retry`，不复用已完成job目录、不覆盖234备份。在该目录准备以下context、switch、rollback脚本（0600）。动作脚本保存在服务器，job启动后不再依赖本机连接。
3. 新deploy先执行 prepare-config.sh，确认挂载/TTL=0；指定MF_JAVA_VERSION=226。prepare-config、config检查可作为一个短job。若候选不存在/哈希不符，先停止核查；确需重建时Python docker build和Java build.sh也各自通过job启动，当前重试无需构建。

`context.sh` 只含路径/标签，不含密码：

```sh
export DEPLOY=/opt/mentorflow/releases/java-profile-retry/refactor/server-java/deploy
export STATE=/opt/mentorflow/state/release226-retry
export BACKUP=/opt/mentorflow/state/release226
export OLD_DEPLOY=/opt/mentorflow/releases/java-70c282ec-222/refactor/server-java/deploy
export BACKEND_ENV=/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env
export PYTHON_FALLBACK_OVERRIDE=/opt/mentorflow/state/release226/python226.override.yml
export MF_JAVA_VERSION=226
export PYTHON_COMPOSE_PROFILES=account-core-mvp
export JAVA_COMPOSE_PROFILES=account-core-mvp
export MIGRATION_IMAGE=mentorflow-slim-backend:226-python-fallback
export MIGRATION_TOOL_IMAGE=mentorflow-migration-tool:226
export MIGRATION_SECRETS_VOLUME=mentorflow-e1-preview_migrate_secrets
export MF_MIGRATION_PASSWORD_FILE=/run/secrets/postgres_migrator_password
```

准备job的动作文件：

```sh
set -eu
. /opt/mentorflow/state/release226-retry/context.sh
cd "$DEPLOY"
sh prepare-config.sh mentorflow-e1-preview-backend-1 /opt/mentorflow/state/java-shadow/trust
sh java-compose.sh config
```

Python219的 canonical revision 是0045，orm.py:46–57严格要求相等，在0046上不能启动；Python226要求0046，与当前数据库相符（仍需运行健康检查确认其他依赖）。旧Java无revision gate，可保留0046回退应用。**绝不恢复旧Python219备用**。

## 3. 切换动作与核验

切换脚本 source context 后，以下顺序全在同一个后台job内，用 `checked-step.sh` 包住该脚本。以下命令组成switch.sh；不要直接在本机逐条通过短SSH连接执行长步骤。

```sh
set -eu
. /opt/mentorflow/state/release226-retry/context.sh
cd "$DEPLOY"
sh python-compose.sh start
healthy=no
for attempt in $(seq 1 45); do
  if [ "$(docker inspect --format '{{.State.Health.Status}}' mentorflow-e1-preview-backend-1)" = healthy ]; then healthy=yes; break; fi
  sleep 2
done
[ "$healthy" = yes ]
docker run --rm --network mentorflow-e1-preview_internal -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python mentorflow-slim-backend:226-python-fallback /tmp/probe.py
sh java-compose.sh config
sh java-compose.sh up
ready=no
for attempt in $(seq 1 45); do
  if docker run --rm --network mentorflow-e1-preview_internal -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python mentorflow-slim-backend:226-python-fallback /tmp/probe.py > "$STATE/probe.log" 2>&1; then ready=yes; break; fi
  sleep 2
done
[ "$ready" = yes ]
cat "$STATE/probe.log"
test "$(sh cutover.sh status)" = java || sh cutover.sh java
for endpoint in live startup ready; do curl --fail --silent --show-error --max-time 10 --output /dev/null --write-out "$endpoint %{http_code}\n" "https://89.45.45.28/health/$endpoint"; done
expected=$(docker image inspect --format '{{index .Config.Labels "org.mentorflow.jar.sha256"}}' mentorflow-java-server:226)
actual=$(docker exec mentorflow-java-backend sha256sum /app/mentorflow-server.jar | awk '{print $1}')
test "$expected" = "$actual"
sh stop-python.sh
```

`python-compose.sh` 默认启用account-core-mvp，因为线上backend→backend-tls-ready→runtime-secrets-ready都在此profile；只显式target backend不会激活所有同profile辅助服务。start只启动既有backend/proxy，不运行上游初始化任务；network45仍最后加载，调用者可覆盖profile列表。该修法由 [Docker profiles说明](https://docs.docker.com/compose/how-tos/profiles/) 与实际脱敏依赖图支撑。

预先另起一个后台观测job：从Java up前连续到最终成功/回退恢复后，对API入口三个health按约0.5–1秒采样（每次带超时），保存时间和状态码，不取响应内容；观测有最大时长并在两个结局都停止。234首次ready异常样本跨度10.085秒、成功样本包围11.201秒，仅作过去观测，**不承诺下一次停机时长**。下次将上线及回退两个窗口都纳入采样，不能把SSH等待时间当不可用秒数。

不注册账号、不调用用户profile写接口。probe是无账号合同检查（9/9、双方ready、email），认证后的资料/头像200必须等待用户使用后看统计。

## 4. 远端应用回退（rollback.sh）

只由服务器checked-step的实际失败触发，或后续明确人工回退任务触发。旧Java镜像tag/配置从234备份恢复，数据库始终保留0046；不覆盖表，不自动pg_restore整库，不downgrade。

```sh
set -eu
. /opt/mentorflow/state/release226-retry/context.sh
cd "$DEPLOY"
sh restore-python.sh
cd "$OLD_DEPLOY"
cp "$BACKUP/previous-java.env" .env
sed 's/^MF_JAVA_VERSION=.*/MF_JAVA_VERSION=rollback226/' .env > "$STATE/rollback-java.env"
cat "$STATE/rollback-java.env" > .env
cp "$BACKUP/previous-java-runtime.env" .java.env
cp "$BACKUP/previous-compose.generated.yml" compose.generated.yml
export MF_JAVA_VERSION=rollback226
sh java-compose.sh config
sh java-compose.sh up
cd "$DEPLOY"
ready=no
for attempt in $(seq 1 45); do
  if docker run --rm --network mentorflow-e1-preview_internal -v "$PWD/probe.py:/tmp/probe.py:ro" --entrypoint python mentorflow-slim-backend:226-python-fallback /tmp/probe.py --java-only > "$STATE/rollback-probe.log" 2>&1; then ready=yes; break; fi
  sleep 2
done
[ "$ready" = yes ]
cat "$STATE/rollback-probe.log"
sh cutover.sh java
for endpoint in live startup ready; do curl --fail --silent --show-error --max-time 10 --output /dev/null --write-out "$endpoint %{http_code}\n" "https://89.45.45.28/health/$endpoint"; done
sh stop-python.sh
```

Python恢复实际失败时不得称备用可用；按已批准的直接旧Java回退路径恢复，不临时改数据库、账号或Compose机制。远端任务done及退出码、日志内ROLLBACK_EXIT和最终health/probe共同决定结果，客户端重连不重复触发回退。

## 5. 只输出访问统计、交付及清理

edge配置 `access_log off`，Docker edge日志是错误日志，不能拿combined格式去解析。实际入口是Java `RequestAccessLog149Filter`，固定格式 `http method=... route=... status=... elapsed_ms=...`。只通过Docker日志接口流式读取，不输出原始行：

```sh
docker logs --since 2026-10-29T00:00:00Z mentorflow-java-backend 2>&1 | python3 "$DEPLOY/access-counts.py"
```

`--since` 设为本次实际执行的UTC起始时间，不照抄示例日期。脚本仅输出profile/avatar白名单的方法、路径、状态码和计数，不输出查询串、邮箱、令牌、IP或正文；空计数不能证明无人请求，也不能算已登录读写验证通过。

本次后续上线成功并完成证据归档后，才在服务器核实路径并删除 `/tmp/mentorflow-source-149b53ed-234.tar`（234的1,935,360字节上传包）；源码根、运行挂载配置、备份及候选/回退镜像仍保留。235只读任务不执行这一步。

## 本机验证边界

沿用226/219的fake Docker和Git自带shell，不安装Docker、不假称验证真实容器生命周期。deploy235使用线上九层配置的脱敏profiles/depends_on闭包复现缺profile；真实shell wrapper顺序、后台任务脱离被杀本地轮询、重连识别结果、服务端检查失败才回退及隐私计数均由离线测试覆盖。下一次生产任务仍须核验实际容器healthy、probe和认证后用户访问。
