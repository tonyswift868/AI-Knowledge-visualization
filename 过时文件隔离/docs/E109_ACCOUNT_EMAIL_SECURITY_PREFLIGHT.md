# E109 account / email / security preflight

TASK_ID: `E109_ACCOUNT_EMAIL_SECURITY_PREFLIGHT_V1`

DISPATCH_ID: `MF-MAIN3-04-ACCOUNT-EMAIL-SECURITY-20260912-01`

BASE_SHA: `04faf2a9fbb50c5f9403dad5abf46354c9b53d82`

Branch: `worker/main3/04`; employee 04 / MAIN3; 2026-09-12.

**本轮结论：PARTIAL。** 客户端能力绕过已修复，后端拒绝、普通用户角色、
workspace continuity 和离线安全契约已准备可复验入口。原生邮件 token 行为、
真实 SMTP、新 issuer 下的公开入口关闭状态、原生 PostgreSQL RLS 未执行，不能计为 PASS。
`release/account-capabilities.json` 保持原样，`register=false`、`forgot_password=false`。

## PHASE 0

| 项目 | 核验结果 |
| --- | --- |
| top-level / cwd | `E:/Codex/Data/worktrees/b18c/MentorFlow` |
| 独立 worktree | `.git` 是指向 worktree 管理目录的文件，不是共享 Local |
| git-dir | `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow4` |
| common-dir | `E:/Codex/Projects/MentorFlow/.git`，仅共享对象库及 refs |
| index | `.../.git/worktrees/MentorFlow4/index`，与主工作区独立 |
| HEAD | 初始 detached，等于 BASE_SHA；独立 `.../MentorFlow4/HEAD` |
| clean / collision | 初始 clean；没有其他 worktree 占用 `worker/main3/04` |
| attach | 安全核验后执行 `git switch worker/main3/04` |

## 已核实的边界与本轮修复

| 范围 | 证据及结论 |
| --- | --- |
| 默认普通用户 | `test_account_workspace_bootstrap.py` 通过实际 ASGI + 隔离 SQLite 证明 organization/workspace role 为 LEARNER；realm policy 也声明 LEARNER，未审 live 用户 |
| continuity | 同一账户重复初始化、另一客户端重新发现、已有 profile/goal/plan/progress/assets 保留、并发初始化、回滚、撤销成员不复活均有测试 |
| 租户拒绝 | 双向 A/B workspace 越权、伪造 role/organization/workspace 请求、无认证拒绝；authorization negative pack 包含 404 个案例，其中 326 个拒绝 |
| RLS | FORCE RLS 源码契约、连接上下文清理、unsafe role 拒绝通过离线测试；SQLite 不证明 PostgreSQL RLS |
| capability | 打包配置严格布尔校验、启动安全门、禁用按钮及直接 signal/intent 调用均拒绝；新增内嵌页同源 registration/reset 链接检查，含重复 prompt、编码路径和分号路径 |
| 登录连续性 | HTTPS issuer origin、精确 loopback、PKCE、普通 authenticate、既有账号 required actions 仍有回归；不解码 action token，不增加认证服务 |
| 后端 | Account Core 没有 register/reset/verify-email 接口；新增已认证及未认证 GET/POST 拒绝测试，禁止重定向。它不代理 `/realms/`，因此这些测试不证明 live nginx/Keycloak 的原生入口关闭 |
| rate / brute-force | 从 checked-in nginx 提取规则测试 POST 路径匹配；token 30/min burst20、login 10/min burst10、email 3/min burst3；realm failureFactor5、临时等待60..900秒。仅源码契约，未发压测流量 |
| migration 测试 | 原有两项把 0034 写作 head 的测试已更新为 0035，仍逐段检查 0033→0034→0035 及实际 SQLite 升降级；没有修改任何迁移实现 |

本轮没有修改 issuer migration 文件、live hostname、nginx 配置、Cloudflare、
SMTP 配置、账户能力配置、main2、integration/main3 或 GitHub Actions。
所有写入由 Root 完成，没有创建子 Agent。

## 原生邮件行为的源码审计与缺口

Keycloak 26.7.3 的 [LoginActionsService](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/services/resources/LoginActionsService.java)
在分发 action token 前检查有效期、realm issuer、签名、用户及 client；检查过的代码
不是本轮运行的 JVM，不能等同现场验收。

[ResetCredentialsActionTokenHandler](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/actiontoken/resetcred/ResetCredentialsActionTokenHandler.java)
要求 realm 允许 reset，并声明 token 不可重复使用；
[VerifyEmailActionTokenHandler](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/actiontoken/verifyemail/VerifyEmailActionTokenHandler.java)
检查邮箱一致、已验证状态，并未覆盖
[AbstractActionTokenHandler](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/actiontoken/AbstractActionTokenHandler.java)
默认允许重复使用的设置。由此只能推断 verification 依赖状态拒绝，**不能声明具有与 reset
相同的严格 token 消费保证**。已验证后 replay、重新设置 required action 后 replay、
跨浏览器及并发行为都是必验项；若不符合严格 single-use，保持能力关闭并交由身份服务修复。

