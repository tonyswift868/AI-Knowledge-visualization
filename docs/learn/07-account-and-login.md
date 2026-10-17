# 07｜账号与登录：谁认得你，谁让你进门

本篇事实基线：代码提交 `7df4f6ea`。标题里的“常被问／偶尔问／基本不问”是复习优先级判断，不是面试统计。贯穿场景是一栋有前台的公寓：Keycloak 像核验住户身份的发证处，Java 服务像每间房门口的门卫，PostgreSQL 像住户与门禁记录的账本。这个比喻能解释“身份核验、接口授权、持久化状态”分工；它不等于真实的物理门锁，令牌和数据库事务也不是一张实体门卡。

## 1. 注册：先登记，再确认邮箱｜偶尔问

账号注册要把账号名、邮箱和密码交给身份系统核验，也要确认邮箱归注册者使用。[S02] **一句话层。**

在 MentorFlow 的桌面软件里，用户填账号名、邮箱和密码；Java 账号服务检查输入，再把注册开始请求交给 Keycloak 的身份扩展。Keycloak 建立待验证账号并发出邮箱验证码，用户在软件里填码，验证成功后才可以登录。[S02][C02] **三句话层。**

这条链路由 Java 的 `LifecycleController146` 接收注册和验证码请求，`LifecycleService146` 组织流程，`NativeIdentityBridge` 把身份操作送到 Keycloak；验证码的一次性状态和用户凭据由 Keycloak 扩展维护。[C02][C03] 当前项目状态记录：注册代码已上线，但还没有真人完整走通线上注册；不能把代码接线说成真实用户验收。[S02] **深一层。**

像公寓前台先录入入住申请，再由发证处确认申请人能收到通知。这个比喻说明了“登记”和“验证”是两步；真实系统不会因为填了邮箱就把邮箱持有人自动认定为账号本人，验证要由验证码流程完成。

### 注册时，Java 和 Keycloak 各做什么？

- Java 账号服务检查输入，再把注册开始请求交给 Keycloak 的身份扩展。
- Keycloak 建立待验证账号并发出邮箱验证码，用户在软件里填码，验证成功后才可以登录。
- 验证码的一次性状态和用户凭据由 Keycloak 扩展维护。
- 当前项目状态记录：注册代码已上线，但还没有真人完整走通线上注册；不能把代码接线说成真实用户验收。

### 学习路线：本节出处

| 编号 | 具体说法 | 出处 |
| --- | --- | --- |
| S02 | 邮箱验证码在软件内完成；注册尚无真人线上完整验证 | `D:/MentorFlow/docs/project/CURRENT_STATE.md`：[ACC-02] 第 526–531 行 |
| C02 | 生命周期 HTTP 入口、注册参数、开始注册、验证码确认与状态检查 | `refactor/server-java/src/main/java/win/clannad/mentorflow/account/LifecycleController146.java:29-44,75-91`；`LifecycleService146.java:38-76` |
| C03 | Keycloak 注册扩展创建 pending 用户、设置邮箱未验证并发码 | `refactor/server/keycloak/native-identity-verifier/NativeIdentityVerifier.java:205-209`；`refactor/server/keycloak/native-identity-verifier/RegistrationCodes.java:73-107,111-161` |

你可以这样跟面试官说：**“注册不是 Java 服务自己存一份密码。桌面把申请交给 Spring Boot 账号接口，Java 校验参数并编排验证码流程，Keycloak 才是身份凭据和邮箱验证码的权威；用户在软件里验证邮箱后再登录。代码已经接好，但我不会把它说成线上真人注册已经验收。”**[S02][C02][C03]

## 2. 登录与令牌：短期通行证，长期可续｜常被问

登录验证用户名和密码，成功后软件拿到短期访问令牌和可轮换的刷新令牌。[C04] **一句话层。**

桌面把账号密码发给 Java 的原生登录接口，Java 通过私有 HTTPS 身份桥让 Keycloak 执行密码核验；身份通过后，Java 建立自己的原生会话族并返回 access 与 refresh。日常桌面请求使用随机格式的原生 access；它不是 Keycloak JWT，服务器不会把它按 JWT 解码。[C04][C05] **三句话层。**

Spring Security 的统一入口会按令牌类型走不同验证：Keycloak JWT 由资源服务器验证签名和声明；`mfn1_a_` 原生 access 交给 `NativeSessionService` 检查进程缓存与数据库中的会话族。原生 access 有效期为 300 秒，刷新后会轮换 refresh；数据库保存刷新令牌摘要和会话主体，不保存刷新令牌明文。默认 TTL=0 时会话族没有时间到期，用户退出或明确撤销仍会使它失效；一台设备登录会撤销同账号先前的会话族。[C04][C05][S03] **深一层。**

