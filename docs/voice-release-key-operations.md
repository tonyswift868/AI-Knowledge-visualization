# MentorFlow 语音/引擎发布密钥操作规程

MentorFlow 的离线 voice pack、engine pack 与发布 catalog 使用 Ed25519 对 manifest 的**原始字节**签名。发布者只在隔离签名环境持有 leaf 私钥；应用、安装器和用户机器只接收 leaf 公钥、public trust receipt 与根签名的 release trust policy。receipt 的自签名只证明内容与该 leaf 公钥内部一致，**不是独立信任根**；独立信任只能从随应用发布的固定 root registry 开始，再由已安装 root 验证 policy 对 leaf key 的授权。这个流程不要求最终用户配置 TTS API，也不允许把发布私钥带进应用。

配套工具为 `scripts/manage_voice_release_keys.py`。无参数、`--plan` 和仅填写生成参数的调用都不写文件；只有显式 `--generate` 才创建密钥。本仓库没有、也不应自动生成或提交真实生产私钥。

## 安全边界

- 私钥根只能是新的、绝对的 `E:\...` 目录。工具拒绝 C 盘、相对路径、UNC、路径逃逸、ADS、Windows 设备名、repo 内目录、TEMP/TMP、已存在目标，以及任一路径链上的 symlink、junction/reparse point。
- 私钥文件名由 192-bit CSPRNG 随机值生成，不可由 key id 推测。它只记录在受保护索引 `.mentorflow-key-index.json` 中；public trust receipt、stdout、stderr 和应用配置都不包含该文件名或私钥路径。
- 文件使用 Win32 `CreateFileW(CREATE_NEW)` 独占创建，绝不覆盖。创建句柄不共享 DELETE；工具在句柄仍打开时记录并复核 VolumeSerial、FileId、对象类型、link count 和 size。读操作同样使用固定句柄并拒绝写/删除共享。
- 生成期间根目录也持续由不共享 DELETE 的句柄固定。PowerShell 无法直接使用父 Python 进程的 HANDLE，因此 ACL 子进程按路径操作时，父进程持续持有该句柄，并在 ACL 前后通过原句柄及重新打开的路径比对 FileId。
- reparse point 和普通文件的多 hardlink 状态均 fail closed。受保护索引把私钥、公钥和 receipt basename 绑定到 VolumeSerial+FileId；公钥与 receipt 还绑定内容 SHA256。
- 失败清理只通过句柄删除本次记录的同一 VolumeSerial+FileId。如果路径已被替换，工具返回清理失败并保留替换物，绝不会按路径删除未知对象。
- 当前 builder 使用未加密 PKCS#8 PEM。严格 ACL 不是离线备份加密的替代品；备份介质仍须使用全盘或加密容器。
- `--verify` 必须同时收到 `--trust-policy` 与 `--trust-policy-signature`，并且只能使用代码/安装位置推导出的固定 root registry。CLI 没有 registry 路径覆盖参数。`--trusted-fingerprint` 与 `--expected-key-id` 只能在完整 trust chain 之上进一步收窄，绝不能单独建立信任。

## 精确 ACL 合同

生成根、私钥、公钥、public trust receipt 和受保护索引都必须满足同一 fail-closed 合同：

- owner 是当前执行用户 SID，DACL inheritance 为 protected；
- 只有当前用户 SID、`SYSTEM` (`S-1-5-18`) 和内置 `Administrators` (`S-1-5-32-544`) 三条 Allow ACE；
- 每条 ACE 的 rights 必须精确等于 `FullControl`，`IsInherited` 必须为 false，Propagation 必须精确为 `None`；
- 目录 ACE 的 Inheritance 精确为 `ContainerInherit,ObjectInherit`；文件 ACE 精确为 `None`；
- 不允许额外、缺失、重复、Deny、继承或弱化的 ACE。设置后和每次离线签名读取前都会复读验证。

## 1. 先做 plan

下面只展示命令格式，不代表已经生成过生产密钥。`rotate-after` 是该密钥最后允许签名的 UTC 日期；验证可继续到计算得到的 `overlap-until`。示例显式给出 `--rotation-start`；若省略，工具统一使用当前 UTC 日期，不使用机器本地日期。

```powershell
$keyRoot = 'E:\MentorFlow_Release_Keys\release-2026-q3'
$keyId = 'mentorflow-release-2026-q3'

python scripts/manage_voice_release_keys.py --plan `
  --output-root $keyRoot `
  --key-id $keyId `
  --purpose mentorflow-voice-and-engine-release `
  --rotation-start 2026-08-08 `
  --rotate-after 2026-11-08 `
  --overlap-days 30
```

