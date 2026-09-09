# MentorFlow 邮箱与认证安全流程

E109，2026-09-10。认证仍由同一 VPS 上的 Keycloak 26.7.3 负责，nginx 负责公网入口；没有新增 auth service 或认证数据库。

当前是 **邮件 UAT 已通过、公开自注册关闭** 的状态。真实外部 SMTP 尚未接入，Mailpit 不能代替生产邮件投递。

## 1. Registration：注册

原生流程：填写用户名和邮箱 → 验证邮箱 → 设置密码 → 普通用户完成注册。可以把邮箱填写为新用户名，但保留旧账号原来的用户名规则。

- 绑定流程：`mentorflow-email-first-e109`，复制 Keycloak 内置 registration。
- 邮箱是原生用户资料校验的必填项；空邮箱请求已实测拒绝，不能绕过验证。
- `VERIFY_EMAIL` 是新用户默认必做动作，优先级 20；`UPDATE_PASSWORD` 优先级 30。原生 Password Validation 仍为 REQUIRED，并识别 Verify Email 默认动作，因此初始注册表单没有密码字段。
- 未验证账号没有密码、没有登录会话，不能取得正常可用的认证结果。
- 新默认组 `self-registered-learners-e109` 只授予 `LEARNER`。不修改原默认角色的组合，避免间接改变旧账号权限。
- 未声明的自定义属性关闭；伪造角色、分组、`emailVerified` 和初始密码字段的注册请求均不能提权或跳过流程。
- 内置 reCAPTCHA 动作已留在注册流程中，但保持 DISABLED；没有 site/secret key，不假启用。

兼容性边界：不强制旧账号补绑邮箱，不改旧账号凭据或角色。保留 `registrationEmailAsUsername=false`；启用 realm `verifyEmail=true`，并保留新用户默认 Verify Email 动作。初始审计中，已有邮箱的旧账号均已验证；原生 Verify Email 不对旧的空邮箱记录触发补绑。关闭旧账号登录时的自动 `VERIFY_PROFILE` 资料完整性提示，避免新增必填邮箱追溯锁住旧账号；注册和账号编辑的原生资料校验仍生效。这不是关闭密码验证或 MFA。

[Keycloak 26.7.3 原生注册密码动作](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/forms/RegistrationPassword.java) 同时支持 realm Verify Email 开关和默认 Verify Email 动作。

## 2. Verify email：验证与绑定邮箱

验证邮件包含 Keycloak 签发的限时动作链接，不含密码。先证明能访问邮箱，再设置密码。

更换/绑定邮箱使用内置 `UPDATE_EMAIL`，配置 `verifyEmail=true`。已验证：确认新邮箱前原邮箱保持不变，确认后新邮箱才生效。此动作不是旧账号默认必做动作。

Mailpit 仅接收 `@e109-uat.invalid` 的合成测试邮件，其他收件人被忽略；SMTP/UI/API 均不发布公网端口。测试 SMTP 凭据只在服务器私有 secret store 和 Keycloak 配置中使用，不写入源码、日志或报告。

## 3. Login：登录

密码仍只交给 Keycloak；MentorFlow backend 不新增密码或 hash 存储。

- Argon2；新密码长度 15–128，已接受 15、64、128 字符的纯小写密码。
- 不要求大小写、数字、特殊字符组合，不定期强制改密。
- 拒绝用户名/邮箱本身，以及固定版本公开常见密码词表。10 万项中 99,928 项由长度边界拒绝；其余 72 项与少量产品相关补充项使用原生 `regexPattern` 做大小写不敏感的完整字符串拒绝匹配，76 项均经 Keycloak 实测拒绝。它不是字符组合规则，也不是在线泄漏查询。
- 当前只读镜像没有 password-blacklists 挂载，故没有启用文件型 blacklist provider，也没有为此再重启 Keycloak。

