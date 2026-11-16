# Java 后端部署操作

## 393 edge 访问日志

上线前可运行 `sh edge/install-edge-logging393.sh check /opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf`。它验证真实日志文件的可写性、动态PID轮转配置，并将完整候选配置临时放进容器 `/tmp` 后执行 `nginx -t -p /etc/nginx -c ...`；不改挂载配置、不reload、不装cron、不建备份。会留下允许保留的空访问日志文件。

`install-edge-logging393.sh install EDGE_CONF BACKUP` 会先复用上述检查，再编辑现有 edge nginx 配置文件，保留其 inode；安装前保留备份，`nginx -t` 通过后平滑 reload。回退运行 `install-edge-logging393.sh rollback EDGE_CONF BACKUP`，恢复备份、验证并 reload。脚本不重建 edge 容器。

两个域名写入容器 `/tmp/mentorflow-access393.log`，每行仅含 ISO 时间、nginx 配置域名、状态码。现有宿主 cron 每分钟调用 `rotate-edge-access393.sh`；脚本每次从 Docker inspect 读取当前容器 PID，经 `/proc/<pid>/root/tmp/` 找到该容器内的日志，再调用现有 logrotate。现网 nginx master 的 UID/GID 为 101:101，nginx 自己创建日志；轮转读取当前日志 owner，使用 `su root root` 处理 sticky `/tmp` 目录，并以原 UID/GID 创建新日志。日志位于容器已有的 64 MiB tmpfs（与其他 `/tmp` 内容共享硬上限）；单个日志超过 1 MiB 时轮转并保留 7 个归档。阈值不是文件系统硬配额，单次高流量突发可在下一次检查前超过阈值。Docker stdout 没有大小上限，因此访问日志不写 stdout。容器重启或重建会清空 tmpfs 历史，nginx master重新启动后会创建当前日志；单独 reload nginx不会清空tmpfs。轮转脚本按当前容器 PID 自动跟随。

只读按小时、域名和状态码计数（小时按 nginx 时间戳显示的时区分组）：

```sh
docker exec mentorflow-e1-preview-edge-1 awk -F '\t' '{hour=substr($1,1,13); count[$2,$3,hour]++} END {for (key in count) {split(key, fields, SUBSEP); printf "%s\t%s\t%s\t%d\n", fields[1], fields[2], fields[3], count[key]}}' /tmp/mentorflow-access393.log | sort
```

**每次上线的第一步必须运行 `sudo -n sh preflight.sh`（已在root shell时用 `sh preflight.sh`），所有项目 OK、退出码 0 后才能备份、迁移、构建或重建容器。** FAIL 先核实原因；不在上线中途临时改配置或猜卷名。只读检查不等于获准部署。