plan 会校验路径、key id、用途和轮换窗口，只返回 `writes: false`、public basename 与 ACL 策略。输出根的父目录必须预先存在，目标根本身必须不存在。

测试 key 必须同时使用 `--test-key` 和 `test-` 开头的 key id；生产 key 必须两者都不使用。前缀与 `test_only` 不一致会被拒绝。

## 2. 生产生成必须由发布负责人显式执行

在隔离发布机器复核 plan 后，发布负责人才能把 `--plan` 改成 `--generate`。开发服务器、应用进程和 CI 不得自动调用生成操作。

```powershell
python scripts/manage_voice_release_keys.py --generate `
  --output-root $keyRoot `
  --key-id $keyId `
  --purpose mentorflow-voice-and-engine-release `
  --rotation-start 2026-08-08 `
  --rotate-after 2026-11-08 `
  --overlap-days 30
```

成功后，受保护根内有四类对象：随机命名私钥、公钥 PEM、`*.public-trust.json` 和受保护索引。控制台只返回 status、key id、purpose、raw public-key fingerprint、公钥 basename、receipt basename 与完整 public receipt；没有绝对目录、私钥标识、私钥 basename 或私钥内容。

public trust receipt 自身也由同一 Ed25519 私钥签名。该签名用于完整性检查，不建立来源信任。receipt 绑定：

- schema、algorithm、key id、purpose、status 与 `test_only`；
- raw 32-byte Ed25519 public key、PEM basename/PEM SHA256 与 raw-key SHA256 fingerprint；
- `starts_on`、`rotate_after`、`overlap_days`、计算出的 `overlap_until`；
- 与 rotation 精确对应的 signing/verification validity；
- public-only 的 `application_trust.catalog_fields`；
- canonical receipt payload SHA256 与 64-byte Ed25519 self-signature。

应用侧只复制 receipt 中的 public `catalog_fields` 或公钥 PEM。voice catalog 的 `trusted_keys` 与 engine adapter 的 `trusted_public_keys` 都只能接收公钥，而且该 leaf 公钥必须先由固定 registry 与根签名 policy 独立授权。Release Candidate 不能自行新增 root、替换 registry 或把候选公钥注册为可信。

## 3. 离线签署 manifest

签名机接收已定稿 manifest。签名后不要重新格式化、改换换行或补空格，因为签名绑定原始 bytes。

```powershell
python scripts/manage_voice_release_keys.py --sign-manifest `
  --manifest 'E:\Offline_Signing_Inbox\engine-manifest.json' `
  --key-root $keyRoot `
  --signature-out 'E:\Offline_Signing_Inbox\engine-manifest.sig'