把 access 想成当天的访客通行证，把 refresh 想成前台核验后换新证的凭条。这个比喻能说明短期 access 与续接会话分工；但 refresh 不是能独立开门的永久密码，服务端会检查它是否当前有效、是否已被使用，旧 refresh 被重放会撤销整组会话。[C05]

### Keycloak JWT 和桌面原生令牌有什么区别？

- Spring Security 的统一入口会按令牌类型走不同验证：Keycloak JWT 由资源服务器验证签名和声明；`mfn1_a_` 原生 access 交给 `NativeSessionService` 检查进程缓存与数据库中的会话族。
- 原生 access 有效期为 300 秒，刷新后会轮换 refresh；数据库保存刷新令牌摘要和会话主体，不保存刷新令牌明文。
- 默认 TTL=0 时会话族没有时间到期，用户退出或明确撤销仍会使它失效；一台设备登录会撤销同账号先前的会话族。

### 学习路线：本节出处

| 编号 | 具体说法 | 出处 |
| --- | --- | --- |
| C04 | 原生登录、5 分钟 access、refresh 轮换、摘要入库、永久会话族、登出与撤销 | `refactor/server-java/src/main/java/win/clannad/mentorflow/account/NativeSessionService.java:31-48,100-175,177-250,252-317`；`refactor/server-java/src/main/java/win/clannad/mentorflow/account/NativeAuthController.java:30-55`；`refactor/server-java/src/main/java/win/clannad/mentorflow/account/NativeConfiguration144.java:82-88` |
| C05 | Keycloak 身份桥、账号与密码状态检查、原生 token 处理 | `refactor/server-java/src/main/java/win/clannad/mentorflow/account/NativeIdentityBridge.java:33-75,88-123`；`NativeSessionService.java:177-249,252-317` |
| C06 | JWT 与原生 access 分流；JWT 签名和声明校验 | `refactor/server-java/src/main/java/win/clannad/mentorflow/identity/IdentitySecurity.java:32-84`；`refactor/server-java/src/main/java/win/clannad/mentorflow/identity/CompatibleJwtDecoder.java:94-165`；`NativeSessionService.java:259-287` |
| S03 | 永久刷新族、会话持久化与当前 Java 服务边界 | `D:/MentorFlow/docs/project/CURRENT_STATE.md`：[BUG-55] 第 1022–1030 行；[ARCH-09] 第 300–324 行 |
| S04 | 同账号单设备在线，登录会使旧设备退出 | `D:/MentorFlow/docs/project/CURRENT_STATE.md`：[ACC-01] 第 520–524 行 |

你可以这样跟面试官说：**“我把身份凭据和应用会话分开了：Keycloak 核验密码并提供身份，Spring Security 验证进入接口的凭证，Java 再检查原生会话和账号状态。桌面 access 只有五分钟，refresh 轮换后摘要写进 PostgreSQL，所以后端重启能接着刷新；同账号新设备登录会撤掉旧会话。”**[C04][C05][C06][S03][S04]

## 3. 找回密码与改密：证明邮箱或当前身份，再换凭据｜偶尔问

找回密码通过绑定邮箱验证码确认，改密或重置完成后，旧原生会话会被撤销。[C07] **一句话层。**

找回入口先向邮箱发送验证码，再校验验证码并签发短期重置证明；用户提交新密码和确认密码后，Keycloak 更新凭据，Java 收到完成结果后撤销该账号现有原生会话。客户端随后提示用户用新密码登录。[C07] **三句话层。**

为了不泄露邮箱是否注册，找回请求对“邮箱存在”和“不存在”使用相同的对外提示。改密码则要求当前用户已登录，并重新核验当前密码；验证通过后更新密码并撤销该主体的会话。邮箱修改也使用登录态下的验证码步骤。[C07] **深一层。**

这像住户遗失钥匙时，先用登记的联系方式证明自己，再由前台换新钥匙并作废旧门禁。它说明了重置证明与旧会话撤销的目的；实际系统里验证码、密码凭据和会话状态分别由 Keycloak、Java 与 PostgreSQL 配合处理，不是一把钥匙由单一组件全部保管。

### 找回密码与改密有哪些步骤和边界？

- 找回入口先向邮箱发送验证码，再校验验证码并签发短期重置证明；用户提交新密码和确认密码后，Keycloak 更新凭据，Java 收到完成结果后撤销该账号现有原生会话。
- 为了不泄露邮箱是否注册，找回请求对“邮箱存在”和“不存在”使用相同的对外提示。
- 改密码则要求当前用户已登录，并重新核验当前密码；验证通过后更新密码并撤销该主体的会话。
- 邮箱修改也使用登录态下的验证码步骤。

### 学习路线：本节出处

