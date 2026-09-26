# MF-U09：服务器源码覆盖包（未执行）

基线 `2014a74`，包含第二批集成成果。仅以本机已有
`mentorflow-mf-u04-backend:MF-U04` 构建 `mentorflow-mf-u09-backend:MF-U09`。
`overlay/` 与 `manifest.json` 固定六个文件：

- `backend/app/enterprise/identity/native.py`
- `backend/app/enterprise_tenancy/structured_sync.py`
- `backend/app/enterprise/catalog_api.py`
- `backend/app/account_core_mvp.py`
- `backend/app/enterprise/structured_sync_api.py`
- `backend/alembic/versions/0042_structured_sync_changes.py`

Dockerfile 只有 FROM 和六条 COPY，不安装依赖、不复制整个仓库。
包内文本固定 LF 换行，manifest 校验的是 Linux 构建使用的原始字节。
数据库由 `0041_account_closure` 升到 `0042_structured_sync_changes`。
先停止 backend、迁移并检查，再切镜像；不重建数据库/Keycloak/代理等服务。
本包没有包含本批客户端代码；客户端随客户端集成交付。

## 执行边界与配置

由架构师获得用户上线许可后，在现有主机本地执行。开发工位未连接服务器，
未运行 Docker、Compose 或任何脚本的 `--execute` 模式。
`release.py`、`configure.py`、`migration.py`、`migration_db.py`、`verify.py` **默认只打印**；只有显式
`--execute` 才运行命令、写配置或发送验证请求。脚本不执行 SSH、上传或推送。

活跃项目固定 `mentorflow-e1-preview`。四份 Compose 按以下顺序叠加：

1. `/opt/mentorflow/releases/21337c1eda64f7e087fcade77c3d4cd7dce4a0af-exact-source/deploy/enterprise/staging/rp05/compose.account-core-mvp.yml`
2. `/opt/mentorflow/artifacts/gatea-native-20260913-08/backend.override.json`
3. `/opt/mentorflow/artifacts/r2-04-final-20260917/runtime.override.json`
4. `/opt/mentorflow/artifacts/mf-d04-20260922/runtime.override.json`

只改第 4 份中的 `services.backend.image` 和 `services.backend.volumes`，
追加只读 bind：宿主 `/opt/mentorflow/state/mentorflow-e1-preview/catalog`
→ 容器 `/run/mentorflow/catalog`。目录必须存在，不让 Compose 隐式创建。
其余键和服务保持原值；脚本比较完整合并配置，越界则停止。

使用当前运维已有的 Compose env 文件，不生成替代 env，不展示其中的秘密。
原生认证须保留 `MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS=1`、`WEB_CONCURRENCY=1`。
`MENTORFLOW_CATALOG_DIR` 不必新增，默认 `/run/mentorflow/catalog`；若现有值不同，
脚本停止，由架构师检查，不擅自改环境。备份目录为
`/opt/mentorflow/state/mentorflow-e1-preview/mf-b03-rollback/mf-u09`，配置快照权限 0600。
备份已存在时不会覆盖，应先核对上次执行情况。

迁移沿用已有 `migrate` 服务的网络、10001 身份和 `migrate_secrets`，
需要其中的 `postgres_app_password`、`postgres_migrator_password`。不新增 DSN 环境变量。
原有 `mentorflow_migrator`、`mentorflow_closure` 及 0041 注销配置必须已完成。
`migration.py` 临时追加第 5 份 override，仅给一次性 `compose run --rm --no-deps migrate`
选用 MF-U09 镜像和只读挂载本包；执行后删除该临时配置，不用于后台常驻服务。
不要单独运行旧 `migrate` 镜像，它不包含新迁移。

## 步骤和检查命令

将整个 `mf-u09` 目录通过获批交付方式放到主机，例如
`/opt/mentorflow/artifacts/mf-u09`。以下变量由运维填实际路径，不含令牌或密码：

```sh
PKG=/opt/mentorflow/artifacts/mf-u09
ENV_FILE=/实际现有的/compose.env
API_ORIGIN=https://实际账号服务域名
CA_FILE=/实际信任的/ca.pem
AUTH_FILE=/受控临时目录/mf-u09-auth.json
```

