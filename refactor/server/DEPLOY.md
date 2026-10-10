# 独立账号与同步入口（部署材料，工位未部署）

入口：`refactor.server.app:app`，单进程；数据库继续用
`0042_structured_sync_changes`，Keycloak 继续用
`mentorflow-mf-u11-keycloak:MF-U11`。这次只替换 backend 镜像和启动命令。

## 构建与切换

以下由架构师在获准后于服务器本机执行。`$RELEASE` 指包含 refactor/server 的
本次源码目录；`$BACKEND_ENV` 是现用 Compose env 文件，不新建或猜测凭据。

```sh
docker build -f "$RELEASE/refactor/server/Dockerfile" -t mentorflow-slim-backend:MF-14 "$RELEASE"
docker run --rm --entrypoint python mentorflow-slim-backend:MF-14 -c 'from refactor.server.app import app; print(app.title)'
docker image inspect mentorflow-slim-backend:MF-14 --format '{{.Size}}'
```

构建上下文只需 `refactor/server/`；可以在服务器临时空目录中保留这个相对路径再构建，
无需上传 backend、客户端、模型、测试或私钥。没有签名、扫描或额外认证步骤。

沿用 MF-U11 的 backend Compose 叠加顺序（项目 `mentorflow-e1-preview`）：

1. `/opt/mentorflow/releases/21337c1eda64f7e087fcade77c3d4cd7dce4a0af-exact-source/deploy/enterprise/staging/rp05/compose.account-core-mvp.yml`
2. `/opt/mentorflow/artifacts/gatea-native-20260913-08/backend.override.json`
3. `/opt/mentorflow/artifacts/r2-04-final-20260917/runtime.override.json`
4. `/opt/mentorflow/artifacts/mf-d04-20260922/runtime.override.json`
5. 本次 `$RELEASE/refactor/server/compose.slim.yml`

在当前运维 shell 中设置 `OLD_COMPOSE` 为包含现用 `--env-file`、`-p mentorflow-e1-preview`、
上述前四个 `-f` 参数的命令数组。`NEW_COMPOSE` 是在该数组后追加第五份 `-f` 的数组。
下列为 bash 数组调用；不能把带空格的整段命令当成一个数组元素。

```sh
OLD_COMPOSE=(docker compose --env-file "$BACKEND_ENV" -p mentorflow-e1-preview
  -f /opt/mentorflow/releases/21337c1eda64f7e087fcade77c3d4cd7dce4a0af-exact-source/deploy/enterprise/staging/rp05/compose.account-core-mvp.yml
  -f /opt/mentorflow/artifacts/gatea-native-20260913-08/backend.override.json
  -f /opt/mentorflow/artifacts/r2-04-final-20260917/runtime.override.json
  -f /opt/mentorflow/artifacts/mf-d04-20260922/runtime.override.json)
NEW_COMPOSE=("${OLD_COMPOSE[@]}" -f "$RELEASE/refactor/server/compose.slim.yml")
"${NEW_COMPOSE[@]}" config --no-interpolate
"${NEW_COMPOSE[@]}" up -d --no-deps --force-recreate backend
cid=$("${NEW_COMPOSE[@]}" ps -q backend)
for attempt in $(seq 1 45); do
  state=$(docker inspect --format '{{.State.Health.Status}}' "$cid")
  [ "$state" = healthy ] && break
  sleep 2
done
docker inspect --format '{{.State.Health.Status}}' "$cid"
"${NEW_COMPOSE[@]}" exec -T backend python -c 'import urllib.request; print([(p, urllib.request.urlopen("http://127.0.0.1:8000/health/"+p).status) for p in ("live","startup","ready")])'
```

只有 healthy 且三个健康检查均 200 才继续。否则立即执行下文回滚。
如现用 `oidc-loopback-proxy` 配置仍为 `network_mode: service:backend`，重建 backend 会
更换网络命名空间，须一并重建该代理（不改其配置）：
`"${NEW_COMPOSE[@]}" up -d --no-deps --force-recreate oidc-loopback-proxy`。
再检查代理健康及 `/health/ready`；原生认证已配置为直接 Keycloak SPI 时不依赖该代理。
不运行 `down`、不重建 postgres/Keycloak、不升级/降级数据库。

保留原有环境变量和只读配置挂载，尤其是：
`MENTORFLOW_KEYCLOAK_*`、`MENTORFLOW_NATIVE_*`、`MENTORFLOW_IDENTITY_*`、
`MENTORFLOW_ACCOUNT_LIFECYCLE_ENABLED/CONFIG`、`MENTORFLOW_APPLICATION_SUPER_ADMIN_ACCOUNT_IDS`、
`MENTORFLOW_ADMIN_OVERVIEW_CONFIG`、`MENTORFLOW_CATALOG_DIR`、`SSL_CERT_FILE`。
同样保留现有 identity continuity 配置及其公钥（如已配置）。登录桥沿用既有 TLS/mTLS，
本次没有新增证书或校验层。目录仍挂载 `/run/mentorflow/catalog`，缺文件保持 404。
旧对象存储环境和挂载可以暂时保留，但新入口不读取、不提供原件接口。

