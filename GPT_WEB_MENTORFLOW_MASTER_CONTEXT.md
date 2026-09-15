AUTHORITATIVE_FOR = MAIN3_CURRENT
CHATGPT_READ_BRANCH = control/main3-current
LAST_VERIFIED_SHA = 1b8f30862f24ac989a6fbdef2f0819afb44a7b06
LAST_VERIFIED_TREE = fd285c9e42af0fbc4b2205ec479bc8416296ee98
UPDATED_AT = 2026-09-15

# MentorFlow Main3 — current review authority

Authority: MF-MAIN3-A-CONTROL-CONVERGENCE-HANDOFF-SEAL-20260915-25.
上述 SHA/tree 是最终经过产品源码验证的 convergence source；随后的文档 publication commit
不能把自身 SHA 写进自身内容。最终远端 publication HEAD 由 Git ref 与交付回执给出，
[生成 handoff](docs/handoff/WEB_SESSION_LATEST.md) 记录其确切 source snapshot。
这一区分不允许把旧 artifact 冒充新 exact-source candidate。

旧的“本地单用户、禁止账号/登录/JWT/多租户/云账户/云同步”规则已被 Main3 明确取代。
不得再据此否定现有 Keycloak、PostgreSQL、租户隔离和 cloud sync 实现。

## Current source, boundaries and evidence

- 唯一 ChatGPT 读取/审阅远端：`control/main3-current`。重复 handoff 分支经 SHA/PR 检查后删除。
- `integration/main3` 是另行授权的正式阶段分支，本轮不推进；远端仍为
  `a0b40f0c3c4972668e4c227b5287344c60c863b9`。A 旧本地 integration ref 保留 `efb354c`。
- GitHub default `main2` 为 `04faf2a9fbb50c5f9403dad5abf46354c9b53d82`，不推进。
  main/main1/main2、worker refs、tags、formal main3 不变；不运行 GitHub Actions。
- efb354c control PRE02 保留。03 exact delivery `80b619d263d81f5e684af67528da898cbfbda178`
  / tree `c78a18ed2e9734d91a1de933a4be78aa7bf1531f` 的必要 sealing delta 已收拢。
  消费 dfe77c9、2a9be82、80b619d；不合并整个 worker history，不带入 R2 preference/UI seam。
- 原生用户名/密码 → Backend → backend-only authenticated verifier → Keycloak → session → workspace。
  不降级 TLS/mTLS、Bearer authority、candidate/hash 检验；不使用 password/direct grant。
- PostgreSQL head/expected：`0040_admin_overview_aggregate`；app-role RLS 与 account scope fail closed。
- A/B evidence 是 real native PostgreSQL + RS256 validated HTTP + independent Desktop client processes。
  覆盖同 workspace、plan/task、progress/review、learner/history、settings、asset metadata、server revisions，
  A 进程退出后 B 恢复、B→A 更新、B 重启与 foreign-account 403。
  A-only physical payload 不复制；B 为 `MISSING_ON_THIS_DEVICE`，结构化计划/进度仍可读取。
- 这些是 registered sync projection transport 证据，不是完整 domain business write/UI/Frozen acceptance。
  generic projection 不是第二份业务 truth；完整产品路径仍需独立验收。
- PRE02 source checks覆盖 password mutation isolation、reset consumer、startup observation、email HealthRegistry、
  TLS/AUTH/NOOP classification/deadline、admin 200/member 403、revision、secret rejection。
  SMTP readiness 不等于真实 delivery。详见 [本轮证据](docs/governance/receipts/20260915-control-convergence.json)。
- FULL_ACCOUNT_REHYDRATION = NOT_YET_COMPLETE；PRODUCT_ACCEPTANCE = PENDING。
  本轮没有新 Frozen build、Workbench CURRENT 更新、用户测试、真实迁移或旧 88 plans 修改。

## External authority / human UAT blockers

真实邮箱 inbox receipt、verify/reset link consume、replay、expiry、same-user continuation 尚未完成。
未找到已批准 QQ inbox resolver；不能借用无关 SSO/不明 credential。
TonySwift 精确 SUPER_ADMIN 绑定未获 authority；历史 TEST `tonyswift555` 不可替代 `tonyswift`。
本轮真实 SMTP send = 0；TonySwift account mutation = 0；secret exposure = 0。
未来真实发送、角色绑定、续期环境、用户验收须新的明确授权。本机旧 TEST 租约已到期，不能直接交用户。

## Main3 Server / Deployment Authority

2026-09-15 只读 SSH/docker/config 核实。下面区分 retained VPS staging 与本机 disposable TEST；
服务 running 不证明当前 Main3 源码部署，配置 URL 不证明当前可登录。
绝不在本文件保存密码、token、cookie、API/SMTP/client secret、私钥值。只允许 resolver/reference。

### Retained VPS staging (observed, not a new Main3 deployment)