`AUTH_FILE` 由运维从测试账号当前已验证会话准备，权限 0600，只用于验证：
含 `access_token`、`organization_id`、`workspace_id`。不将令牌放到命令行、
终端输出、仓库或回执。原生 access token 会过期，验证前刷新文件中的令牌。

先查看完整计划：

```sh
python3 "$PKG/release.py" prepare --env-file "$ENV_FILE"
python3 "$PKG/release.py" build --env-file "$ENV_FILE"
python3 "$PKG/release.py" deploy --env-file "$ENV_FILE"
```

获批后逐步加 `--execute`，每步退出码必须为 0：

```sh
python3 "$PKG/release.py" prepare --env-file "$ENV_FILE" --execute
python3 "$PKG/release.py" build --env-file "$ENV_FILE" --execute
python3 "$PKG/release.py" deploy --env-file "$ENV_FILE" --execute
```

- prepare：检查六文件 SHA256/语法、MF-U04 本地镜像存在、Compose 配置有效；
  捕获原 override 与完整合并配置；建立空的 catalog 目录。
- build：`docker build --network=none --pull=false`；新镜像内核对六文件 SHA256。
  打印基础和新镜像 ID，运维保存到部署记录，不改动旧镜像 tag。
- deploy：检查基线仍一致，`stop backend`；运行迁移到 0042，检查版本、回填、
  数据计数/修订总数/删除数/正文大小、序列、索引、触发器与 FORCE RLS；
  再原子替换最后一份 override，检查 `config --quiet`
  及完整配置差异；只运行 `up -d --no-deps --no-build --pull never --force-recreate backend`；
  最后显示 backend 状态。会话在进程重启后需重新登录，按既有内存会话机制处理。
- 任一步失败不继续切镜像；backend 保持停止，按下方回滚恢复 MF-U04。

## 增量数据与迁移检查

0042 只为同步表增加 `change_seq`、唯一索引、作用域分页索引和写入触发器。
原 revision、payload、删除标记、CAS 和幂等回执保持。已有行通过 volatile
`ADD COLUMN DEFAULT nextval(...)` 在表锁内回填，再去掉默认值；无需关闭 RLS，
也不执行旧业务 UPDATE/注销触发器。大表会重写并短暂占用额外磁盘，安排维护窗口，
先按既有运维流程完成数据库备份，确认磁盘和备份可恢复，再执行 deploy。
脚本锁等待上限 15 秒、单条语句上限 120 秒；超时先调查容量/锁持有者，不跳过检查。

序列使用 CACHE 1；写入触发器先获得当前账号/空间的事务锁，再分配序号。
游标只推进到该作用域实际返回的行，不使用全局序列高水位，避免并发提交造成漏读。
接口返回每条记录最新状态；同一记录的多次更新可能合并，追加事件仍各有独立记录。
软删除保留在变化流中。注销账号的硬删除继续由原 0041 流程处理。

可重复的只读数据库检查（默认只打印）：

```sh
python3 "$PKG/migration.py" check --env-file "$ENV_FILE"
python3 "$PKG/migration.py" check --env-file "$ENV_FILE" --execute
```

接口为 `GET /api/enterprise/sync/v1/changes?since=0&kind=learning_run&limit=100`，
认证与空间请求头沿用同步接口；limit 为 1–500。返回 `next_cursor`（十进制字符串）、
`has_more`、`entities`（含 `change_seq` 和删除）。匿名 401；不同账号/空间不可互读。
快照接口保持原协议。客户端每页先保存再推进游标，旧服务器 404 时回退快照。
上线后用受控测试账号运行下方验证，再手测两设备上传、拉取、删除和重开续传：

```sh
python3 "$PKG/verify.py" --mode changes --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE"
python3 "$PKG/verify.py" --mode changes --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE" --execute
```

上线健康检查使用实际存在的 `/health/live`、`/health/startup`、`/health/ready`，
不把不存在的裸 `/health` 当作验收端点：

```sh
python3 "$PKG/verify.py" --mode health --url "$API_ORIGIN" --ca-file "$CA_FILE"
# 核对打印结果后，显式执行；等待 backend 就绪后应全部 200
python3 "$PKG/verify.py" --mode health --url "$API_ORIGIN" --ca-file "$CA_FILE" --execute
```