```

调用者不能传私钥路径、fingerprint 或 key id。工具只接受受保护根，并执行以下顺序：

1. 复核根和索引精确 ACL；
2. 由受保护索引的 basename+FileId 定位 receipt、公钥和随机私钥；
3. 验证 receipt self-signature、公钥/fingerprint、key id、purpose、status/test-only、rotation 与 validity；
4. 验证索引私钥与 receipt 公钥是同一 key pair；
5. 只在 signing window 内签署，并从 receipt 派生 signature JSON 的 key id。

签名输出必须与 manifest 同目录并使用合同名称，且已有文件不覆盖：

| Manifest | Schema | Signature | 允许的 key purpose |
|---|---|---|---|
| `engine-manifest.json` | `mentorflow.qwen-engine-manifest.v1` | `engine-manifest.sig` | engine 或 voice+engine |
| `voice-pack.json` | `mentorflow.voice-pack.v1` | `voice-pack.sig` | voice 或 voice+engine |
| `engine-catalog.json` | `mentorflow.qwen-engine-catalog.v1` | `engine-catalog.sig` | release-catalog |
| `voice-pack-catalog.json` | `mentorflow.voice-pack-catalog.v1` | `voice-pack-catalog.sig` | release-catalog |

signature JSON 只含 `algorithm: ed25519`、receipt 派生的 `key_id` 与 Base64 signature。控制台只返回 signature basename，不返回目录。

## 4. 用固定 root registry、签名 policy、receipt 与公钥验证

验证不读取任何私钥。应用固定 registry 的 schema 是 `mentorflow.release-trust-roots.v1`，安装路径是 `backend/config/voice/release_trust_roots.v1.json`，由工具自身位置推导，调用者不可指定。registry 的完整性属于**应用发布边界**：它必须随经过审核和签名的应用版本交付；如果攻击者已经能改写已安装 registry，就已越过本工具的信任边界。候选包内同名 registry、fake root 或 CLI 路径参数均不能改变信任来源。

当前 registry 的 `production_roots` 明确为空，因此 production runtime bootstrap 会以 `TRUST_PRODUCTION_ROOTS_EMPTY` fail closed；候选 policy 即使声称其他 root 也不能改变结果。仓库只安装一个显式 `test_only` root，私有 seed 只存在于测试源码中；工具不会生成 production trust root 或其私钥，test root/policy/leaf 也绝不能授权生产 identity。上线生产验证前，必须由独立的应用发布流程审核并安装 production root，不能由 Release Candidate 自助注册。

release trust policy 使用 `mentorflow.release-trust-policy.v1`，字段固定为：

- `policy_id`、`root_key_id`、`test_only` 与 canonical `created_at_utc`；
- `validity.not_before` 与 `validity.not_after`；
- `allowed_leaf_keys[]` 中每个 leaf 的 `key_id`、raw-public-key SHA256、允许的 `purposes` 与 `schemas`；
- `revoked_key_ids[]`。

policy signature sidecar 只包含 `algorithm: ed25519`、`root_key_id` 与 Base64 signature；signature 绑定 policy 的**原始字节**。policy 和 receipt 的时间戳都必须严格使用 `YYYY-MM-DDTHH:MM:SSZ`；`+08:00`、小数秒和其他非 canonical 表示一律拒绝。

```powershell
python scripts/manage_voice_release_keys.py --verify `
  --manifest 'E:\Release_Candidate\engine-manifest.json' `
  --signature 'E:\Release_Candidate\engine-manifest.sig' `
  --public-key 'E:\Release_Candidate\mentorflow-release-2026-q3.public.pem' `
  --trust-receipt 'E:\Release_Candidate\mentorflow-release-2026-q3.public-trust.json' `
  --trust-policy 'E:\Independent_Trust_Channel\release-trust-policy.json' `
  --trust-policy-signature 'E:\Independent_Trust_Channel\release-trust-policy.sig'
