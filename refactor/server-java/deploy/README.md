# Java 后端部署操作

**每次上线的第一步必须运行 `sudo -n sh preflight.sh`（已在root shell时用 `sh preflight.sh`），所有项目 OK、退出码 0 后才能备份、迁移、构建或重建容器。** FAIL 先核实原因；不在上线中途临时改配置或猜卷名。只读检查不等于获准部署。

当前操作清单只有 [RELEASE269.md](RELEASE269.md)，线上版本及验证范围见 [ARCH-09](../../../docs/project/CURRENT_STATE.md#arch-09-服务器后端逐块改用-java)。当前 Python backend 已停止、edge upstream 指向正式 Java。重建接流量的服务须在架构师批准的重启窗口内执行，running 不等于 ready。

## 只读上线前检查

脚本既能从仓库文件运行，也能通过 `sudo -n sh -s` 从标准输入执行，不依赖服务器有一份新脚本。后者不上传文件、不在服务器留文件。Windows 调用端应将最终脚本按 UTF-8、LF 的字节输入 SSH，不能让 text-mode 管道转回 CRLF。

```sh
# 已授权的服务器 shell；下面只检查，不部署。
sudo -n sh preflight.sh
```

检查现有镜像、网络、卷/secret元信息、Compose labels/文件/env键/挂载、源码、磁盘、当前库版本和健康入口。每项输出 `项目名 OK/FAIL 原因`；不打印 `.env` 值、secret、令牌或资料正文。迁移项只使用**已经存在**的工具镜像执行 Alembic `current`；镜像缺失直接 FAIL，不拉取、不构建。不得用普通 `migrate.sh current` 代替此项：该维护命令会先构建工具镜像。

预检默认针对当前在线发布目录。新包尚未构建时，先检查在线目录；不要对新包提前运行 `java-compose.sh config`。新包的 `MF_JAVA_VERSION` 要等 `build.sh VERSION` 写入后才能展开 Compose（[BUG-209]）。新的现场只有任务明确批准后才能通过同名环境变量覆盖；默认值对应下面这份统一清单。

## 服务器现场条件

这里只记录对象名称、路径和必需键名，不记录密码、令牌或 `.env` 内容。换现场时先核对本节和预检输出，再更新脚本默认与对应测试，不能只留在回执。

| 条件 | 当前名称/约束 |
| --- | --- |
| 运维权限 | 登录用户 `mentorflow-admin`（服务器 `PermitRootLogin no`，root 不能 ssh 登录），钥匙 `C:\Users\21533\.ssh\mentorflow_server`（2026-11-09 新建，[BUG-228]）；SSH 别名 `mentorflow` 需在本机 `~/.ssh/config` 里配（HostName 89.45.45.28、User mentorflow-admin、IdentityFile 上面那把、IdentitiesOnly yes），没配时用 `ssh -i <钥匙> mentorflow-admin@89.45.45.28`；Docker与现有root文件用非交互 `sudo -n`，不改权限 |
| 在线源码 | `/opt/mentorflow/releases/java-1070685b`；新包从审定提交以 `git -c core.autocrlf=false -c core.eol=lf archive` 导出，保留 `refactor/server`、`refactor/server-java` 相对路径，不覆盖运行目录/挂载；比对关键源码SHA256（[BUG-107]） |
| Java pair | `mentorflow-java-backend`、`mentorflow-java-oidc`；镜像 `mentorflow-java-server:269`；OIDC使用backend网络命名空间，每次重建后精确核对 `container:<backend ID>` |
| Python pair | `mentorflow-e1-preview-backend-1`、`mentorflow-e1-preview-oidc-loopback-proxy-1`；`mentorflow-slim-backend:269-python-fallback`；保持停止，认0047；旧226标签保留但不能作为当前Python备用 |
| 回退保留 | `mentorflow-java-server:226`、`mentorflow-java-server:rollback272`；`release272`、`release274`、`release275`下各自数据库备份不能覆盖或清理 |
| PostgreSQL | `mentorflow-e1-preview-postgres-1`；业务库 `mentorflow`、迁移角色 `mentorflow_migrator`、主机 `postgres`；当前Alembic为 `0047_rag_promoted_manifests`，上传manifest表为`rag_document_upload_manifests`；PG17与pgvector扩展沿用，不改角色/授权 |
| 迁移镜像 | 基础 `mentorflow-slim-backend:269-python-fallback`；已存在工具 `mentorflow-migration-tool:MF-275`；`migration.Dockerfile`只额外装项目已用的固定Alembic版本 |
| 迁移网络/凭据 | `mentorflow-e1-preview_internal`；**`mentorflow-e1-preview_migrate_secrets`** 卷，容器内 **`/run/secrets/postgres_migrator_password`**。OIDC的 `mentorflow-e1-preview_oidc_proxy_secrets` 是TLS卷，不是迁移密码卷（234/274、[BUG-209]） |
| 迁移覆盖参数 | `MIGRATION_IMAGE`、`MIGRATION_TOOL_IMAGE`、`MIGRATION_NETWORK`、`MIGRATION_SECRETS_VOLUME`、`MF_MIGRATION_PASSWORD_FILE`；维护脚本在一次性容器内读密码并URL编码，不写入命令行、`.env`或日志 |
| Compose来源 | 从已有Python backend和Java pair的 `com.docker.compose.project`、`project.working_dir`、`project.config_files`、`project.environment_file` labels读取；文件列表逗号分隔；保留全部后加层，不能猜一个基础compose文件代替 |
| 旧edge标签 | edge仍保留较早的单compose标签和自己的旧runtime.env；检查其记录的文件真实存在，不强求等于backend环境文件或已有network45层。它不是本次重建目标；今后实际执行任何Compose操作仍须network45最后 |
| 环境文件 | `BACKEND_ENV` 指向 `/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env`；旧Python容器已停止也能inspect；不能从旧Python重新生成新版Java运行设置 |
| Java发布配置 | deploy内 `.env`、`.java.env`、`compose.generated.yml`；`.env`必需键：`MF_JAVA_DEPLOY_DIR`、`MF_JAVA_TRUSTSTORE_DIR`、`MF_OIDC_PROXY_IMAGE`、`MF_OIDC_PROXY_CONFIG`；已构建的在线目录另须唯一`MF_JAVA_VERSION`。`.java.env`中的身份、单进程及会话设置沿用，预检只输出键名/存在性 |
| 挂载 | Java truststore、backend/secrets、native mTLS、lifecycle、catalog、TLS、OIDC nginx.conf：从inspect取得来源，只查存在、类型、非空和只读标记，不打印内容；PostgreSQL数据卷和Python数据目录正常可写，不能把所有挂载都要求只读；不能重建密钥或卷来让检查通过 |
| 网络覆盖 | `/opt/mentorflow/state/bug45-20261010/network45.override.yml` 必须是每条Compose命令最后一个 `-f`。用现有 `java-compose.sh`/`python-compose.sh`还原完整文件组；原生 `docker run` 迁移/探针不使用Compose覆盖（[BUG-45]） |
| Python准备参数 | `PYTHON_COMPOSE_PROFILES`未设置保留兼容默认；**显式空字符串**是234/238/275验证过的无profile路径；`PYTHON_FALLBACK_OVERRIDE`可指向发布专用覆盖；`PYTHON_ENV_FILE`未设置时用`BACKEND_ENV` |
| Compose CLI | 现场Compose 5.5.0；`config --quiet`仅证明可展开；`--dry-run`对停止容器的健康依赖曾误报，不能据此判断备用是否可用；不在只读预检里模拟start/up |
| 定向启动 | Python只 `up -d --no-deps backend oidc-loopback-proxy`，重建另加 `--force-recreate`；不能改成`compose start`让旧初始化依赖重跑，也不加`--remove-orphans`或重建postgres |
| Java RAG运行 | `/tmp`必须为带`exec`的128MiB tmpfs；native/cache目录沿用compose（[BUG-205]）；Java没有Docker HEALTHCHECK，不能要求不存在的`.State.Health`，用真实HTTP readiness |
| 公网探活 | API/健康入口是 `https://89.45.45.28` 的 `/health/live`、`/health/startup`、`/health/ready`；下载站域名不是这个vhost（221）。普通API用`/api/enterprise/auth/lifecycle/capabilities`；匿名401不代表用户登录业务通过 |
| TLS/身份扩展 | 保留edge转发的HTTPS语义、Java信任库/Keycloak回环/mTLS。注册SPI有改动时先按该任务验证Keycloak扩展再换Java；实际Keycloak可有shell包装入口，不能只grep一个Cmd字段推断`--optimized` |
| 主机工具/磁盘 | `sh`、Python3、Docker/Compose、curl、sha256sum、df可用；备份用PostgreSQL容器里的`pg_dump/pg_restore`，不用旧宿主机版本。备份前确认目标不存在和足够空间，权限600，不输出备份正文 |
| 预检对照输入 | 当前`SOURCE_SHA256_MANIFEST`为`/opt/mentorflow/state/release275/source.sha256`。`PREFLIGHT_SOURCE_REVISION`默认`0047_rag_promoted_manifests`（完整14项），发布阶段按0048、0049、0050、0051逐项追加迁移文件（最终18项）；每个指定路径必须恰好出现一次，数量由同一集合计算，缺项、重复、额外项或SHA256不符均FAIL。`EXPECTED_ALEMBIC_REVISION`独立表示数据库应处的版本，默认仍0047；`JAVA_IMAGE`、`PYTHON_IMAGE`核对在线镜像，不拿尚未构建的目标冒充现网。`RELEASE_ROOT`绑定清单源码，`DEPLOY_DIR`绑定现有部署配置，`SERVER_ROOT`绑定迁移源码 |

## 0051发布的源码清单与迁移阶段

当前线上事实仍是0047；以下是另行获准后使用的新发布流程，不表示0051已上线。完整路径集合只维护在`preflight.sh`中；`--source-manifest`按选定源码版本输出原生SHA256清单，不连接Docker/数据库、不访问网络、不写文件，且可通过`sh -s -- --source-manifest`标准输入执行。只从审定提交的LF归档生成对照清单，不能从待核验的现场目录重算一份来替代审定输入。

| 阶段 | 源码版本/清单 | 数据库期望 |
| --- | --- | --- |
| 先检查现网 | 默认0047、原14项、原发布目录 | 默认0047，在线镜像及旧部署配置 |
| 新源码迁移前 | 显式0051、原14项依次加`0048_rag_source_chunks.py`、`0049_drop_redundant_rag_indexes.py`、`0050_account_daily_activity.py`、`0051_sync_closure_fence.py`共18项、新归档目录；部署配置仍绑定在线目录 | 显式0047，仍核对正在运行的旧镜像与配置 |
| 迁移成功后 | 保持新0051源码和同一18项清单 | 显式0051，同时要求`rag_source_chunks`存在、0049两项冗余索引已移除、`account_daily_activity`存在、`mentorflow_app`拥有`account_closure.assert_open(text,text)` EXECUTE |

先完成默认现网预检。随后在受信任的构建端为审定的新归档生成清单，调用者只在成功时发布临时输出；将该清单作为发布材料带到批准的现场，不覆盖旧清单：

```sh
set -eu
umask 077
: "${NEW_RELEASE_ROOT:?set the approved new LF archive directory}"
: "${CANDIDATE_SOURCE_MANIFEST:?set a new manifest output path}"
NEW_DEPLOY="$NEW_RELEASE_ROOT/refactor/server-java/deploy"
test ! -e "$CANDIDATE_SOURCE_MANIFEST"
test ! -e "$CANDIDATE_SOURCE_MANIFEST.tmp"
manifest_lines=$(PREFLIGHT_SOURCE_REVISION=0051_sync_closure_fence RELEASE_ROOT="$NEW_RELEASE_ROOT" \
  sh "$NEW_DEPLOY/preflight.sh" --source-manifest)
printf '%s\n' "$manifest_lines" > "$CANDIDATE_SOURCE_MANIFEST.tmp"
mv "$CANDIDATE_SOURCE_MANIFEST.tmp" "$CANDIDATE_SOURCE_MANIFEST"
```

在另行批准的维护shell中，将新源码/清单与仍在线的部署配置分开绑定。`ONLINE_DEPLOY_DIR`须由现有Java Compose labels核实；这一阶段不在新包上提前展开Compose，不提前把`MF_JAVA_VERSION`或在线镜像期望改成未构建版本：

```sh
: "${NEW_RELEASE_ROOT:?set the new archive directory on this host}"
: "${CANDIDATE_SOURCE_MANIFEST:?set the transferred approved manifest on this host}"
: "${ONLINE_DEPLOY_DIR:?set the existing verified Java deploy directory}"
NEW_DEPLOY="$NEW_RELEASE_ROOT/refactor/server-java/deploy"
export RELEASE_ROOT="$NEW_RELEASE_ROOT"
export DEPLOY_DIR="$ONLINE_DEPLOY_DIR"
export SERVER_ROOT="$NEW_RELEASE_ROOT/refactor/server"
export SOURCE_SHA256_MANIFEST="$CANDIDATE_SOURCE_MANIFEST"
export PREFLIGHT_SOURCE_REVISION=0051_sync_closure_fence
EXPECTED_ALEMBIC_REVISION=0047_rag_promoted_manifests sh "$NEW_DEPLOY/preflight.sh"
```

全部OK后才备份；备份确认后从新归档的deploy目录执行`migrate.sh current/upgrade/current`，逐步检查退出码。`upgrade head`此时明确以0051为目标。迁移成功后，用同一绑定再次检查：

```sh
PREFLIGHT_SOURCE_REVISION=0051_sync_closure_fence \
SOURCE_SHA256_MANIFEST="$CANDIDATE_SOURCE_MANIFEST" \
RELEASE_ROOT="$NEW_RELEASE_ROOT" DEPLOY_DIR="$ONLINE_DEPLOY_DIR" \
EXPECTED_ALEMBIC_REVISION=0051_sync_closure_fence sh "$NEW_DEPLOY/preflight.sh"
```

迁后检查通过才按批准的版本构建并重建服务。新配置就绪后，最终预检再将`DEPLOY_DIR`、在线镜像和版本期望切换到实际部署的新值；不能只改数据库期望来绕过真实FAIL。表、索引和授权检查不替代0051迁移角色、回填、RLS和closure测试。旧Python269继续停止，不据此声称已可接0051；备用须另按同源新版本构建并验证。

## 维护顺序与失败传播

预检全OK → 新目录备份并用容器内`pg_restore --list`验可读 → `migrate.sh current/upgrade/current`检查退出码和版本 → 迁后预检 → `build.sh VERSION` → JVM 参数试启动 → `java-compose.sh config/up` → Java 三项真实探活 → 最终预检。逐步成功才继续。此次0051上线不重建或切换旧Python269备用；它继续停止并只认0047。应用启动不自动迁移。

`migrate.sh`维护命令会构建工具，和只读预检不同；不要把输出接到吞退出码的管道。迁移中的closure角色修复已在迁移内按既有`SET LOCAL ROLE`实现，不另授CREATE（[BUG-107]）。若新库已迁移，应用回退保留新表/数据，禁止把恢复旧全库当常规回退；0047不能downgrade。

长任务用现有 `job.sh start JOB_DIR sh checked-step.sh ACTION_SCRIPT ROLLBACK_SCRIPT`，每次用新任务目录。轮询同一job的`status/done/exit_code`；SSH断开或UNKNOWN不等于应用失败，不重复启动、不在SSH客户端异常处理里直接回退。只有真实步骤失败才执行事先审阅的应用回退，回退后仍报告原步骤失败。

所有`.env`版本/目录键改写采用“过滤已有键→追加一行”，同时覆盖无键和重复键情况；用`cat > .env`保留inode。新包配置从当前Java部署复制，改新包的deploy目录键；不要重写线上原配置。

## 备用准备、探针与切流边界

```sh
# 仅示例操作顺序；必须先有对应的服务器变更授权并通过preflight。
export PYTHON_COMPOSE_PROFILES=
sh python-compose.sh recreate
sh restore-python.sh --prepare-only
```

`--prepare-only`等Python backend真实healthy、OIDC running后返回，不切入口；无参数的`restore-python.sh`会准备后切Python。不要用无参数调用做启动对照。准备失败只在Java仍接流量时清理Python，不能误停正在服务的Python。

保持 `BACKEND_ENV`、profile和fallback覆盖参数到整个维护shell。`probe.py`用现有Python镜像在internal网运行，设置`PYTHONDONTWRITEBYTECODE=1`、只读挂载；`--java-only`不请求已停Python。双栈模式要求9项契约、两端ready及email readiness一致。探针不创建账号、不发邮件，只用无效令牌；它不替代真实账号业务验收。

只有明确获准且两侧健康时才能切流；`cutover.sh status`已是java就无需再切。cutover保持edge.conf原inode，nginx检查/reload失败自动恢复。停备用用`stop-python.sh`（先确认入口java）。恢复和故障限制见[RELEASE269.md](RELEASE269.md)；Python269没有RAG资料问答接口，275只验证启动及内部契约，不能声称已做269的全公网备用演练。
