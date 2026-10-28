# 226账号资料：部署、核验与旧Java回退（237修订）

237已通过“直接换Java”分支上线：运行 `mentorflow-java-server:226`，JAR SHA256为 `39419dc9e31d7f60577547b2fb63aa9ee6395b556fd0ec08a61b96a7375a8077`；数据库0046保留。Python226备用镜像存在且停止，**备用启动仍待修，不能称可用**。用户已登录后的资料读写/头像上传由用户亲测后再核访问计数，不用无令牌401代替验收。

实际源码工具目录 `/opt/mentorflow/releases/java-profile-237/refactor/server-java/deploy`，任务证据 `/opt/mentorflow/state/release237`。已结束的job目录不得拿来再次上线。以后服务器操作仍按新的用户任务执行。

## 0. 从现状开始，不重复迁移

```sh
DEPLOY=/opt/mentorflow/releases/java-profile-237/refactor/server-java/deploy
STATE=/opt/mentorflow/state/release237
BACKUP=/opt/mentorflow/state/release226
BACKEND_ENV=/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env
schema=$(docker exec mentorflow-e1-preview-postgres-1 psql -U postgres -d mentorflow -Atc 'SELECT version_num FROM alembic_version')
test "$schema" = 0046_account_profiles
test "$(docker exec mentorflow-e1-preview-postgres-1 psql -U postgres -d mentorflow -Atc "SELECT to_regclass('public.account_profiles')")" = account_profiles
test -s "$BACKUP/mentorflow-before226.dump"
docker exec -i mentorflow-e1-preview-postgres-1 pg_restore --list < "$BACKUP/mentorflow-before226.dump" >/dev/null
```

0046已存在，跳过迁移，不downgrade、不恢复整库、不覆盖release226备份。Python219要求精确0045，不能作为0046的备用；Python226要求0046，版本门槛相符但不代表启动命令已可用。旧Java219没有对应Alembic门槛，234/236已实际回退，保留 `mentorflow-java-server:rollback226` 标签与原配置。

候选已构建，无需重建。核对Java226标签及实际JAR，Python226镜像ID应为 `sha256:2adb3d025759308a6f312cd25db0f804cc168bdf38ffaddc53003fe42549d83c`；不一致就停止核实。迁移工具仍保留原migrate.sh；本次不调用。

## 1. 长任务独立于SSH

既有job.sh用系统nohup启动命令，stdin为/dev/null，stdout/stderr写output.log，最后依次发布exit_code、done。只有done和退出码0才SUCCEEDED；UNKNOWN/MISSING/连接错误都不能推断部署失败。重复start同job-dir只查状态，不再次执行。

```powershell
& D:/MentorFlow/.venv/Scripts/python.exe refactor/server-java/deploy/remote-job.py --ssh-config D:/MentorFlow/.local/server/ssh_config --runner /opt/mentorflow/releases/java-profile-237/refactor/server-java/deploy/job.sh --job-dir /opt/mentorflow/state/release237/jobs/switch wait
```

这是查询已完成237任务的命令。新任务使用新的state/job目录，以 `start -- sh <动作脚本>` 启动；断线后对**同一个**目录wait。remote-job设置ServerAliveInterval=15、ServerAliveCountMax=6；返回0=远端成功、1=远端明确失败、2=仍未决。不得把SSH非零当作应用失败后再次切换。checked-step.sh只在服务器action真正非零时执行rollback，保留action原失败码并记录ROLLBACK_EXIT。

## 2. 备用的新方法与真实dry-run门槛

234成功基准是 `python-compose.sh recreate`，实际为 `up -d --no-deps --force-recreate backend oidc-loopback-proxy`，当时未设置PYTHON_COMPOSE_PROFILES。235仅增加profile默认值，236的裸 `compose start`仍报初始化依赖缺失。因此start分支现在复用 `up -d --no-deps backend oidc-loopback-proxy`，不强制无谓重建；recreate仍保留force-recreate。

所有Compose参数沿用backend容器labels确定的项目、项目目录、九层文件及BACKEND_ENV；account-core-mvp保持显式启用，network45最后。只明确协调backend/proxy或java-backend/java-oidc，不启动postgres、migrate或初始化服务。

先确认服务器Compose版本和 `--help` 的 `--dry-run`，同一wrapper提供 `COMPOSE_DRY_RUN=1`：

