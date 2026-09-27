# MF-U10：按 MF-U04 取证重做的服务器覆盖包

基线 `0d8ad81`。基础镜像固定 `mentorflow-mf-u04-backend:MF-U04`，候选镜像
`mentorflow-mf-u10-backend:MF-U10`。MF-U09 保留作事故记录，不再用于上线。
本包上线 refactor-sync 信封、course_record、增量 /changes、目录接口和单设备登录。
不提供资料原件对象存储，不改注册、改密、注销业务行为。

## 审计与覆盖范围

完整逐文件表见 [AUDIT.md](AUDIT.md)，机器可核验来源、SHA256 和差异见
[runtime-audit.json](runtime-audit.json)。`manifest.json` 记录每个覆盖文件及其线上
原版本来源。线上取证来自架构师提供的 `D:\MentorFlow\.local\deploy-audit\mfu04`。
未取出的未改动依赖只以 `37f9507` 为历史参考，**不声称已取得其线上字节**。
prepare/build/deploy 在本机 MF-U04 镜像内核验全部参考；差异即停止，由架构师
补取报错文件后重新审计，不能用仓库文件自动替换未知版本。

八个覆盖文件（含一份迁移）：

| 文件 | 用途 | 线上原版本 |
|---|---|---|
| `backend/app/account_core_mvp.py` | 装配目录；去掉原件存储 | MF-U04 取证，等于 37f9507 |
| `backend/app/enterprise/structured_sync_api.py` | /changes；去掉对象接口 | MF-U04 取证，等于 37f9507 |
| `backend/app/enterprise_tenancy/structured_sync.py` | 信封、course_record、增量查询 | MF-U04 取证，等于 37f9507 |
| `backend/app/enterprise/identity/native.py` | 单设备登录、session | MF-U04 后续覆盖版本取证 |
| `backend/app/enterprise/catalog_api.py` | 两个只读目录接口 | 新增，线上不存在 |
| `backend/app/enterprise_tenancy/schema.py` | 准入版本 0042 | MF-U04 取证，等于本批基线 |
| `backend/app/enterprise_tenancy/sync_schema.py` | 精确校验 0042 结构 | MF-U04 取证，等于本批基线 |
| `backend/alembic/versions/0042_structured_sync_changes.py` | 序列、回填、触发器、索引 | 新增，当前线上已回滚 0041 |

Dockerfile 只有 FROM 和八条 COPY；不复制整仓库、不安装依赖。文本固定 LF。
仓库的 `observability/health.py`、`refactor/server/closure.py` 和 `closure_api.py`
已还原到 MF-U04 取证版本，不需要覆盖。删除了两份未上线的 account_object_storage
服务端模块。旧客户端 `sync_transport.upload_original/download_original` 及依赖它们的
旧同步页面仍保留代码；相关 `/api/enterprise/sync/v1/objects/{sha256}` 请求返回 404。
新 CourseSync 使用结构记录，不依赖此接口。
旧 `upload_history_blob/download_history_blob` 也通过同一对象端点，相关请求同样 404。

## 已验证的回滚限制

**原版 MF-U04 不能在 0042 上启动。** 隔离 PostgreSQL 实测：它的同步服务直接
INSERT/UPDATE/DELETE 和快照读取可用，0042 触发器为旧写入自动编号；但真实应用的
`orm.verify_enterprise_runtime_schema()` 只接受 0041，启动在请求处理前就被拒绝。
即使只伪造版本号，其旧同步结构白名单也不接受 change_seq，不能绕过校验。

因此本包自动回滚必须降到 `0041_account_closure`，再恢复原 MF-U04 配置和镜像。
不提供“数据库保留 0042、原 MF-U04 仍能健康”的虚假保证。不覆盖 MF-U04 镜像 tag，
不通过启动补丁掩盖旧镜像的限制。若业务坚持保留 0042，须另行审核一份支持 0042
准入的回滚镜像；该镜像不属于本包。

0042 降级保留所有业务正文、revision、删除标记和幂等回执，并保留序列高水位。
再次升级为所有已有行分配更大的序号，已保存游标仍能追上数据。不会回滚 0041 注销
迁移。0042 改动在 PostgreSQL 事务内完成；读写、回填、隔离、降级/再次升级均有本地测试。

## 执行边界与准备

由架构师获得上线许可后在服务器本机操作。开发工位没有连接服务器、执行 Docker、
部署或执行下列脚本的 `--execute` 模式。所有脚本默认只打印；显式 `--execute` 才
执行。无 SSH、上传或 push。下面命令是交付给运维的操作说明。

