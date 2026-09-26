# MF-U09：服务器源码覆盖包（未执行）

基线 `399e9c4`，包含已合入的 T07。仅以本机已有
`mentorflow-mf-u04-backend:MF-U04` 构建 `mentorflow-mf-u09-backend:MF-U09`。
`overlay/` 与 `manifest.json` 固定四个源码文件：

- `backend/app/enterprise/identity/native.py`
- `backend/app/enterprise_tenancy/structured_sync.py`
- `backend/app/enterprise/catalog_api.py`
- `backend/app/account_core_mvp.py`

Dockerfile 只有 FROM 和四条 COPY，不安装依赖、不复制整个仓库。
包内文本固定 LF 换行，manifest 校验的是 Linux 构建使用的原始字节。
数据库仍为 0041；不运行迁移、不重建数据库/Keycloak/代理等服务。
本包没有包含本批客户端代码；客户端随客户端集成交付。

## 执行边界与配置

由架构师获得用户上线许可后，在现有主机本地执行。开发工位未连接服务器，
未运行 Docker、Compose 或任何脚本的 `--execute` 模式。
`release.py`、`configure.py`、`verify.py` **默认只打印**；只有显式
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

- prepare：检查四文件 SHA256/语法、MF-U04 本地镜像存在、Compose 配置有效；
  捕获原 override 与完整合并配置；建立空的 catalog 目录。
- build：`docker build --network=none --pull=false`；新镜像内核对四文件 SHA256。
  打印基础和新镜像 ID，运维保存到部署记录，不改动旧镜像 tag。
- deploy：检查基线仍一致，原子替换最后一份 override，检查 `config --quiet`
  及完整配置差异；只运行 `up -d --no-deps --no-build --pull never --force-recreate backend`；
  最后显示 backend 状态。会话在进程重启后需重新登录，按既有内存会话机制处理。
- 若修改 override 后任一步失败，不继续其他操作，按下方回滚恢复 MF-U04。

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

保留 catalog 文件和数据库，不做降级迁移。查看计划后，由运维显式执行：

```sh
python3 "$PKG/release.py" rollback --env-file "$ENV_FILE"
python3 "$PKG/release.py" rollback --env-file "$ENV_FILE" --execute
python3 "$PKG/verify.py" --mode health --url "$API_ORIGIN" --ca-file "$CA_FILE" --execute
```

脚本要求当前 override 正是本包生成的内容，恢复原始字节，检查完整配置与捕获的
MF-U04 一致，只重建 backend。若有其他人的改动则拒绝覆盖，交架构师核对。
回滚后新目录/session API 不再作为通过条件；原有账号与健康接口按 MF-U04 验证。