密码策略依据 [NIST SP 800-63B](https://pages.nist.gov/800-63-4/sp800-63b.html)；拒绝词表的固定版本和校验值见 `realm-policy.json`，来源为 [SecLists](https://github.com/danielmiessler/SecLists/blob/c205c36a445bff37f8e58a9ec829105cd4975c58/Passwords/Common-Credentials/xato-net-10-million-passwords-100000.txt)。

暴力破解防护使用临时锁定：

| 参数 | 值 | 用意 |
| --- | --- | --- |
| failureFactor | 5 | 允许少量输错后开始锁定 |
| permanentLockout | false | 避免恶意请求永久锁死账号 |
| waitIncrementSeconds | 60 | 等待按失败次数递增 |
| maxFailureWaitSeconds | 900 | 临时等待最多 15 分钟 |
| quickLoginCheckMilliSeconds | 1000 | 检测快速连续失败 |
| minimumQuickLoginWaitSeconds | 60 | 快速失败至少等待 1 分钟 |
| maxDeltaTimeSeconds | 43200 | 失败计数使用 12 小时窗口 |

独立测试账号在第 5 次失败后被临时锁定，正确密码在锁定期间也不能登录。未对 owner 做锁号测试。锁定可能被用于拒绝服务，所以同时采用 nginx IP 限流。

已统一现有中英文登录错误文案，未知账号和锁定账号的可见响应一致。TOTP、WebAuthn/Passkey 注册能力可用，但均不作为旧 owner 的默认必做动作。

基础设施边界：桌面 OIDC 客户端位于 `mentorflow` realm；`master` 没有桌面客户端。应用账号未获得 `realm-management` 权限，master 管理账号未改造。

## 4. Forgot password：忘记密码

输入邮箱或原用户名 → 通用响应 → 如账号可接收邮件则发送重置链接。

存在、不存在账号的可见响应已逐字比较一致。不会返回“邮箱不存在”，也不发送或显示旧密码。请求频率由独立 email zone 限制。此设计遵循 [OWASP 忘记密码指引](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)。

SMTP 配置保持 provider-neutral：`SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_SECRET`、`SMTP_FROM`。部署脚本从 `SMTP_SECRET` 环境变量或 `SMTP_SECRET_FILE` 私有文件读取凭据；模板不含密钥。真实 relay 必须使用 STARTTLS 或 TLS。`admin.py apply-smtp` **不会开启公开注册**。

注意现有认证 issuer 仍为 `https://localhost:18080/realms/mentorflow`，本轮没有迁移它，以免破坏 backend/desktop 的现有认证约定。UAT 使用可信公网 TLS 连接发送同一 Keycloak 路径，保留原 issuer，并在测试客户端中映射传输地址；它不证明“关闭桌面客户端后从任意机器直接点击邮件”已经可用。接入真实 relay 前，仍须在实际桌面入口验证邮件链接可达性，不能把本轮 UAT 当作公网邮箱产品上线许可。

## 5. Reset：重置密码

邮件链接 → Keycloak 验证签名、有效期与使用状态 → 设置新密码 → 重新登录。

- 原生用户动作链接有效期 600 秒；已另行签发短时测试链接证明过期会被拒绝，随后恢复 600 秒。
- 已验证：使用成功的重置链接不能再次打开设置密码表单。
- 专用流程 `mentorflow-reset-password-only-e109` 强制重置后重新登录，禁用 Reset OTP 分支，不删除已绑定的第二因素。
- 邮箱绑定、重置及密码规则均只用合成账号验收；原有 11 个账号的资料、角色映射和凭据与备份一致。

## 6. Rate limit：HTTP 限流与代理信任

只对真实认证 POST 路由计数，不粗暴限制普通页面 GET、CSS/JS 或 OIDC 跳转。

| zone | 路由 | 速率 / burst |
| --- | --- | --- |
| e109_auth_token | OIDC token POST | 30/min，20 |
| e109_auth_login | login-actions/authenticate POST | 10/min，10 |
| e109_auth_email | registration、reset-credentials、验证/绑定邮箱提交 | 3/min，3 |

超额返回 429。账号较少的 staging 采用这个初始值；共享 NAT 下可能需要等待，不据此永久锁号。公网注册路径及 `prompt=create` 另有关闭门禁，UAT 只能来自服务器管理侧，最终 realm 注册开关也关闭。当前“注册无枚举泄漏”依赖该关闭状态；原生开放注册的重复账号错误仍需在将来的开放注册验收中处理，不能仅删除门禁就宣称防枚举完成。

nginx 覆盖 Keycloak 识别的转发头，清除客户端的 `Forwarded`、原始 URL/转发信息等旁路头。Keycloak 使用 `KC_PROXY_HEADERS=xforwarded`，仅信任实际代理地址 `172.18.0.5`，不信任整段 Docker 网段。这个地址来自当前容器网络，并已落入当前 compose 配置；以后代理地址变化需重新审计，不可扩大为全网信任。参见 [Keycloak 反向代理指引](https://www.keycloak.org/server/reverseproxy)。

Windows 外部测试已证明：伪造 IP/host/proto 不改变 issuer，轮换伪造 IP 仍触发同一限流；Keycloak 记录实际来源地址。来自非代理容器/宿主机的转发头也被忽略。原有 E108 更新 HTTPS 路径保持可用。

配置和可复跑脚本在 `deploy/enterprise/auth-email-security/`，脱敏验收回执见同目录的 `acceptance-evidence.json`。服务器回滚材料位于 `/opt/mentorflow/auth-email-security/e109/backup/`，包含 realm、flow/role/SMTP 配置及 nginx/容器/compose 快照；它们是配置回滚材料，**不是数据库备份**。本轮只进行一次已授权 Keycloak 重建（约 81 秒），没有重启 backend 或 PostgreSQL。`proxy_trust_restart.py` 是本次已消费授权的执行记录，不是可重复运行的常规部署步骤；其一次性标记不得删除或绕过。