```sh
. /opt/mentorflow/state/release237/context.sh
COMPOSE_DRY_RUN=1 sh "$DEPLOY/python-compose.sh" start
COMPOSE_DRY_RUN=1 sh "$DEPLOY/python-compose.sh" stop
COMPOSE_DRY_RUN=1 sh "$DEPLOY/python-compose.sh" recreate
COMPOSE_DRY_RUN=1 MF_JAVA_VERSION=226 sh "$DEPLOY/java-compose.sh" up
COMPOSE_DRY_RUN=1 MF_JAVA_VERSION=rollback226 sh "$STATE/rollback-deploy/java-compose.sh" up
```

237服务器为Compose5.5.0，支持该参数。实际结果：Python start/recreate仍退出1，输出backend exited的健康依赖错误（不再是missing runtime-secrets-ready）；Python stop退出0；Java候选和旧Java回退均退出0，动作仅两个Java容器。未执行dry-run失败的备用启动，也未据dry-run失败断言备用镜像业务代码有错。

**config通过或假Docker成功不代替真实dry-run，更不代替实际healthy。** dry-run不能过的命令不执行；备用仍不行时按任务批准走直接Java，备用单独待修。依据：[Docker Compose dry-run](https://docs.docker.com/reference/cli/docker/compose/)、[up --no-deps](https://docs.docker.com/reference/cli/docker/compose/up/)。

## 3. 上线与回退分支

在新的源码工具目录运行prepare-config.sh，保留现有5个只读挂载、OIDC代理镜像/config、SESSION_TTL=0。版本号可由context的MF_JAVA_VERSION传入；复用候选时没有运行build.sh，不应假定.env里必有该行。准备步骤也放后台job。

回退配置先从release226的previous-java.env、previous-java-runtime.env、previous-compose.generated.yml与旧compose.yml复制到state/rollback-deploy，只将MF_JAVA_DEPLOY_DIR指向该副本，保留其余值。放入同一新java-compose.sh，让回退使用与dry-run相同的wrapper、配置和 `MF_JAVA_VERSION=rollback226`，不改原备份。

本次动作记录在 `state/switch.sh`、`rollback.sh`、`run-switch.sh`，它们的执行顺序如下：

1. Java候选与旧Java回退的dry-run均成功；备用若dry-run/实际healthy失败，记录原因选择Java-only，不能因此立即回退或阻挡头像上线。
2. 若备用验证成功，先恢复Python并probe；否则不执行备用start。执行 `java-compose.sh up`（仅两个Java容器）。
3. 等既有probe.py通过：带备用时两端对照，不带备用时 `--java-only`；核9/9、内部ready及email。再用cutover.sh指向Java（先nginx -t；已指向则不重复reload）。
4. 核公网 `/health/live`、`/health/startup`、`/health/ready`全部200、实际容器image ID和JAR sha256；最后执行已dry-run通过的stop-python.sh路径。
5. 只有服务器action真正失败才由checked-step执行旧Java回退：回退目录的java-compose up → Java-only probe → cutover Java → 三健康和镜像校验 → 停Python。保留0046。回退后停下交回，不在同一任务第三次上线。

不注册账号、不调用用户profile写接口，不打印令牌、配置秘密或资料内容。probe使用无账号/虚假令牌合同检查，不是用户读写验收。

## 4. 健康采样、日志计数和清理

从任何实际切换之前起独立后台采样job，到成功或回退恢复后的几秒结束；任务设最大时长，三健康并发请求，约0.5秒间隔，只保存UTC/请求开始结束/状态码，不保存响应体。逐路径报告坏样本段及最后成功请求开始到恢复成功请求结束的包围窗口；多路径不能相加。操作用时、SSH等待用时均不是停机时间。

edge关闭access_log；真正的安全访问格式来自Java RequestAccessLog149Filter（method/route/status），统计必须用现有access-counts.py：

```sh
docker logs --since 2026-10-28T21:59:45Z mentorflow-java-backend 2>&1 | python3 "$DEPLOY/access-counts.py"
```

时间参数改为要核验的实际窗口；只输出profile/avatar白名单的方法、路径、状态码和次数。237即时计数为空，不能声称已登录读写成功。用户用2.0.37打开个人资料后，由后续任务再核。

只有上线成功并核对完后，才删除任务指定的/tmp源码/actions tar，不删除运行源码/挂载、state日志、release226备份或rollback226标签。237已清理234、236、237的5个临时tar，共4,003,840B；保留本次任务日志、当前源码及旧Java回退配置。