历史分阶段操作清单见 [RELEASE269.md](RELEASE269.md)，391 的索引迁移按本文件 `deploy391.sh` 执行。线上版本及验证范围见 [ARCH-09](../../../docs/project/CURRENT_STATE.md#arch-09-服务器后端逐块改用-java)。当前 Python backend 已停止、edge upstream 指向正式 Java。重建接流量的服务须在架构师批准的重启窗口内执行，running 不等于 ready。

## 只读上线前检查

脚本既能从仓库文件运行，也能通过 `sudo -n sh -s` 从标准输入执行，不依赖服务器有一份新脚本。后者不上传文件、不在服务器留文件。Windows 调用端应将最终脚本按 UTF-8、LF 的字节输入 SSH，不能让 text-mode 管道转回 CRLF。

```sh
# 已授权的服务器 shell；下面只检查，不部署。
sudo -n sh preflight.sh
```

检查现有镜像、网络、卷/secret元信息、Compose labels/文件/env键/挂载、源码、磁盘、当前库版本和健康入口。每项输出 `项目名 OK/FAIL 原因`；不打印 `.env` 值、secret、令牌或资料正文。迁移项只使用**已经存在**的工具镜像执行 Alembic `current`；镜像缺失直接 FAIL，不拉取、不构建。不得用普通 `migrate.sh current` 代替此项：该维护命令会先构建工具镜像。

无参数预检默认使用393源码与运行配置、393 Java镜像、0053数据库版本和393来源清单。装前使用保留的391预检入口检查d37112fd+0053，最终使用393入口检查新Java。若现场布局变更，须同步默认值与合同测试。新包尚未构建时，不要提前对新包运行 `java-compose.sh config`。新包的 `MF_JAVA_VERSION` 要等 `build.sh VERSION` 写入后才能展开 Compose（[BUG-209]）。

## 服务器现场条件

这里只记录对象名称、路径和必需键名，不记录密码、令牌或 `.env` 内容。换现场时先核对本节和预检输出，再更新脚本默认与对应测试，不能只留在回执。

| 条件 | 当前名称/约束 |
| --- | --- |
| 运维权限 | 登录用户 `mentorflow-admin`（服务器 `PermitRootLogin no`，root 不能 ssh 登录），钥匙 `C:\Users\21533\.ssh\mentorflow_server`（2026-11-09 新建，[BUG-228]）；SSH 别名 `mentorflow` 需在本机 `~/.ssh/config` 里配（HostName 89.45.45.28、User mentorflow-admin、IdentityFile 上面那把、IdentitiesOnly yes），没配时用 `ssh -i <钥匙> mentorflow-admin@89.45.45.28`；Docker与现有root文件用非交互 `sudo -n`，不改权限 |
| 在线源码 | 393源码和Java运行配置在`/opt/mentorflow/releases/java-393`；旧d37112fd与391归档保留用于回退。新包从审定提交以LF归档导出，保留`refactor/server`、`refactor/server-java`相对路径，不覆盖旧运行目录/挂载；核对归档SHA256（[BUG-107]） |
| Java pair | `mentorflow-java-backend`、`mentorflow-java-oidc`；当前镜像`mentorflow-java-server:393`；OIDC使用backend网络命名空间，每次重建后精确核对`container:<backend ID>` |
| Python pair | `mentorflow-e1-preview-backend-1`、`mentorflow-e1-preview-oidc-loopback-proxy-1`；`mentorflow-slim-backend:269-python-fallback`；保持停止，认0047；旧226标签保留但不能作为当前Python备用 |
| 回退保留 | `mentorflow-java-server:226`、`mentorflow-java-server:rollback272`；`release272`、`release274`、`release275`下各自数据库备份不能覆盖或清理 |
| PostgreSQL | `mentorflow-e1-preview-postgres-1`；业务库 `mentorflow`、迁移角色 `mentorflow_migrator`、主机 `postgres`；391目标Alembic为 `0053_workbench_question_index`，上传manifest表为`rag_document_upload_manifests`；PG17与pgvector扩展沿用 |
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
| 预检对照输入 | 393来源清单为`/opt/mentorflow/state/release393/source.sha256`，37项；旧388/391清单保留。`PREFLIGHT_SOURCE_REVISION`与`EXPECTED_ALEMBIC_REVISION`仍为`0053_workbench_question_index`。默认源码、运行配置均绑定393；缺项、重复、额外项或SHA256不符均FAIL |

## 393 账号页与诊断日志部署

393只切换Java、替换心跳脚本并为两个域名启用精简日志，不执行数据库迁移或schema回退。源码及运行配置为`/opt/mentorflow/releases/java-393`，来源清单为`/opt/mentorflow/state/release393/source.sha256`。`--source-manifest`按选定源码版本输出SHA256清单，不连接Docker/数据库、不访问网络、不写文件。

发布归档必须核对实际输出字节，不能只看Git索引的LF：Windows Git启用`core.autocrlf`时会按归档内`.gitattributes`做导出转换。服务器Java目录统一以LF导出，归档回归测试覆盖所有deploy子目录shell（含edge和monitoring），见[BUG-119]。失败作业和归档保留，重试使用新的job和STATE，不复用已完成的job目录。

| 阶段 | 源码版本/清单 | 数据库期望 |
| --- | --- | --- |
| 装前原入口 | 保留的391源码/清单与d37112fd运行配置 | 0053；Java镜像d37112fd |
| 候选预检 | 393源码/清单，运行配置与镜像仍显式绑定d37112fd | 0053 |
| 最终无参数入口 | 393源码/运行配置/来源清单 | 0053；Java镜像393，保留函数与索引检查 |

候选源码用审定提交的 LF 归档，不覆盖在线运行目录。生成新来源清单时先等全部文件校验成功，再落盘，缺文件时可补齐后重试：

```sh
set -eu
umask 077
: "${NEW_RELEASE_ROOT:?set the approved LF archive directory}"
: "${CANDIDATE_SOURCE_MANIFEST:?set a new manifest output path}"
test ! -e "$CANDIDATE_SOURCE_MANIFEST"
test ! -e "$CANDIDATE_SOURCE_MANIFEST.tmp"
manifest_lines=$(PREFLIGHT_SOURCE_REVISION="${PREFLIGHT_SOURCE_REVISION:-0053_workbench_question_index}" \
  RELEASE_ROOT="$NEW_RELEASE_ROOT" sh "$NEW_RELEASE_ROOT/refactor/server-java/deploy/preflight.sh" --source-manifest)
printf '%s\n' "$manifest_lines" > "$CANDIDATE_SOURCE_MANIFEST.tmp"
mv "$CANDIDATE_SOURCE_MANIFEST.tmp" "$CANDIDATE_SOURCE_MANIFEST"
```

运行`RELEASE_ROOT=/opt/mentorflow/releases/java-393 sh refactor/server-java/deploy/deploy393.sh`。它先核对候选源码和旧现网，再原样复用旧运行配置，构建393镜像、试启动JVM、切换Java pair、探活、替换心跳、安装edge日志，最后隔离调用者覆盖参数跑无参数预检。成功时Java只切换一次；旧配置、旧镜像及本次脚本/edge备份保留。

切换后的任何步骤失败会在服务器作业内执行真实`rollback393.sh`，恢复d37112fd Java及本次心跳/edge配置；回退成功仍保留原步骤失败码。回退失败输出AUTO_ROLLBACK393_FAILED并停止。手工恢复入口为`RELEASE_ROOT=/opt/mentorflow/releases/java-393 sh refactor/server-java/deploy/rollback393.sh`。旧391索引迁移脚本不是本次发布步骤，数据库保持0053。

账号页由Java容器监听8081。现场的`mentorflow-e1-preview_internal`虽在Docker inspect中显示端口HostBindings，实际却没有生成宿主listener。用systemd socket-proxyd在宿主监听`127.0.0.1:18081`，服务按需inspect Java容器在现有internal网络上的IP并连接8081；不改Java网络、不新增egress网络、不经edge。桌面脚本将本机`18091`隧道转发至服务器回环`18081`，打开`/admin/accounts`。`systemd-socket-proxyd`使用`Accept=no`和`--exit-idle-time=30s`，参见[官方手册](https://www.freedesktop.org/software/systemd/man/latest/systemd-socket-proxyd.html)。单独补装可运行`RELEASE_ROOT=<候选源码目录> STATE=<新建部署状态目录> sh install-account-page-proxy.sh install`；回退同一`STATE`运行`sh install-account-page-proxy.sh remove`，只删除带本任务inode所有权标记的units。

## 维护顺序与失败传播

393顺序为预检全OK → 保留旧配置与回退脚本 → Java构建/JVM试启动/切换 → 健康与账号页 → 心跳与edge日志安装 → 无参数最终预检。393不启动Python备用、不改数据库或迁移版本。应用启动不自动迁移。

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