| 编号 | 具体说法 | 出处 |
| --- | --- | --- |
| C07 | 找回、验证、重置完成后的会话撤销；改密当前密码核验；改邮箱验证码 | `refactor/server-java/src/main/java/win/clannad/mentorflow/account/LifecycleController146.java:31-44`；`LifecycleService146.java:80-111,113-153,170-225`；`NativeSessionService.java:290-317`；`refactor/server/keycloak/native-identity-verifier/EmailCodeReset.java:34-117` |
| S05 | 注册、找回、改密、改邮箱线上真人流程仍未完整验证 | `D:/MentorFlow/docs/project/CURRENT_STATE.md`：[ACC-02] 第 526–531 行；[ARCH-09] 第 300–324 行 |

你可以这样跟面试官说：**“找回密码不是只发一封邮件就结束：服务先发验证码，验证通过后给一个短期重置证明，再让身份系统更新密码。密码变更成功后，Java 会撤销旧会话，让新密码成为下一次登录的凭据；找回请求也不会告诉外部这个邮箱是否注册。”**[C07]

## 4. 注销：跨系统分步完成，失败时从检查点继续｜偶尔问

注销先确认当前密码，再分步清理服务器数据库中的账号业务数据和身份账号，并撤销会话；服务器不通过网络删除客户端文件，客户端收到注销完成确认后会清理该账号的本机受管目录。[C08][S07] **一句话层。**

用户先提交当前密码准备注销，服务返回操作编号和恢复凭证，但此时还没删账号；用户明确确认后提交第二步。流程撤销会话、清理 PostgreSQL 中该账号的业务数据、让 Keycloak 删除对应身份，最后再确认注销完成。服务器不通过网络删除客户端文件，客户端收到注销完成确认后会清理该账号的本机受管目录，包括其中的上传副本、设置及模型凭据；原来选文件的外部目录、导出文件和其他账号不在清理范围。[C08][S07] **三句话层。**

PostgreSQL 和 Keycloak 是两个独立系统，无法由一个 JDBC 事务同时提交或回滚。服务把操作状态和检查点持久化；如果数据库清理已完成、Keycloak 删除暂时失败，操作会保持可重试状态，原恢复凭证可继续同一操作。因而失败恢复不等于“所有内容都回到删除前”。[C08] **深一层。**

这像退租：前台先登记退租单，再清理门禁和房间账目，最后通知发证处注销住户身份。这个比喻能说明步骤与检查点；但现实里的两个系统没有一个共同的“撤销全部”按钮，所以恢复靠记录进度后续做，不是跨系统原子回滚。

### 为什么注销不能简单包进一个数据库事务？

- 注销先确认当前密码，再分步清理服务器数据库中的账号业务数据和身份账号，并撤销会话；服务器不通过网络删除客户端文件，客户端收到注销完成确认后会清理该账号的本机受管目录。
- 用户先提交当前密码准备注销，服务返回操作编号和恢复凭证，但此时还没删账号；用户明确确认后提交第二步。
- PostgreSQL 和 Keycloak 是两个独立系统，无法由一个 JDBC 事务同时提交或回滚。
- 服务把操作状态和检查点持久化；如果数据库清理已完成、Keycloak 删除暂时失败，操作会保持可重试状态，原恢复凭证可继续同一操作。

### 学习路线：本节出处

| 编号 | 具体说法 | 出处 |
| --- | --- | --- |
| C08 | 注销准备、确认、恢复凭证、撤销会话、清理数据、Keycloak 删除与重试检查点 | `refactor/server-java/src/main/java/win/clannad/mentorflow/closure/ClosureService.java:60-139`；`refactor/server-java/src/main/java/win/clannad/mentorflow/closure/PostgresClosureStore.java:20-80`；`refactor/server-java/src/main/java/win/clannad/mentorflow/account/ClosureSessionHooks146.java:12-32` |
| S07 | 原件不上传；COMPLETED 后客户端清理当前账号受管数据与凭据，不遍历外部原件或其他账号 | `docs/project/CURRENT_STATE.md:506-510`（[DATA-02]）；`refactor/desktop/account_closure_cleanup.py:31-65,75-111`；`refactor/desktop/account_closure_page.py:155-187` |

你可以这样跟面试官说：**“注销横跨 PostgreSQL 和 Keycloak，没法靠一个数据库事务把两边绑成原子操作。我先用当前密码准备一个可恢复操作，用户确认后逐步撤销会话、清理业务数据和删除身份；每一步记检查点，外部系统失败时可以继续原操作。”**[C08]

## 5. Spring Boot 的门卫、数据库账本与迁移权限｜常被问

Spring Boot 承接服务器账号业务，Spring Security 把每个请求交给统一认证入口，PostgreSQL 保存会话和业务状态。[C09] **一句话层。**