| Field | Verified value / scope |
| --- | --- |
| ENVIRONMENT_NAME | mentorflow-e1-preview |
| ENVIRONMENT_PURPOSE | Retained Account Core staging; mixed retained releases, no deployment this task |
| SERVER_HOST_OR_ALIAS | mentorflow-admin@89.45.45.28 (approved SSH connection) |
| SERVER_IP_OR_FQDN | 89.45.45.28; auth.clannad.win is configured identity proxy hostname |
| SSH_USER / SSH_PORT | mentorflow-admin / 22 |
| SERVER_OS | Ubuntu 24.04.4 LTS |
| DEPLOYMENT_ROOT | /opt/mentorflow |
| BACKEND_DEPLOYMENT_PATH | Compose labels: /opt/mentorflow/releases/21337c1eda64f7e087fcade77c3d4cd7dce4a0af-exact-source/deploy/enterprise/staging/rp05; mounted entrypoint: /opt/mentorflow/artifacts/gatea-native-20260913-08/source/deploy/enterprise/staging/rp05/runtime-entrypoint.py |
| FRONTEND_OR_DESKTOP_ARTIFACT_PATH | Static updates: /opt/mentorflow/update-static/e108; desktop candidate remains local release/distributable/MentorFlow/MentorFlow.exe, not newly qualified here |
| SERVICE_MANAGER | Docker; Compose project mentorflow-e1-preview |
| BACKEND_SERVICE_NAME | mentorflow-e1-preview-backend-1 (running) |
| KEYCLOAK_SERVICE_NAME | mentorflow-e1-preview-keycloak-1 (running) |
| POSTGRES_SERVICE_NAME | mentorflow-e1-preview-postgres-1 (running) |
| NGINX_OR_REVERSE_PROXY | mentorflow-e1-preview-edge-1; mentorflow-e1-preview-oidc-loopback-proxy-1; update static nginx |
| PUBLIC_BASE_URL | https://89.45.45.28; https://auth.clannad.win (configured routes, not current auth acceptance) |
| BACKEND_INTERNAL_URL | http://backend:8000 |
| POSTGRES_HOST / POSTGRES_PORT | postgres / 5432, internal Docker only |
| POSTGRES_DB / POSTGRES_USER | mentorflow / mentorflow_app; separate identity DB/user mentorflow_keycloak; postgres is administrative role |
| KEYCLOAK_PUBLIC_URL | Configured issuer https://localhost:18080/realms/mentorflow; externally routed https://auth.clannad.win/realms/mentorflow is NOT an approved issuer cutover |
| KEYCLOAK_INTERNAL_URL | http://keycloak:8080; OIDC loopback TLS transport https://localhost:18080/realms/mentorflow |
| KEYCLOAK_REALM | mentorflow (runtime DB query verified); master administrative realm |
| KEYCLOAK_CLIENT_IDS | mentorflow-api, mentorflow-bff, mentorflow-desktop, mentorflow-web, mentorflow-worker; built-in account/account-console/admin-cli/broker/realm-management/security-admin-console also present |
| SMTP_HOST / SMTP_PORT / SMTP_FROM | Approved transport smtp-relay.brevo.com / 587 STARTTLS / no-reply@clannad.win; not a claim every retained service uses it |
| MENTORFLOW_SMTP_CONFIG_REFERENCE | E:/Codex/Secrets/MentorFlow/smtp.env; approved resolver brevo_smtp.load_private_store; no values from secret fields retained |
| TLS_TERMINATION_POINT | edge nginx 443→8443; backend-only native mTLS independently enforced |
| TLS_CERT_LOCATION_OR_MANAGER | Edge /run/secrets/e108-ip-tls/active/fullchain.pem; hostname /run/secrets/e109-cloudflare-origin/cert.pem; loopback /run/secrets/tls_certificate; Docker secret volumes |
| TLS_RENEWAL_MECHANISM | mentorflow-e108-ip-renew.timer → /opt/mentorflow/update-static/config/renew-ip-cert.sh (last observed status 0); Cloudflare origin renewal owner/schedule UNKNOWN |
| HEALTH_ENDPOINTS | /health/live, /health/startup, /health/ready; diagnostic status not UAT |
| BACKEND_STATUS_COMMAND | sudo -n docker inspect --format '{{.State.Status}}' mentorflow-e1-preview-backend-1 |
| BACKEND_RESTART_COMMAND | sudo -n docker restart mentorflow-e1-preview-backend-1 (reference only; requires separate writer authority) |
| BACKEND_LOG_COMMAND | sudo -n docker logs --since 10m mentorflow-e1-preview-backend-1 (local restricted review/redaction, never paste raw logs) |
| KEYCLOAK_STATUS_COMMAND | sudo -n docker inspect --format '{{.State.Status}}' mentorflow-e1-preview-keycloak-1 |
| KEYCLOAK_RESTART_COMMAND | sudo -n docker restart mentorflow-e1-preview-keycloak-1 (reference only; not executed) |
| KEYCLOAK_LOG_COMMAND | sudo -n docker logs --since 10m mentorflow-e1-preview-keycloak-1 (restricted/redacted) |
| POSTGRES_STATUS_COMMAND | sudo -n docker exec mentorflow-e1-preview-postgres-1 pg_isready -U postgres |
| DEPLOY_COMMAND | scripts/enterprise/staging_release_orchestrator.py deploy; requires exact package, live preflight, writer authorization and secret references; retained mixed stack is not auto-replaced |
| ROLLBACK_COMMAND | staging_release_orchestrator.py rollback-drill requires exact A/B packages with identical migration heads; source rollback is not DB/identity rollback |
| BACKUP_LOCATION_OR_PROCEDURE | Observed /opt/mentorflow/backups and /opt/mentorflow/state/mentorflow-e1-preview/backups; documented separate custom-format application/identity pre-migration backups and isolated restore verification; latest backup integrity NOT_REVERIFIED |
| CURRENT_REMOTE_REPO | https://github.com/tonyswift868/AI-Knowledge-visualization |
| CURRENT_REVIEW_BRANCH | control/main3-current |

