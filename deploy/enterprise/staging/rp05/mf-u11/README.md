# MF-U11：软件内注册与找回验证码

基线 f27737f；本包只由获批运维人员在服务器本机执行。开发工位没有连接服务器、
获取生产 JAR 或部署。所有 CLI 默认打印计划，只有明确的 `--execute` 才执行。
不迁移数据库，数据库保持 0042。旧 MF-U09/U10 包保留，不作为本次回滚命令使用。

## 覆盖、来源与预检

| 内容 | 本次变更 | 已确认原版本 |
|---|---|---|
| backend/app/enterprise/identity/lifecycle_api.py | 注册验证码三接口；旧 forgot 返回 410，不发链接 | MF-U10 沿用 MF-U04 取证，和 f27737f 一致 |
| mentorflow-native-verifier.jar | 新增 RegistrationCodes；NativeIdentityVerifier 路由；EmailCodeReset 邮件文案 | 架构师 2026-09-28 只读取证的 SHA256，见下 |
| UPDATE_PASSWORD realm required action | 只把 defaultAction 改 false，enabled 保持 true | 执行前 GET 保存完整原值，回滚原样恢复 |

后端底图 `mentorflow-mf-u10-backend:MF-U10`；候选 `mentorflow-mf-u11-backend:MF-U11`。
Keycloak 底图 `mentorflow-r2-keycloak:37f9507`，固定 ID
`sha256:a744677a8b4682c2d64064f89af21c9db688fa68baaac616cc0611231c5871a5`；
候选 `mentorflow-mf-u11-keycloak:MF-U11`。
旧 JAR `/opt/keycloak/providers/mentorflow-native-verifier.jar` 共 46157 字节，SHA256
`367ab6e6724816eb3f06831d334c0fee096ee41e808f13d9d77f383ee34ec46b`。
providers 没有挂载。上述信息来自架构师转达；工位未获得生产 JAR 字节。

prepare 在服务器本机从运行容器复制旧 JAR，先校验上述哈希，再打印新旧类清单：
只允许新增 RegistrationCodes 及内部类，以及变更 NativeIdentityVerifier、EmailCodeReset
及内部类。任何其他类、SPI 注册或资源的字节差异都会停止，列出路径，须架构师取证复审；
禁止用改期望哈希的方式跳过审计。新 JAR 从本仓库源码用 Keycloak 26.7.3 / JDK 21 构建；
fixture-control 测试扩展不在包内。此线上对比尚未执行，不声称生产 JAR 已比对通过。

`manifest.json` 包含原后端运行文件来源/哈希、全部包文件哈希。`image_check.py` 在断网、
只读候选镜像中实际导入 app.account_core_mvp、生命周期接口及既有装配模块，并核对路由。
Keycloak 候选替换 JAR 后必须重新 `kc.sh build`，沿用 37f9507 Dockerfile 的
`KC_DB=postgres KC_HEALTH_ENABLED=true`，确认原 `mentorflow-optimized-v1` 标记保留；
启动命令继续使用原 Compose 的 `start --optimized --import-realm`。
`link-check.jar` 仅作为镜像外只读校验工具挂入；不安装进服务镜像。
已有 realm 不会被 `--import-realm` 覆盖，策略由管理接口更新。

## 两套 Compose，分别保留

后端沿用 MF-U10 的四份文件（代码 `package_ops.FILES`），只改最后
`/opt/mentorflow/artifacts/mf-d04-20260922/runtime.override.json` 的 backend image。

Keycloak 使用架构师提供的独立叠加顺序（代码 `KC_FILES`）：

1. `/opt/mentorflow/releases/1531c65116063ec606ed1c623acd29fb68e9e8b7-exact-source/deploy/enterprise/staging/rp05/compose.account-core-mvp.yml`
2. `/opt/mentorflow/artifacts/gatea-native-20260913-08/keycloak.override.json`
3. `/opt/mentorflow/artifacts/gatea-password-policy-20260913-10/keycloak-denylist.override.json`
4. `/opt/mentorflow/artifacts/gatea-password-policy-20260913-10/keycloak-native-fix.override.json`
5. `/opt/mentorflow/artifacts/r2-04-final-20260917/runtime.override.json`

只改 Keycloak 最后一份的 keycloak image。这个文件也被后端叠加引用，脚本同时校验
两套合并配置；所有其他服务、命令、环境变量、只读挂载必须保持原样。
不执行 compose down，不重建数据库/edge/其他服务。原单进程认证配置保留。

## 运维步骤（开发工位不执行）

先按既有流程备份两个数据库，确认恢复可用、MF-U10 和 Keycloak 均 healthy。
安排维护窗口；两个服务重建后旧原生会话需要重新登录。
提供各自现用的 Compose env 文件，不猜测它们相同。管理接口使用 HTTPS、可信 CA、
受控的短期管理员 Bearer token 文件（0600），由现有批准流程取得，不在命令行放令牌。
state-dir 必须为服务器上新建的绝对路径，存配置/策略/旧 JAR，目录 0700。

以下每步先不加 `--execute` 检查打印结果。实际执行由获批人员逐步加此参数：