[VerifyEmail](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/requiredactions/VerifyEmail.java)
和 [ResetCredentialEmail](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/authenticators/resetcred/ResetCredentialEmail.java)
按 token 类型读取 lifespan；不能只看到通用 `actionTokenGeneratedByUserLifespan=600` 就
假定 verify-email/reset-credentials 的有效期也是 600。ResetCredentialEmail 对不存在、
缺邮箱及正常发送返回通用消息；时延、模板及注册重复身份路径仍需独立验证。

现有 `verify_email_uat.py` 是旧 issuer / Mailpit / admin mutation 工具，导入 `admin.py`
就会读取管理凭据并联系服务。本轮**没有导入或运行**它。已有测试包括 reset 成功、过期、
完成后 replay、forgot 可见文本比对及 brute-force，但缺少完整 verification 过期/重放、
两类 token 篡改/错误用途/并发、注册反枚举和真实投递证据。不得把旧 JSON 或 Mailpit
结果复制为新 issuer 的 PASS。

另一个独立缺口：现有 `auth-email-security/realm-policy.json` 和基础 realm 配置中的
`resetPasswordAllowed=true` 与桌面 `forgot_password=false` 并非同一个开关。
本轮保留这些部署文件原样，未查看 live 配置。新 issuer 验收开始前，必须由部署责任人
证明公开原生注册/找回入口关闭，且仅在已授权的合成验收范围允许测试；应用开关不能替代此门禁。

## 可复验的离线 harness

本轮 runner 实测：backend **50 passed / 2 skipped**、desktop **88 passed**、
policy **9 passed**，合计 **147 passed / 2 skipped**。两个 skip 均为未分配隔离
PostgreSQL 的显式 gate，非功能测试忽略；没有 SMTP 或 live Keycloak 操作。

在本 worktree，使用具有 backend/desktop 测试依赖的 Python：

```powershell
& 'E:\Codex\Temp\mf-e1s1-rp01-test-venv\Scripts\python.exe' scripts/e109_account_email_preflight.py
```

可换为同依赖的 Python 路径。runner 的选择器固定为源码内 `GROUPS`，每组最多 240 秒。
临时目录在 `E:/Codex/Temp/MF-MAIN3-04-preflight-*`，其中 `receipt.json` 只含固定状态及计数。
它清除测试 DB DSN / pytest 注入参数，使用本地模式，不启用 pytest 外部插件自动加载，
不会执行旧邮件 UAT。backend 预期恰好 2 个原生 PostgreSQL 测试跳过；多跳过、xfail、
异常、超时或失败都不能计为离线 PASS。输出永远将真实邮件和原生 token 测试标记为 NOT_RUN。

原生 RLS 的后续入口（本轮未运行）：

- `backend/tests/test_enterprise_tenant_persistence.py::test_real_postgres_rls_is_fail_closed_and_pool_safe`：
  专用隔离空库、兼容当前迁移链的迁移角色和 `MENTORFLOW_RLS_DSN`。此测试含角色 DDL 和
  migration，只能使用明确分配的 disposable PostgreSQL；不得指向 live 库。
- `backend/tests/test_account_workspace_bootstrap.py::test_real_isolated_postgres_first_use_rls_if_provisioned`：
  已迁移至 0035 的隔离库及 `E1_WORKSPACE_BOOTSTRAP_TEST_DATABASE_URL`，必须为非 superuser、
  无 BYPASSRLS 的 application role。验证省略 WHERE 的直接 SQL 仍不能看到另一用户的 workspace。
- DSN 从受控环境注入，禁止写进命令行、日志、提交或报告；完成后以角色/表级只读检查确认 FORCE RLS。

## Post-cutover real email acceptance checklist（未执行）

本清单是后续单独授权的执行计划，本轮不执行。发行能力开关在验收期间继续为 false。
先取得 01 的真实 NEW issuer cutover 通过证据，固定实际运行的 Keycloak image digest/version、
新的 HTTPS origin、realm/client/audience 和部署责任人批准的合成账号测试通道。
不得自动改 hostname、代理路由、TTL、realm flow 或公共能力，也不得重用旧 UAT 的硬编码地址。

范围：最多 3 个专用合成账号 A/B/C，最多 12 封真实邮件，单次最多 30 分钟、120 次 HTTP
请求。任何界限达到即停并记录 PARTIAL；不扩大收件范围，不重试真实发送失败，不访问正式用户。
邮箱仅限验收人员控制的专用收件箱。每次网络请求有 15 秒超时、最多 5 跳同源重定向；禁止
自动跟随跨 origin action link。密码/action link/token 只存在内存或受控用户会话，不写 evidence。
不保存 HAR、cookie、截图中的链接、SMTP 密钥、邮箱密码、真实邮箱地址或用户资料。

