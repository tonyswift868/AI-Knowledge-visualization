# Runtime lease binding V2 receipt

Dispatch: MF-MAIN3-02-EPHEMERAL-RUNTIME-LEASE-BINDING-20260913-10.
Worktree: E:/Codex/Data/worktrees/08ac/MentorFlow, branch worker/main3/02.
Starting HEAD: 6eab5be998172861022da6aa537daa4c3e0539b1.
Read-only integration: d73f3f9850fc0ff99ce874eaf8166f988ba3637b.

**STATUS = BLOCKED_CROSS_OWNER_PRODUCT_FIX_REQUIRED.** The source harness repair
passes its regression checks. Actual frozen native login still fails after the
new endpoint and CA have been proven inside the frozen child's environment.
No authentication algorithm or product auth module was modified. The remaining
failure requires cross-owner diagnosis; this receipt does not establish which
product component is defective.

## Configuration divergence

CURRENT test: MAIN3_CROSS_DEVICE_AB_V1.
Build source: 2906893fe7d77e6ff70cf8efc374953423eb2450.
Build tree: aa9c757bb4d9c1e371f1994f9f3867f1c78742df.
Artifact SHA256:
7db576d4e89f184ea33568a0e469edebf1d50f5eb9322903e85ef7678ff37c46.

| Safe field | Immutable candidate receipt | Current runtime lease |
| --- | --- | --- |
| issuer | https://127.0.0.1:56005/realms/mf-native-5ebf8362af | https://127.0.0.1:58788/realms/mf-ab-3c58f23a06 |
| api_base_url | https://127.0.0.1:56006 | https://127.0.0.1:58789 |
| tls_ca_path | E:/Codex/Temp/CQ/build-2906893fe7d7/mf-native-9a1a6528d63d/ca.pem | E:/Codex/Temp/AR-20260913-03/public/ca.pem |
| tls_ca_sha256 | a31ad66c5c1002ca0b680093139dd5ca31f33e6e7c9d193d1924be41121329d2 | f29e91b623a764da7af123537d8250f917223fa23641082e6fcc3682ced2c81c |

CONFIG_MATCH_BEFORE = NO. The original launch_exact spawn arguments were captured
without executing a process and contained the candidate-pinned values. This is
a proven default-launch defect. However, dispatch 09's external acceptance
driver explicitly replaced that configuration with the current lease before
real Popen. Therefore the divergence cannot truthfully be called the proven
cause of dispatch 09's native-login failure. Historical process memory was not
available for retroactive inspection.

## Real frozen retest

Lease MF-AB-RUNTIME-20260913-03-08575775d6 remained READY_FOR_ACCEPTANCE, expiring
2026-09-13T15:17:59+00:00. The exact current EXE was admitted by the modified own
branch harness against the read-only integration publication. Only owned Device
A was reset and launched. The acceptance driver passed Popen the unmodified
environment supplied by launch_exact; it did not inject/replace configuration.

Real bootloader PID 85784 and frozen child PID 40172 were inspected via bounded
ReadProcessMemory of their PEB environments. Only safe allowlisted transport
fields were recorded. Both held the current runtime configuration and lease ID.
The frozen child additionally held the applied MENTORFLOW_DESKTOP_OIDC_ISSUER,
MENTORFLOW_ENTERPRISE_API_BASE_URL and SSL_CERT_FILE matching that lease. This
proves entrypoint application, not merely a parent-side proposed environment.

First paint = PASS. A single completed native-login attempt used the disposable
runtime credentials, with the password masked. Result: “登录暂时无法完成，请检查网络或账号服务后重试。”
FROZEN_NATIVE_LOGIN_RETEST = FAIL. No full A/B or cloud recovery PASS is claimed.

Only the test window was closed. Backend PID 90892, Identity PID 23780 and
PostgreSQL PID 3988 remained running; HTTPS startup health returned 200. Public
CA and server/backend private-key files remained present. LATEST still matched
its named publication. No keys, services, ordinary credentials or real user data
were cleaned or modified.

Local evidence directory: E:/Codex/Temp/MF-MAIN3-02-RUNTIME-LEASE-10.
Files: config-before.json, spawn-proof.json, child-environment-proof.json,
native-login-result.json, native-login-failed.jpg, preserved-runtime.json.
No password, token or private-key material is recorded in these evidence files.

## Source validation

The full Workbench suite passed 177 tests before the final additional CA-secret
and file-pinning regressions. The resolver/device follow-up passed 64 tests;
three final malformed JSON cases separately cover duplicate keys, nonfinite
constants and excessive nesting. Exact source/tree/hash admission, supersession,
separate local state, reset ownership, and ordinary launch behavior remain tested.
Security coverage includes wrong test/source/tree/artifact, old/superseded and
expired leases, CA tampering, untrusted paths, reparse authority, pointer-only
edits, absent named publication, ownership failure, size limits, arbitrary-file
and environment injection, private keys, TLS bypass, expiry before spawn and
deny-write/delete through spawn. New contract-only receipts and legacy immutable
receipts are both exercised. The final command results and delivery commit are
reported in the dispatch response.
