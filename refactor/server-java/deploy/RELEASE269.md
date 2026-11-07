# Java 269 维护与回退清单

## 第一步：只读上线前检查

**先运行 `sudo -n sh preflight.sh`（已在root shell时用 `sh preflight.sh`），全部 OK 且退出0后才允许以下写操作。** 可用本机审阅版本通过`sudo -n sh -s`标准输入检查，不上传检查脚本。检查对象、默认参数、必须文件及历史现场差异统一在[README「服务器现场条件」](README.md#服务器现场条件)；不再从旧批次清单猜路径。任何FAIL先核实，不改凭据、网络或卷硬凑。

当前Java269与数据库0047已经上线。本文件用于另行获准后的269同源重建、备用准备或紧急应用回退，不是自动重做已完成部署的任务；上线事实和验证边界见[ARCH-09](../../../docs/project/CURRENT_STATE.md#arch-09-服务器后端逐块改用-java)。不重新迁回0046，也不覆盖旧发布目录或备份。

本清单使用审定的269同源0047归档。源码包含0048时，按[README「0048发布的源码清单与迁移阶段」](README.md#0048发布的源码清单与迁移阶段)分别准备15项清单、迁前0047/迁后0048检查和获准的新版本；不要把新源码套入下文`build.sh 269`，或把新`upgrade head`说成0047幂等核对。本清单没有验证269/Python269在0048后的备用或回退能力。

## 备份与新发布目录

从审定Git提交以LF archive导出源码，保留两个server目录相对路径。新目录与独立STATE按当次任务指定；不能复用正在运行的源码目录写配置，不能覆盖272/274/275备份。下面`RELEASE_ROOT`、`STATE`由任务给出，不默认猜一个新任务号。实际在线源码与env路径以预检/README为准。

```sh
set -eu
umask 077
: "${RELEASE_ROOT:?set the approved new release directory}"
: "${STATE:?set a new approved state directory}"
DEPLOY="$RELEASE_ROOT/refactor/server-java/deploy"
test -f "$DEPLOY/build.sh"
test -f "$DEPLOY/java-compose.sh"
test ! -e "$STATE"
mkdir -p "$STATE"
docker inspect --format '{{.Image}}' mentorflow-java-backend > "$STATE/java-before-image-id"
docker inspect --format '{{.Id}}' mentorflow-java-backend > "$STATE/java-before-container-id"
docker inspect --format '{{.State.StartedAt}}' mentorflow-java-backend > "$STATE/java-before-started-at"
docker exec mentorflow-e1-preview-postgres-1 pg_dump -U postgres -d mentorflow -Fc > "$STATE/mentorflow-before.dump.tmp"
chmod 600 "$STATE/mentorflow-before.dump.tmp"
test -s "$STATE/mentorflow-before.dump.tmp"
docker exec -i mentorflow-e1-preview-postgres-1 pg_restore --list < "$STATE/mentorflow-before.dump.tmp" > "$STATE/backup-contents.txt"
test -s "$STATE/backup-contents.txt"
mv "$STATE/mentorflow-before.dump.tmp" "$STATE/mentorflow-before.dump"
```

单独保留当前镜像的回退标签，名字按当次任务指定，不覆盖既有rollback标签。备份只证明可列目录，不等于已验证整库恢复；普通应用回退不恢复旧全库。

## 复制当前Java配置、迁移、构建

用运行Java的Compose labels找唯一旧deploy目录；不从停止的旧Python重新生成Java运行设置。`BACKEND_ENV`使用README中的已有入口文件，不能打印环境内容。

```sh
: "${BACKEND_ENV:?set the existing runtime env file listed in README}"
test -s "$BACKEND_ENV"
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
test "$OLD_DEPLOY" != "$DEPLOY"
test -s "$OLD_DEPLOY/.env"
test -s "$OLD_DEPLOY/.java.env"
test -s "$OLD_DEPLOY/compose.generated.yml"
cp "$OLD_DEPLOY/.env" "$DEPLOY/.env"
cp "$OLD_DEPLOY/.java.env" "$DEPLOY/.java.env"
cp "$OLD_DEPLOY/compose.generated.yml" "$DEPLOY/compose.generated.yml"
grep -v '^MF_JAVA_DEPLOY_DIR=' "$DEPLOY/.env" > "$STATE/deploy.env" || true
printf 'MF_JAVA_DEPLOY_DIR=%s\n' "$DEPLOY" >> "$STATE/deploy.env"
cat "$STATE/deploy.env" > "$DEPLOY/.env"
cd "$DEPLOY"
sh migrate.sh current
sh migrate.sh upgrade
sh migrate.sh current
sh build.sh 269
```

在本清单限定的269同源0047归档中，维护命令的当前revision应为0047，upgrade head是幂等核对；含0048的新归档会执行新迁移，必须走上面的独立阶段流程。migrate默认基础/工具镜像、网络、正确secret卷与文件见README；只有非当前现场才用同名export覆盖。密码只在一次性容器内读取，禁止输出。0047增加上传manifest表，已有资料正文、段和向量不得改动。

**build成功后**才 `java-compose.sh config`；`MF_JAVA_VERSION`由build写入，不能提前config（[BUG-209]）。构建校验最终镜像JAR SHA256；同名269重建前必须留独立回退标签。每条命令检查退出码，长任务放在现有job/checked-step中，SSH断开只轮询同一job。

## 重建Java与探活

```sh
test "$(sh cutover.sh status)" = java
sh java-compose.sh config
sh java-compose.sh up
test "$(docker inspect --format '{{.State.Status}}' mentorflow-java-backend)" = running
BACKEND_ID=$(docker inspect --format '{{.Id}}' mentorflow-java-backend)
OIDC_MODE=$(docker inspect --format '{{.HostConfig.NetworkMode}}' mentorflow-java-oidc)
if [ "$OIDC_MODE" != "container:$BACKEND_ID" ]; then
  docker rm -f mentorflow-java-oidc
  sh java-compose.sh up
  BACKEND_ID=$(docker inspect --format '{{.Id}}' mentorflow-java-backend)
  OIDC_MODE=$(docker inspect --format '{{.HostConfig.NetworkMode}}' mentorflow-java-oidc)
fi
test "$OIDC_MODE" = "container:$BACKEND_ID"
PYTHON_IMAGE=$(docker inspect --format '{{.Config.Image}}' mentorflow-e1-preview-backend-1)
ready=0
for attempt in $(seq 1 20); do
  if docker run --rm --pull=never --network mentorflow-e1-preview_internal \
    -e PYTHONDONTWRITEBYTECODE=1 -v "$DEPLOY/probe.py:/tmp/probe.py:ro" --entrypoint python \
    "$PYTHON_IMAGE" /tmp/probe.py --java-only --timeout 1 > "$STATE/java-probe.log" 2>&1; then ready=1; break; fi
  sleep 5
done
cat "$STATE/java-probe.log"
test "$ready" -eq 1
for path in live startup ready; do
  code=$(curl -ksS --max-time 15 -o /dev/null -w '%{http_code}' "https://89.45.45.28/health/$path")
  printf '%s %s\n' "$path" "$code"
  test "$code" = 200
done
```

Java没有Docker HEALTHCHECK；只有真实9/9契约、readiness以及公网三项200才通过。主机无凭据健康GET沿用现有curl核验方式；真实客户端必须用既有合并CA验证TLS，不能关闭验证。账号、refresh摘要和计划数据在PostgreSQL；重启可通过刷新续会话，不要求用户重新输入密码。无效token探针不算真实登录验收。

## 同源Python备用

库升级后备用必须用相同源码构建并验证schema一致。当前静态fallback覆盖已指向README中的269标签，旧226标签保留但不能接0047启动。

```sh
cd "$RELEASE_ROOT"
docker build -f refactor/server/Dockerfile -t mentorflow-slim-backend:269-python-fallback .
cd "$DEPLOY"
export PYTHON_COMPOSE_PROFILES=
sh python-compose.sh recreate
sh restore-python.sh --prepare-only
docker run --rm --pull=never --network mentorflow-e1-preview_internal \
  -e PYTHONDONTWRITEBYTECODE=1 -v "$DEPLOY/probe.py:/tmp/probe.py:ro" --entrypoint python \
  mentorflow-slim-backend:269-python-fallback /tmp/probe.py --timeout 15
test "$(sh cutover.sh status)" = java
sh stop-python.sh
```

上面只准备并探测备用，**不切公网流量**。Python269没有RAG documents API，不能把9/9账号/同步探针描述为资料问答备用可用。只有另行批准才用`restore-python.sh`无参数切Python；之前必须确认两个服务真实健康，切后仍需公网检查，失败回Java。Compose dry-run不能代替这一步（[BUG-193]）。

## 应用回退

优先用当次保留的已验证旧Java镜像，不降级0047、不还原旧全库。若确需紧急回Java226，需另外获准并告知用户暂停资料删除：226旧DELETE会连带清理alias，不实现269逐份删除语义。manifest表不能让226获得新删除规则。旧Python226不是0047回退目标。

下面只展示回退版本键的稳健写法；实际rollback脚本还必须检查OIDC命名空间、执行上述真实探针与公网健康检查，任何失败停止并保留日志。

```sh
cd "$DEPLOY"
grep -v '^MF_JAVA_VERSION=' .env > "$STATE/rollback.env" || true
printf 'MF_JAVA_VERSION=226\n' >> "$STATE/rollback.env"
cat "$STATE/rollback.env" > .env
sh java-compose.sh config
sh java-compose.sh up
```

不把以上多行命令当成自动安全回退：正式操作必须使用已审阅ACTION/ROLLBACK脚本配合`checked-step.sh`，并记录原步骤与回退各自exit code。job的UNKNOWN或SSH断开不触发应用回退。本机fake Docker只验证组装/失败传播；真实上线和回退另按批准任务验证。本轮277只读试跑，不执行此清单任何写操作。