| Case ID | 动作 / 成功判据 |
| --- | --- |
| GATE-01 | 真实 NEW issuer discovery、签名验证及 cutover 责任人回执一致；旧 issuer link 不因重写域名而被接受。固定运行版本与本清单源码版本差异，未核对即停止 |
| GATE-02 | register=false / forgot_password=false 的实际候选客户端按钮、直接调用、内嵌页链接关闭；未认证及已认证 backend 均拒绝对应 API。由公开非管理网络检查原生注册/找回 URL 均不可触发业务或邮件；未关闭则停止，不以 UI 灰按钮代替 |
| SMTP-01 | 经单独授权的合成通道请求验证/重置邮件；以真实外部收件箱实际收到且 action link origin 等于 NEW issuer 为证据，区分入站时间与 provider 接受时间。Mailpit/mock/provider-accepted-only 均不算到达 |
| REG-01 | A 的注册必须有邮箱，验证前无登录授权/业务 workspace；提交伪造 OWNER/ADMIN/groups/emailVerified 不得提权。验证后正常登录，持久化角色仅 LEARNER |
| VERIFY-01 | 新鲜 A verification link 完成验证且仅改变 A；单独浏览器 B 的会话不得被当成 A 登录，邮箱验证结果不得作用于 B |
| VERIFY-02 | 在内存篡改签名、错误 token 类型、错误 issuer/client/recipient 场景拒绝；不完成验证、不建立会话、不创建 workspace。错误 client 不要求假装是签名通过的新 token |
| VERIFY-03 | B 的未使用 verification token 等到原生 exp+容差后再使用，必须拒绝且 B 保持未验证；记录寿命秒数，不记录 token/claims。核对 token 类型 override，超过获准 600 秒上限即停止，不临时缩短 live TTL |
| VERIFY-04 | A 已完成链接在原浏览器、全新浏览器均重放，不能再次完成动作或获得授权；对 C 的两个并发使用最多一个完成。若因已验证状态返回安全提示，标记 STATE_IDEMPOTENCE，严格 SINGLE_USE 仍需消费保证证据，不能混记 PASS |
| RESET-01 | A 请求找回、真实收到、使用新鲜 token 更新密码；旧密码拒绝，新密码可独立登录；force_login=true 不允许完成重置即自动获得业务会话 |
| RESET-02 | 分别拒绝篡改/错误用途/错误 issuer/client/recipient/过期 token；不得改变密码或产生授权。原生过期使用 exp+容差，不改系统时钟，不改 realm TTL |
| RESET-03 | 成功提交后原链接再次 GET 及再次提交拒绝；另一对并发提交最多一个改变密码，后续任何 replay 都不能再次修改。仅“没看到表单”不是充分证据，必须检查最终密码状态和无额外会话 |
| ENUM-01 | known / unknown / unverified / disabled 的 forgot 请求同状态、同归一化可见消息、同重定向类别，不回显存在性；不存在的账户不创建记录、不发真实邮件。最多两轮交错样本，报告时延范围；小样本不声明统计性抗侧信道 |
| ENUM-02 | 重复注册的邮箱/用户名与新身份的可见响应分别比较；如明确泄露存在性则 FAIL，不能用 forgot 的通用文案代替注册反枚举。检查 verify resend 也不泄露其他账号存在性 |
| RATE-01 | 在独立验收来源短突发测试 token ≤24、login ≤16、email ≤8 个 POST，每组出现首个 429 即停；不自动重试429。不能影响普通 GET/CSS/redirect，伪造 X-Forwarded-For 不绕过限制；provider 接收/邮件总量必须遵守预算 |
| RATE-02 | 专用合成用户最多6次错误密码观察第5次后的临时锁定，记录实际等待和恢复，不能永久锁定；默认 LEARNER、未注册用户通用提示和其他合成用户独立性都保留。达到预算无锁定则 FAIL，禁止无限压测 |
| CONT-01 | A 注册/验证/重置前后在可用阶段由应用 discovery/initialize/metadata 读取；相同身份已有空间/goal/plan/progress/assets 不丢失、不复制、不换属主；新设备发现同一空间，B 双向访问403，伪造身份字段422 |
| RLS-01 | 隔离 PostgreSQL 上以真实 application role 执行原生 RLS 两项 gate；不能用 SQLite/API拒绝代替。新 issuer 的账户连续性同时依赖01已确认的 identity binding，不改迁移实现 |
| END-01 | 只清理已登记的合成账号/邮件/会话；释放测试限流通道；再次核验两项 release capability 仍为 false，公开原生入口仍关闭。所有必验项通过且责任人接受真实邮件证据后，另行处理能力启用 |

证据仅记录 Case ID、UTC、版本/digest、合成别名 A/B/C、状态码、布尔状态变化、
计数、时长、固定失败码和经授权的受控证据引用。禁止原始响应正文、token、密码、邮箱地址、
正式用户数据。SMTP key 没有出现在任何输出里不是投递成功证据。

最终验收必须分别填写 verification/reset 的 expiry、wrong、expired、reused、concurrent、
single-use，注册/找回 anti-enumeration，实际 rate/brute-force、原生 RLS 和 continuity。
任一 PENDING/NOT_RUN/FAIL 或只达到 state idempotence，均不得声明完整 READY 或开启能力。