Keycloak 主要负责用户身份、密码和邮箱验证码；Java 服务负责 HTTP 接口、请求认证与授权、账号业务流程和数据库事务。验证令牌后，服务还要检查账号是否开放、用户是否属于请求的组织或工作区；令牌有效本身不代表有权读取任意空间。[C09][C10] Keycloak JWT 由资源服务器验证签名和声明；`mfn1_a_` 原生 access 则由 `NativeSessionService` 按会话族检查。[C09] **三句话层。**

业务持久层使用 Spring JDBC 和事务模板延续现有 SQL 与边界，数据库用行级安全（RLS）按事务绑定的账号/租户范围限制行访问。Alembic 迁移仍由专门迁移角色执行，Java 不在启动时自动建表。BUG-107 暴露过线上权限与测试夹具不一致：迁移角色对 `account_closure` 只有 `USAGE`、没有 `CREATE`，所以替换注销函数时报权限错误；修复是在替换函数时临时 `SET LOCAL ROLE mentorflow_closure`，没有给迁移角色额外授权。上线脚本后来逐步检查退出码，避免失败被管道吞掉。[C10][C11] **深一层。**

这次把服务器适合的账号和同步业务迁到 Java，原因是用户的求职方向是 Java + AI；模型提示与课堂编排仍留在 Python。选择依据是岗位方向和业务分工，不是“Java 一定比 Python 快”的性能结论。[S06]

在公寓比喻里，Spring Security 像门口先看通行证，业务权限像核对住户是否属于这栋楼和这间房，RLS 像每个账本抽屉只允许当前楼层管理员打开。它能帮助理解多层边界；但 RLS 不是认证系统，也不能代替应用层身份和成员关系校验。

### 账号请求的认证、授权、数据库访问和迁移各管什么？

- Keycloak JWT 由资源服务器验证签名和声明；`mfn1_a_` 原生 access 则由 `NativeSessionService` 按会话族检查。
- 验证令牌后，服务还要检查账号是否开放、用户是否属于请求的组织或工作区；令牌有效本身不代表有权读取任意空间。
- 业务持久层使用 Spring JDBC 和事务模板延续现有 SQL 与边界，数据库用行级安全（RLS）按事务绑定的账号/租户范围限制行访问。
- BUG-107 暴露过线上权限与测试夹具不一致：迁移角色对 `account_closure` 只有 `USAGE`、没有 `CREATE`，所以替换注销函数时报权限错误；修复是在替换函数时临时 `SET LOCAL ROLE mentorflow_closure`，没有给迁移角色额外授权。

### 学习路线：本节出处

| 编号 | 具体说法 | 出处 |
| --- | --- | --- |
| C09 | Spring Security 接口认证、JWT 声明检查、账号状态与组织/工作区成员解析 | `refactor/server-java/src/main/java/win/clannad/mentorflow/identity/IdentitySecurity.java:32-84,95-130`；`CompatibleJwtDecoder.java:94-165`；`refactor/server-java/src/main/java/win/clannad/mentorflow/identity/IdentityDirectory.java:7-12` |
| C10 | Java JDBC/事务、同步事务的租户作用域与 RLS；Java 不负责迁移 | `refactor/server-java/src/main/java/win/clannad/mentorflow/sync/SyncRepository.java:97-133,151-183`；`refactor/server-java/README.md:60`；`refactor/server/migrations/versions/0029_enterprise_tenancy_rls.py:520-540` |
| C11 | BUG-107 迁移权限问题、修复与复核结果 | `D:/MentorFlow/docs/project/CURRENT_STATE.md`：[BUG-107] 第 1117–1125 行；`refactor/server/migrations/versions/0045_account_closure_rag.py:25-35` |
| S06 | 服务器从 Python 分块迁到 Java 的用户原因与边界：求职方向 Java + AI，AI 编排仍用 Python | `D:/MentorFlow/docs/project/CURRENT_STATE.md`：[ARCH-09] 第 300–324 行 |

你可以这样跟面试官说：**“服务器账号链路现在跑在 Spring Boot 上。Spring Security 先认证凭证，业务再核账号和工作区成员，数据库事务把作用域交给 RLS 保护；持久层用 Spring JDBC，表结构由 Alembic 迁移管理。我们还用线上同款角色复现并修过一次迁移权限问题，没有为了让迁移通过而给角色多开权限。迁到 Java 是为了贴合 Java + AI 的求职方向，AI 编排仍留在 Python。”**[C09][C10][C11][S06]

## 学习路线：不用背｜基本不问

不必背每个 HTTP 路径、验证码状态码和令牌字段；能指出桌面账号入口、Keycloak 身份桥、会话服务、注销服务和迁移文件的位置即可。[C02][C04][C08][C10]