重建会清空进程内原生会话，用户须重新登录。切换后用现有测试账号检查：登录与挤下线、
注册/找回验证码、账号设置邮箱/改密、同步写入/changes、目录 200/304、管理员概览。
注销仅使用明确可删除的验收账号；本次工位测试没有真实账号。

## 回滚

确认前四份配置中 backend 镜像仍是 `mentorflow-mf-u11-backend:MF-U11`；本次没有改写它们。
移除第五份覆盖即可恢复原启动命令与镜像：

```sh
"${OLD_COMPOSE[@]}" up -d --no-deps --force-recreate backend
"${OLD_COMPOSE[@]}" ps backend
"${OLD_COMPOSE[@]}" exec -T backend python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health/startup").status)'
```

同样等待 healthy，并在需要时按旧配置重建 `oidc-loopback-proxy`；再检查 live/startup/ready。
数据库及 Keycloak 完全不变，无数据迁移，也不删除任何数据。不要使用旧 MF-U09 的降库脚本。

## 保留的迁移与 Keycloak

`migrations/versions/` 保留原完整链（0001 至 0042，原链没有 0019）；只有早期迁移的
ORM import 改成本目录内的冻结元数据。`migrations/legacy/` 仅供显式 Alembic 命令，
不会被入口加载。避免重写基线造成现有表、函数、触发器或 RLS 漂移。
本次部署不运行 Alembic。将来独立建库须按原角色预置流程，由数据库维护者使用
`alembic==1.18.5` 和 `MENTORFLOW_MIGRATION_DATABASE_URL` 显式运行
`python -m alembic -c refactor/server/alembic.ini upgrade head`；运行依赖不安装 Alembic。

`keycloak/` 收入 MF-U11 原 JAR、SPI Java 源码、构建脚本、realm 模板、主题、
realm-policy 和原策略管理脚本。此次不重建或重配 Keycloak，原挂载和启动方式原样沿用。
realm 导入模板不是线上现有 realm 的导出，`--import-realm` 不覆盖已有 realm。
`realm_admin.py` 仅保留原 UPDATE_PASSWORD 可重复执行/回滚工具，本次不调用。
正式环境独立存在的密码黑名单、SMTP、密钥及凭据不复制进源码。

## BUG-45：网关固定地址与动态地址池

网关在 `mentorflow-e1-preview_internal` 的 `172.18.0.5` 与
`mentorflow-e1-preview_edge` 的 `172.19.0.2` 必须留在动态地址池外。
现用完整 Compose 命令的最后追加 `-f compose.network45.yml`（使用部署后的绝对路径）：
它声明网关固定 IP、Keycloak 原有单地址信任，以及两个高段动态池
`172.18.128.0/17`、`172.19.128.0/17`。不改镜像、端口、数据卷或应用功能。
不要仅凭修改 YAML 就声称现有网络已经生效：Docker 网络的 IPAM 需要一次维护窗口重建。

`ops/network45.py` 是一次性分步迁移工具，不是启动守护程序；仅由架构师获准后
在服务器执行。按 `backup → check-compose → preflight → quiesce → detach → rebuild →
attach → start → verify` 执行，每步为独立命令，例如：

```sh
python3 network45.py backup --state /opt/mentorflow/state/bug45-20261010 --env-file /现用env文件
python3 network45.py check-compose --state /opt/mentorflow/state/bug45-20261010 --overlay /部署路径/compose.network45.yml
```

工具从真实 Docker inspect 与 Compose labels 备份现行配置，保留当前所有容器的 IP、
DNS aliases、运行状态、restart 策略及 SMTP 外联网；当前动态低段 IP 会显式保留，
未来新增动态端点只从高段分配。只重建两个网络，不删应用容器、镜像、卷或数据库。
停着的重复旧网关保留但断网且 `restart=no`，切换和回退均不让它抢现用网关 IP。
配置出现未支持端点、旧网络 ID、信任地址/子网不符或库存变更时在停机前停止，
不得跳过错误。实际部署文件可能含密码；备份留在服务器，不提交。

`rehearse` 在验证现网后临时停网关并释放它的端点，让其余服务与两个全新动态探针
先起来，再接回网关，验证高段分配与低段固定地址不会冲突；不是一次真实主机重启。
`rollback` 可在切换中断后按原网络 IPAM 恢复，仍隔离旧网关；回退会恢复原冲突风险。
验证默认检查四个公网入口最终 HTTP 200（公网 IP 证书需现有 CA 时传 `--ca`），
然后由用户手工确认客户端登录。完整现场命令和故障处理见该批交付的 `README-server.md`。

源码溯源见 `source_manifest.json`；本地接口审计、运行规模和验证证据见任务回执链接的 report.md。