SSH credential reference: `E:/Codex/Secrets/MentorFlow/ssh/mentorflow-e1-ops-ed25519` (value never recorded).
Retained Keycloak compose root differs from Backend: release `1531c65116063ec606ed1c623acd29fb68e9e8b7-exact-source`;
edge uses `bc9bcf5dc7ca1a8e690abba730eeef2542ab049d-exact-source`; PostgreSQL persistent data is
`/opt/mentorflow/state/mentorflow-e1-preview/data/postgres`. Do not infer homogeneous source from running containers.
Deployment/restore procedures: [staging orchestrator](docs/enterprise/staging-orchestrator.md),
[RP05 entry](deploy/enterprise/staging/rp05/README.md). Commands are documentation, not authorization to execute.

### Disposable native Windows TEST (historical readiness, lease expired)

- ENVIRONMENT_NAME: MF-A-UAT-23; purpose isolated Account MVP acceptance, not production or final migration destination.
- Host local Windows builder; runtime processes uvicorn / Keycloak 26.7.3 Java21 / PostgreSQL17.6, not Windows registered services.
- Backend `https://127.0.0.1:53437`; Identity `https://127.0.0.1:53436`;
  realm `mf-ab-f0daac9477`; clients mentorflow-desktop and mentorflow-account-lifecycle.
- PostgreSQL `127.0.0.1:51602`, application `native_sync`, identity `identity_acceptance`, app role `mentorflow_app`.
- Runtime root `E:/Codex/Temp/AR-FL-20260913-04`; config/evidence `E:/Codex/Temp/MF-A-UAT-23`;
  TLS paths `E:/Codex/Temp/MF-A-UAT-18/tls`; secret refs outside repo. Never copy these into an artifact.
- Latest lifecycle-config lease expired `2026-09-15T10:09:37Z` (18:09:37 Beijing).
  Latest restore receipt source efb354c is historical readiness only. No renewal/restart/send this task.
- SMTP readiness config reference `E:/Codex/Temp/MF-A-UAT-23/smtp-config.json`, secret env name `MF_TEST_SMTP_SECRET`.
  Approved diagnostic egress connected 127.0.0.1:45350→Brevo587, preserving Brevo TLS hostname;
  no MAIL/RCPT/DATA in readiness probe. This is not a production networking policy.
- Coordination `acceptance-runtime/LATEST.json` can be stale; validate lease, TLS and exact candidate before any future UAT.
- Fresh A/B tests in this task use separate disposable loopback PostgreSQL and ephemeral RS256/JWKS issuer,
  never the resident identity/account data. No native-password or GUI-UAT claim from this harness.

SERVER_INVENTORY_COMPLETE = PARTIAL_VERIFIED
SERVER_OWNER_INPUT_REQUIRED = Cloudflare origin certificate renewal ownership/schedule; future deployment writer/backup acceptance; approved inbox resolver and exact role authority.
Existing SSH runtime, compose labels/mounts, realm metadata, proxy config, renewal timer, local approved configs,
and repo runbooks were checked. Unknown operational approval is not invented from configuration presence.

## DO_NOT_REDO / NEXT_TASK

Do not recreate a ChatGPT review branch, reconsume old deliveries, rerun ICU/Frozen builds, modify Workbench CURRENT,
reset credentials, send real email, migrate user data, cut over issuer, or promote integration/main3/main2.
Do not turn old Frozen failure into the current source blocker or source transport PASS into product acceptance.
Next work requires explicit Owner dispatch for remaining external authority and human UAT gates; this task stops after control publication.