```sh
python3 /实际包路径/release.py prepare --env-file /现用/backend.env --keycloak-env-file /现用/keycloak.env --state-dir /opt/mentorflow/state/mentorflow-e1-preview/mf-u11-rollback --origin https://实际身份域名 --ca-file /受控/ca.pem --token-file /受控/admin-token
python3 /实际包路径/release.py build --env-file /现用/backend.env --keycloak-env-file /现用/keycloak.env --state-dir /opt/mentorflow/state/mentorflow-e1-preview/mf-u11-rollback --origin https://实际身份域名 --ca-file /受控/ca.pem --token-file /受控/admin-token
python3 /实际包路径/release.py deploy --env-file /现用/backend.env --keycloak-env-file /现用/keycloak.env --state-dir /opt/mentorflow/state/mentorflow-e1-preview/mf-u11-rollback --origin https://实际身份域名 --ca-file /受控/ca.pem --token-file /受控/admin-token
```

- prepare：核包、MF-U10 镜像内源码/导入；捕获两套原配置、原 JAR 并审计；管理 GET 备份策略。
  任一步非零就停止，不会切换服务。prepare 不覆盖已有备份；失败后先保留证据，另用新目录。
- build：再次核包/配置；只构建两个候选镜像，network=none / pull=false；后端导入和
  Keycloak 类链接检查均须 PASS；不操作运行服务。
- deploy：重复预检并 GET 确认管理员令牌/策略有效 → 停 backend、Keycloak → 原子写两份已审查 override → 只重建 Keycloak
  → 等其 healthy（90 秒）→ 管理 API 更新策略并读回 → 只重建 backend → 等 healthy（90 秒）。
  先迁移策略后开放新注册接口，避免新账号再被附加默认改密。
- 停 backend 后任一失败自动回滚：恢复两份原配置 → 重建旧 Keycloak 并等 healthy → 恢复
  原 UPDATE_PASSWORD 策略 → 重建 MF-U10 backend 并等 healthy；数据库保持 0042。
  回滚也失败则明确非零退出，不宣称恢复成功。独立修改过的配置/策略不会被强行覆盖。
  管理员令牌若在回滚期间失效，仍继续恢复 MF-U10 后端并检查健康，随后明确报告策略
  恢复未完成；不会因策略管理接口失败而把已可恢复的后端留在停机状态。
- 手动回滚使用上述同组参数把 action 改为 `rollback`。别调用 MF-U10 的旧回滚脚本，
  那份脚本会降级到 0041。本包完全没有迁移、降级或数据删除命令。

`realm_admin.py apply` 可重复执行：读回原值或本包目标值才更新；rollback 同理。
不删除用户的 UPDATE_PASSWORD，不改已登录的“当前密码 + 新密码”接口。
长期保留 realm-policy.json 的默认策略已同步；本次只应用一个 required-action 字段，
不要重跑旧的整套 auth-email-security/admin.py 初始化流程。

## 上线后验证与旧账号只读清单

1. `/health/live`、`/health/startup`、`/health/ready` 均 200；两个容器 healthy、无重启循环。
2. 用新的真实邮箱在软件注册：中文六位码、10 分钟、“请在 MentorFlow 注册页面输入”；
   输入后回登录、账号名预填、“注册成功，请登录”；直接登录，不跳浏览器、不要求重设密码。
3. 错码/60 秒重发/原码失效；放置过期后用相同账号名+邮箱重新注册；确认原账号身份复用。
4. 找回密码：软件内邮箱→验证码→新密码；新密码登录成功，旧会话失效；旧 `/forgot` 返回
   410 RESET_CODE_REQUIRED，不发送链接；管理员显式强制改密仍触发验证挑战。
5. 冒烟检查原同步 /changes、目录、session 和账号设置改密。真实邮箱投递由用户实测，
   本地只验证隔离 SMTP；不可将“SMTP 已接受”写成保证用户收件。

```sh
python3 /实际包路径/list_pending_registrations.py --origin https://实际身份域名 --ca-file /受控/ca.pem --token-file /受控/admin-token
```

默认打印；获准读取后加 `--execute`。只 GET 分页、只打印、永不删除。新注册按服务器
provenance 标记列出；旧 `self-registered-learners-e109` 组成员若未验证，单列为
“来源需人工核实”，不能把组成员身份当作删除依据。架构师与用户决定旧账号处理方式。
自动重开仅适用于 MF-U11 标记的未完成注册，旧无标记账号不会被猜测性覆盖。

## 本地验证与重建

`scripts/registration_code_fixture.py` 复用真实 Keycloak 26.7.3 + H2 + mTLS + FastAPI
隔离演练；SMTP 仅本机内存，创建的都是合成用户，不接触现网。它验证发码、错误次数、
重发、过期重开、一次性、管理员动作保留和找回密码。fixture 私钥在退出时清理。
普通回归见 `backend/tests/test_registration_email_codes.py`、`test_mfu11_deployment.py`
及 `tests/refactor/account_lifecycle/`。所有工位临时数据只在 D 盘。

新 JAR 的源码入口为 `scripts/build_native_identity_verifier.py`，精确编译依赖由
本机 Keycloak 26.7.3 分发包提供；`VerifierLinkCheck.java` 可用 `javac --release 21`
独立编译成校验 JAR。重建后必须重新生成 manifest 的内容哈希，不直接修改生产期望值。