```

验证顺序固定如下：

1. 从固定 registry 选择已安装 root，并先验证该 root 对原始 policy bytes 的签名；
2. 严格解析 policy，检查 canonical UTC、有效期、test/production 边界及授权声明；
3. 验证 receipt self-signature、公钥/fingerprint 与内部合同，再要求 policy 精确授权 leaf key id、fingerprint、purpose 和 manifest schema；
4. 最后用已授权 leaf 公钥验证 manifest 的原始字节签名。

只有整条链成立才返回 `status: verified`、`integrity_verified: true`、`policy_signature_verified: true` 与 `trust_anchored: true`。`--expected-key-id`、`--trusted-fingerprint` 和可重复的 `--revoked-key-id` 是可选的附加 fail-closed 约束；它们只能拒绝 policy 已授权的 leaf，不能把任何 leaf 变成可信。test-only receipt 只接受明确的测试 identity：ID 必须以 `test-` 开头且 version 必须含 `test`；catalog 中所有 entry 都必须满足。test key 对生产 identity 的签名与验证均被拒绝。

工具签名格式与运行时 `backend.app.teacher_voice_pack.verify_ed25519(raw_public, raw_manifest, raw_signature)` 直接互通。这里的签名验证不能替代 pack builder/adapter 对 archive、逐文件 SHA、许可证、SBOM 和 runtime 的完整验收。

### Production Qwen 注册信任链

应用启动时由 `backend.app.release_trust_policy` 与 CLI 共用同一套纯验证核心。默认 registry 只可能是应用代码位置推导出的 `backend/config/voice/release_trust_roots.v1.json`；registration descriptor、候选包和环境变量都不能覆盖 root registry。`MENTORFLOW_QWEN_ENGINE_REGISTRATION` 只能指向 registration descriptor；`MENTORFLOW_VOICE_ENGINE_ROOT` 不再参与 production 注册。

`mentorflow.qwen-engine-registration.v1` descriptor 字段精确为：

- `schema`、`enabled`、`package_id`、`version`；
- `leaf_public_key`、`leaf_trust_receipt`、`trust_policy`、`trust_policy_signature`；
- `engine_catalog`、`engine_archive`、`engine_package`。

这些 artifact 字段只能是 application-owned engine root 下、对应 `package_id` release 目录内的 Windows-safe 相对 POSIX 路径。descriptor 不能内联 leaf/root 公钥、fingerprint、registry、engine root、URL、命令、可执行文件、参数、host 或 port；旧版 `trusted_public_key_base64`/`trusted_public_key_sha256` descriptor 会被拒绝。

production bootstrap 的接受顺序固定为：

1. 用 deny-write/delete 的固定句柄读取 descriptor、catalog、manifest、policy、policy signature、leaf PEM 与 receipt；拒绝 reparse point、hardlink、FileId/size/path 替换和重复/non-finite JSON；
2. 固定 registry 必须至少安装一个 production root；空 `production_roots` 即 fail closed，即使 descriptor 或环境变量存在；
3. 验证 root 对 policy 原始字节的签名，再检查 policy scope、有效期、撤销状态和 test/production 边界；
4. 验证 leaf receipt self-signature、PEM/raw public key、fingerprint、purpose/schema grant；
5. 用该授权 leaf 验证 engine manifest signature，再由严格 catalog 验证 manifest/archive digest 并逐文件验证 package；
6. 生成不可伪造的 registration capability receipt，绑定 registry/root、policy/policy-signature、leaf/public-PEM/receipt、catalog 与 archive 的 SHA256（以及 catalog/archive byte count）。

注册仍是 lazy 的，不启动子进程或加载模型。每个新的 child generation 在获取 package launch-attestation lease 之前，会重新读取固定 registry、policy、signature、leaf PEM/receipt、catalog 与 archive：撤销、到期、任何 byte digest 变化或路径身份变化都拒绝这一代启动。测试 root 只有 in-process 测试 fixture 的 underscored 参数可以启用；main app、production 环境变量和 production descriptor 没有该入口。

## 5. 备份与恢复

1. 使用两个独立、加密、离线介质保存整个受保护根；介质分别由不同保管人控制。不要使用 repo、邮件、聊天、云同步、普通 NAS 或 TEMP。
2. 备份清单只记录介质编号、key id、public fingerprint、封存人和封存时间；不记录私钥 basename、路径或内容。
3. 恢复到一个全新的 E: 根，不覆盖旧目录。恢复工具必须重新建立受保护索引的 FileId 绑定；复制后的旧 FileId 索引不能直接用于签名。
4. 定期用专用 test-only key 和无价值 manifest 做恢复演练；结束后核对 UUID 测试根及 key material 无残留。

## 6. 轮换

1. 在前任 `rotate_after` 前生成全新 key id，并在 receipt 写入 `predecessor_key_id`；key id 永不复用。
2. 先发布由已安装 root 签署、同时授权旧新 leaf 的 policy，并更新只含公钥的 runtime catalog，使两把 leaf key 在 overlap 内并存。
3. 到切换日后停止前任私钥签名。工具会在 `rotate_after` 之后自动拒绝前任 key 的新签名，但验证可持续到 `overlap_until`。
4. 确认受支持客户端已获得新 policy 与公钥后，用新的 root-signed policy 移除旧 leaf 授权，并结束 grace policy。
5. 封存或销毁旧私钥；保留不含私钥的 public receipt、发布记录和撤销记录。

## 7. 撤销

私钥疑似泄漏、介质丢失、保管人变化或出现异常签名时，不等待计划轮换：

1. 立即停止签名并隔离介质，记录受影响 key id/fingerprint 与时间范围。
2. 由可信 root 签署并发布把该 leaf key id 写入 `revoked_key_ids` 的紧急 policy，同时从 voice/engine runtime catalog 移除其公钥。`--revoked-key-id` 只作为尚未拿到新 policy 时的附加 fail-closed 约束，不替代签名撤销 policy。
3. 新增不可变 revocation record；不要修改或伪造旧 receipt。
4. 隔离该 key 签署的 release。未经单独审计的版本默认 fail closed。
5. 生成全新继任 key，重复先布公钥、再签 release 的流程；不得复用 key id。

## 8. 测试与验收

测试只生成 `--test-key`，从不生成生产 key：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest `
  backend/tests/test_voice_release_key_tool.py -q -p no:cacheprovider
```

聚焦矩阵覆盖固定 test root 正向链、policy 原始字节签名、A policy→A leaf、B 整包替换、候选 fake root/policy、registry CLI override、空 production root、wrong/expired/future/revoked/scope policy、canonical UTC Z、完整 purpose×schema 组合、test-only 双向生产隔离、随机私钥 basename、stdout 无绝对路径、精确 ACL、VolumeSerial+FileId 索引、hardlink/reparse、模拟路径替换、安全清理、调用者 key-id 拒绝、公私钥错配，以及先经独立 policy 授权再进入真实 engine archive+catalog runtime adapter 的验证。测试通过只证明 test-only 工具合同，不代表已经配置 production root、生成生产 leaf、完成离线发布签名或获得发布审批。