沿用活跃项目 `mentorflow-e1-preview`，Compose 顺序固定：

1. `/opt/mentorflow/releases/21337c1eda64f7e087fcade77c3d4cd7dce4a0af-exact-source/deploy/enterprise/staging/rp05/compose.account-core-mvp.yml`
2. `/opt/mentorflow/artifacts/gatea-native-20260913-08/backend.override.json`
3. `/opt/mentorflow/artifacts/r2-04-final-20260917/runtime.override.json`
4. `/opt/mentorflow/artifacts/mf-d04-20260922/runtime.override.json`

只改最后一份 override 的 backend image 与 catalog 只读挂载。宿主目录
`/opt/mentorflow/state/mentorflow-e1-preview/catalog` → 容器 `/run/mentorflow/catalog`。
不创建替代 env、不改其他服务、不执行 compose down。保留
`MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS=1`、`WEB_CONCURRENCY=1`。
`MENTORFLOW_CATALOG_DIR` 默认 `/run/mentorflow/catalog`；配置不同则停止核对。
沿用原迁移服务的网络、UID、密码 secrets、0041 注销角色；不新增 DSN。

将包通过获批方式交付到主机。设置实际路径，不在命令行写令牌或密码：

```sh
PKG=/opt/mentorflow/artifacts/mf-u10
ENV_FILE=/实际现有的/compose.env
API_ORIGIN=https://实际账号服务域名
CA_FILE=/实际信任的/ca.pem
AUTH_FILE=/受控临时目录/mf-u10-auth.json
```

AUTH_FILE 权限 0600，包含受控测试账号当前 `access_token`、`organization_id`、
`workspace_id`；令牌过期后从客户端刷新，不打印秘密。配置备份目录为
`/opt/mentorflow/state/mentorflow-e1-preview/mf-b03-rollback/mf-u10`（目录 0700、文件 0600）。
备份不覆盖；重试前先核对上次状态。

先确认现网 MF-U04 健康，按现有运维流程完成数据库备份及恢复可用性检查，记录
数据库版本为 0041。迁移大表会持锁、重写并占用额外磁盘，需维护窗口和足够空间。
本包不代替既有数据库备份流程，也不读取或输出数据库密码。

## 构建、迁移、切换与检查

先打印计划，逐步核对后才加执行参数；每步退出码必须为 0：

```sh
python3 "$PKG/release.py" prepare --env-file "$ENV_FILE"
python3 "$PKG/release.py" build --env-file "$ENV_FILE"
python3 "$PKG/release.py" deploy --env-file "$ENV_FILE"
# 以下由获批运维执行，开发工位不执行
python3 "$PKG/release.py" prepare --env-file "$ENV_FILE" --execute
python3 "$PKG/release.py" build --env-file "$ENV_FILE" --execute
docker image inspect mentorflow-mf-u04-backend:MF-U04 --format '{{.Id}}'
docker image inspect mentorflow-mf-u10-backend:MF-U10 --format '{{.Id}}'
python3 "$PKG/release.py" deploy --env-file "$ENV_FILE" --execute
```

- prepare：核验包、基础镜像所有审计文件、应用导入、Compose 配置，保存 override
  原字节与完整合并配置，建立 catalog 目录。任一未取证依赖不匹配时，先补取复审。
- build：禁止网络和 pull；在**候选镜像内部**以 network=none、read-only、tmpfs /tmp
  执行 `image_check.py`，实际 import `app.account_core_mvp` 及全部新装配模块，检查路由、
  SHA256、导入路径以及没有 account_object_storage。导入失败在切换前结束。
- deploy：重新执行基础和候选镜像检查及基线配置检查；只停 backend；先迁移 0042，
  检查回填/序列/触发器/索引/RLS/数据总数；再原子替换最后的 override，检查完整配置
  差异；只 `up -d --no-deps --no-build --pull never --force-recreate backend`。
  最多等待 90 秒 Docker 健康状态；退出、重启循环、缺少 healthcheck、超时均失败。
- 从 stop 开始发生任何失败（包括迁移或切换失败），自动执行安全降级、恢复配置、
  重建 MF-U04，并再次等待 healthy。自动回滚成功仍返回失败，不能算上线成功。
  回滚也失败会明确报错，不声称已恢复；保留现场交架构师处理。

`migration.py` 只为一次性 migrate 容器临时追加第五份 override（MF-U10 镜像和本包
只读挂载），不用于常驻服务。锁等待 15 秒，单条 SQL 120 秒。只读复核命令：