目录尚未放文件时（现有目录若已含文件，不要删业务文件，应记录实际情况）：

```sh
python3 "$PKG/verify.py" --mode empty --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE"
python3 "$PKG/verify.py" --mode empty --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE" --execute
```

检查两个目录接口匿名 401、认证后 404 `CATALOG_NOT_CONFIGURED`。

## 目录文件准备与更新

在代码工位将维护名单转换为约定 2（只含七个字段，排除 candidates）：

```sh
python tools/export_source_whitelist.py --output /本地交付目录/source-whitelist.json
```

价格文件来自 `deploy/enterprise/staging/rp05/catalog/model-prices.example.json`，
由架构师核对官方来源、日期、计价时段后命名为 `model-prices.json`。
两个文件都须是含 version 的 JSON 对象、UTF-8、各不超过 1 MiB。
通过获批方式交付到主机后，同目录暂存再原子改名：

```sh
CATALOG=/opt/mentorflow/state/mentorflow-e1-preview/catalog
install -m 0644 /已审定交付目录/source-whitelist.json "$CATALOG/.source-whitelist.next"
mv "$CATALOG/.source-whitelist.next" "$CATALOG/source-whitelist.json"
install -m 0644 /已审定交付目录/model-prices.json "$CATALOG/.model-prices.next"
mv "$CATALOG/.model-prices.next" "$CATALOG/model-prices.json"
python3 "$PKG/verify.py" --mode catalogs --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE"
python3 "$PKG/verify.py" --mode catalogs --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE" --execute
```

只读挂载目录会看到原子换入的新文件，无需重建 backend。脚本检查 200 原文、
SHA256 ETag、重复 GET 的稳定性、If-None-Match 304、private/max-age=300 和匿名 401。

## session 与挤下线手测

```sh
python3 "$PKG/verify.py" --mode session --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE"
python3 "$PKG/verify.py" --mode session --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE" --execute
```

当前 Bearer 得到 200 `{"state":"active"}`，匿名得到 401。按以下三个场景手测：

1. 测试账号在 A 登录、开课并产生一条尚未同步的事件；B 用同一账号登录。
   A 的旧 access 请求及旧 refresh 均为 401 `NATIVE_SESSION_SUPERSEDED`，
   提示“账号已在另一台设备登录”；B 的 session/正常请求可用。
2. 客户端接好本批 SessionGuard 后，A 应停止课堂并保留未同步事件；A 再登录，
   补传事件不覆盖 B 的追加进度。课堂未接入前不能把服务端检查当作此项通过。
3. B 正常刷新后新令牌可用；退出后 session 返回普通 401；另一测试账号不受影响。

可将 A 的旧 access 放入另一份受控临时 auth 文件，执行
`verify.py --mode session ... --superseded-auth-file /受控临时目录/old-auth.json --execute`，
自动核对旧 access 的特定错误码。旧 refresh 在客户端手测，不自动发起刷新或登录。
验证结束按运维规则清理测试令牌文件，不记录明文。

## 回滚到 MF-U04

保留 catalog 文件、所有业务记录和幂等回执。停止 backend 后，使用 MF-U09 的一次性
迁移容器降回 `0041_account_closure`，检查数据计数保持，再恢复配置和 MF-U04 镜像。
不触及 0041 注销迁移。保留全局序列及其高水位，重新升级重新回填的序号一定更大，
已有客户端游标仍能追上全部数据。查看计划后，由运维显式执行：

```sh
python3 "$PKG/release.py" rollback --env-file "$ENV_FILE"
python3 "$PKG/release.py" rollback --env-file "$ENV_FILE" --execute
python3 "$PKG/verify.py" --mode health --url "$API_ORIGIN" --ca-file "$CA_FILE" --execute
```

脚本要求当前 override 正是本包生成的内容，恢复原始字节，检查完整配置与捕获的
MF-U04 一致，只重建 backend。若有其他人的改动则拒绝覆盖，交架构师核对。
若升级停在切镜像前，原 override 仍为备份原文，回滚同样接受这一状态。
若降级检查失败，不启动旧镜像掩盖问题，保留停止状态与数据库，交架构师处理。
回滚后新目录/session API 不再作为通过条件；原有账号与健康接口按 MF-U04 验证。