```sh
python3 "$PKG/migration.py" check --env-file "$ENV_FILE"
python3 "$PKG/migration.py" check --env-file "$ENV_FILE" --execute
python3 "$PKG/verify.py" --mode health --url "$API_ORIGIN" --ca-file "$CA_FILE"
python3 "$PKG/verify.py" --mode health --url "$API_ORIGIN" --ca-file "$CA_FILE" --execute
```

健康检查为实际存在的 `/health/live`、`/health/startup`、`/health/ready`，均应 200。
进程重启后的重新登录沿用既有内存会话规则。增量接口验证：

```sh
python3 "$PKG/verify.py" --mode changes --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE"
python3 "$PKG/verify.py" --mode changes --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE" --execute
```

## 目录和会话验收

目录没有文件时验证匿名 401、认证后 404 CATALOG_NOT_CONFIGURED；已有目录若有正式
文件，不删除它们来制造空目录，应记录真实状态。

```sh
python3 "$PKG/verify.py" --mode empty --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE"
python3 "$PKG/verify.py" --mode empty --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE" --execute
```

白名单在代码工位用 `tools/export_source_whitelist.py` 转换，价格文件从
`deploy/enterprise/staging/rp05/catalog/model-prices.example.json` 经架构师核价后准备。
正式目录文件均为带 version 的 UTF-8 JSON 对象，每个最多 1 MiB。获批交付后在
catalog 同目录暂存、原子改名为 `source-whitelist.json` 与 `model-prices.json`。
只读目录挂载无需重建即可看到更新。

```sh
python3 "$PKG/verify.py" --mode catalogs --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE"
python3 "$PKG/verify.py" --mode catalogs --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE" --execute
python3 "$PKG/verify.py" --mode session --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE"
python3 "$PKG/verify.py" --mode session --url "$API_ORIGIN" --ca-file "$CA_FILE" --auth-file "$AUTH_FILE" --execute
```

目录检查原文 200、稳定的 SHA256 ETag、If-None-Match 304、private/max-age=300。
session 检查当前 Bearer 200 `{"state":"active"}`、匿名 401。上线后手测：

1. 同一受控账号在 A 登录，再在 B 登录；A 的旧 access 和 refresh 均返回 401
   NATIVE_SESSION_SUPERSEDED，B 正常。可加 `--superseded-auth-file` 自动核对旧 access。
2. B 刷新/登出正常，另一测试账号不受影响；客户端课堂收到挤下线后停止并保留未同步记录。
3. 新客户端写 course_record，另一设备通过 /changes 拉取并重开续传；旧快照仍兼容。

## 手动回滚

```sh
python3 "$PKG/release.py" rollback --env-file "$ENV_FILE"
python3 "$PKG/release.py" rollback --env-file "$ENV_FILE" --execute
python3 "$PKG/verify.py" --mode health --url "$API_ORIGIN" --ca-file "$CA_FILE" --execute
```

只恢复本包捕获的 MF-U04 配置原字节；有无关改动则拒绝覆盖。数据库降到 0041，
catalog 文件保留，只重建 backend 并等待 healthy。回滚后新目录、session、/changes
不应再用作 MF-U04 通过条件。新 course_record 已存正文保留；MF-U04 没有 refactor-sync
写入能力，需等待重新上线后续传，不能把接口存在与记录可写混为一谈。

## 本地演练与证据边界

`backend/tests/test_mfu10_postgres.py` 使用便携 PostgreSQL 17 的独立 initdb、随机
回环端口与临时密码；真实 mentorflow_app/mentorflow_migrator/NOINHERIT closure 角色
和 FORCE RLS。临时后端由 37f9507 未改动源码 + MF-U04 实际取证 + 本包覆盖组装，
不从工位源码路径借用模块。仅外部身份权威用明确替身，禁止非回环 DNS/网络。
实际 Uvicorn HTTPS 子进程完成启动校验、课程写入/增量读取、目录 404/200/304、
session/挤下线、原件接口 404，并验证旧应用拒绝 0042、降级后的 MF-U04 健康读写、
再次升级后课程正文和游标恢复。只读镜像检查程序也在组装目录执行。

部署逻辑测试使用假命令执行器和假时钟，覆盖镜像导入失败禁止切换、90 秒健康超时、
迁移/启动失败自动回滚、回滚失败不报成功、默认不执行、只变更最后 override。
这不是 Docker 实际构建或线上部署的报告；本机没有 Docker 命令，镜像内真实检查由
获批构建步骤执行。结果与精确命令见本批回执及 `validation.json`。
