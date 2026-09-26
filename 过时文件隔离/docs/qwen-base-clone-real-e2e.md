# Qwen Base Clone 真实 E2E 门禁与命令

`scripts/run_qwen_base_clone_real_e2e.py` 是发布验收 harness，不是日常启动器。它会执行正式 builder、固定根信任链、离线 bundled voice-pack 安装、运行时注册、冷启动、PCM WAV 合成、owned restart、重启后重复合成和清理。默认生产路径不会启用测试根；仓库固定 registry 的 `production_roots` 为空时，生产验签会按设计失败。

当前提交除 tiny fixture / fake backend 测试外，已经完成 48 包 portable Python 离线导入 smoke、PE 依赖审计，以及 11 文件 curated model material 的复制与独立 `--verify-only` 复核；仍没有加载 Qwen、调用 CUDA、启动真实服务或生成 production key。

## GO 门禁

真实运行前必须同时满足：

1. 12Hz-only wheelhouse v2 的 fragment 和 validation 均为 `passed`；dependency set 不含 `sox`、`onnxruntime`，Numba 固定 `workqueue`，TBB 为 `optional-not-shipped`。
2. portable Python component fragment、v2 来源 receipt、PSF license/NOTICE、exact tree，以及每个 `.exe`/`.dll`/`.pyd`/`.cat` 的 `Valid` Authenticode publisher/thumbprint 证据全部可由 builder 复核。
3. 模型根只含固定 Hugging Face revision 的 11 个文件；不存在 cache metadata、额外文件、reparse point 或 hardlink；Qwen Apache-2.0 license 与固定 Qwen GitHub source receipt 均有效。
4. runner 已冻结，拥有独立 source receipt、独立可发布 license 和 NOTICE；不得复用 Qwen 模型许可证。生成 build spec 后必须通过 `build_qwen3_tts_runtime_pack.py --plan`。
5. 已有叶密钥、公钥、self-signed trust receipt、根签 policy 和 raw policy signature。harness 不生成任何密钥。生产运行还要求 fixed registry 中存在 production root；当前为空，因此当前生产结论是 **NO-GO**。
6. voice pack 是本地 `bundled` archive，并由同一 fixed root → policy → leaf 链授权 `voice-pack.json`；禁止 HTTPS 下载或环境公钥覆盖。
7. 最终声音已完成人工听审、转写/参考音频/原创身份检查并形成真实 QA/rights 证据。A–E 尚未选定时只能构造 `test-*` / `*-test` 临时测试包，并同时传 `--test-trust-chain --temporary-test-reference`。该报告始终标记 `test_only=true`，不得作为 final voice 或生产证据。
8. 选择 49152–65535 的未占用随机端口；绝不使用或停止 8000、8892、9880。运行前后这三个端口的监听 PID 集合必须完全一致。
9. Windows Job Object、listener PID ownership、NVIDIA driver/CUDA/显存门槛均可用。停止只能走该 harness 保存的 exact supervisor 的 `stop_owned()` / `shutdown()`。
10. voice catalog、engine、模型、wheel 和 cache 均为本地输入。harness 在父进程阻断非 loopback socket，并检查子 PID 的当前 TCP 连接；child 同时固定 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、`local_files_only=True`。报告不会伪称已建立主机防火墙；需要强 no-egress 证明时，验收机/VM 还必须由外层网络策略隔离。

任一门禁不满足即为 **NO-GO**，不得通过删除校验或改用未锚定公钥绕过。

## 1. 生成并验证正式 builder 材料

下面示例是显式 internal-test 链，因此 package/version 使用 test 身份。production key 必须由独立发布流程提供，不能由此工具创建。

```powershell
$generationId = '<new-unique-generation-id>'
$wheelRoot = 'E:\Kristina_Runtime\cache\downloads\qwen-runtime-wheelhouse'
$wheelProfile = "$wheelRoot\v2\generations\$generationId\12hz-only"
$pythonMaterial = 'E:\Kristina_Runtime\materials\python-embed-3.12\3.12.10-amd64'
$modelSourceRoot = 'E:\Kristina_Runtime\models\qwen3-tts\Qwen3-TTS-12Hz-0.6B-Base'
$modelMaterialGenerationId = '<new-unique-model-material-generation-id>'
$modelMaterialGenerationRoot = "E:\Kristina_Runtime\materials\qwen3-tts-12hz-0.6b-base\v1\generations\$modelMaterialGenerationId"
$modelRoot = "$modelMaterialGenerationRoot\model"
$runnerRoot = 'E:\aiPlay\AI-Knowledge visualization\backend\runtime'
$releaseInputs = 'E:\Kristina_Runtime\release-inputs\qwen-e2e-test'

# 原 Hugging Face checkout 可以含 cache metadata、README 与 .gitattributes，且始终只读。
# 工具只选择内置 bytes/SHA/tree 固定的 11 个文件；generation 根必须预先不存在，
# 并与 source/model/evidence 在规范化、大小写不敏感的 Windows 路径语义下双向不重叠。
# 模型文件使用 CopyFileW(bFailIfExists=TRUE)，证据使用 CreateFileW(CREATE_NEW)，均不覆盖。
& '.\.venv\Scripts\python.exe' -B '.\scripts\prepare_qwen3_tts_model_materials.py' `
  --source-root $modelSourceRoot `
  --generation-root $modelMaterialGenerationRoot `
  --generation-id $modelMaterialGenerationId

# 独立只读复核 exact 11、普通文件/无 hardlink/reparse、canonical receipt/inventory 与 tree。
& '.\.venv\Scripts\python.exe' -B '.\scripts\prepare_qwen3_tts_model_materials.py' `
  --generation-root $modelMaterialGenerationRoot `
  --generation-id $modelMaterialGenerationId `
  --verify-only

# 只新增 v2 inventory/fragment；同内容重跑幂等，内容冲突会失败，legacy 文件不改写。
& '.\.venv\Scripts\python.exe' -B '.\scripts\prepare_qwen_runtime_wheelhouse.py' `
  --materialize-portable-python-v2-materials `
  --root $wheelRoot `
  --portable-python-material-root $pythonMaterial

& '.\.venv\Scripts\python.exe' -B '.\scripts\prepare_qwen3_tts_runtime_release_materials.py' `
  --wheelhouse-fragment "$wheelProfile\builder-inputs\builder-dependency-fragment.json" `
  --wheelhouse-validation "$wheelProfile\evidence\builder-dependency-validation.json" `
  --portable-component "$pythonMaterial\BUILDER_COMPONENT.v2.fragment.json" `
  --model-root $modelRoot `
  --model-license 'E:\Kristina_Runtime\sources\Qwen3-TTS\LICENSE' `
  --trusted-qwen-source-receipt 'E:\Kristina_Runtime\sources\Qwen3-TTS\SOURCE_RECEIPT.json' `
  --runner-root $runnerRoot `
  --runner-entrypoint 'qwen_base_clone_server.py' `
  --runner-receipt '<absolute-existing-runner-source-receipt.json>' `
  --runner-license '<absolute-existing-mentorflow-runner-license.txt>' `
  --runner-notice '<absolute-existing-mentorflow-runner-notice.txt>' `
  --runner-license-expression '<owner-confirmed-SPDX-expression>' `
  --output-root $releaseInputs `
  --package-id 'test-mentorflow-qwen3-tts-runtime' `
  --version '0.1.0-test' `
  --created-at-utc '2026-08-08T00:00:00Z' `
  --signing-key-id '<existing-test-leaf-key-id>' `
  --signing-private-key '<absolute-existing-test-leaf-private.pem>' `
  --trusted-public-key '<absolute-existing-test-leaf-public.pem>'

# 非写入复核；要求上一步两个输出逐字节一致
& '.\.venv\Scripts\python.exe' -B '.\scripts\prepare_qwen3_tts_runtime_release_materials.py' `
  --wheelhouse-fragment "$wheelProfile\builder-inputs\builder-dependency-fragment.json" `
  --wheelhouse-validation "$wheelProfile\evidence\builder-dependency-validation.json" `
  --portable-component "$pythonMaterial\BUILDER_COMPONENT.v2.fragment.json" `
  --model-root $modelRoot `
  --model-license 'E:\Kristina_Runtime\sources\Qwen3-TTS\LICENSE' `
  --trusted-qwen-source-receipt 'E:\Kristina_Runtime\sources\Qwen3-TTS\SOURCE_RECEIPT.json' `
  --runner-root $runnerRoot `
  --runner-entrypoint 'qwen_base_clone_server.py' `
  --runner-receipt '<absolute-existing-runner-source-receipt.json>' `
  --runner-license '<absolute-existing-mentorflow-runner-license.txt>' `
  --runner-notice '<absolute-existing-mentorflow-runner-notice.txt>' `
  --runner-license-expression '<owner-confirmed-SPDX-expression>' `
  --output-root $releaseInputs `
  --package-id 'test-mentorflow-qwen3-tts-runtime' `
  --version '0.1.0-test' `
  --created-at-utc '2026-08-08T00:00:00Z' `
  --signing-key-id '<existing-test-leaf-key-id>' `
  --signing-private-key '<absolute-existing-test-leaf-private.pem>' `
  --trusted-public-key '<absolute-existing-test-leaf-public.pem>' `
  --verify-only
```

工具输出仍是 builder 已有的两个正式 schema：

- `qwen3-tts-runtime-build-spec.v1.json` → `mentorflow.qwen3-tts-runtime-build-spec.v1`
- `trusted-huggingface-model-receipt.v1.json` → `mentorflow.trusted-huggingface-model-receipt.v1`

## 2. 选择安全随机端口

```powershell
do {
  $voiceE2ePort = [Security.Cryptography.RandomNumberGenerator]::GetInt32(49152, 65536)
  $occupied = Get-NetTCPConnection -State Listen -LocalPort $voiceE2ePort -ErrorAction SilentlyContinue
} while ($voiceE2ePort -in @(8000, 8892, 9880) -or $occupied)
```

harness 会再次独立检查端口；若发生竞争并出现 listener，它只会失败退出，不会终止占用进程。

## 3. 执行显式 test-only 真实 E2E

builder 的固定输出布局为：

- package root：`<output-root>/<package-id>/<version>`
- archive：`<output-root>/<package-id>/<version>-win-x64.zip`
- catalog：`<output-root>/<package-id>/<version>-engine-catalog.json`

每次真实 E2E 必须使用新的、空的 engine output、voice-pack root、cache 和 evidence root。

```powershell
$packageId = 'test-mentorflow-qwen3-tts-runtime'
$version = '0.1.0-test'
$engineOutput = 'E:\Kristina_Runtime\e2e\qwen-engine-test-001'
$voicePackRoot = 'E:\Kristina_Runtime\e2e\voice-packs-test-001'
$cacheRoot = 'E:\Kristina_Runtime\e2e\cache-test-001'
$evidenceRoot = 'E:\Kristina_Runtime\e2e\evidence-test-001'

& '.\.venv\Scripts\python.exe' -B '.\scripts\run_qwen_base_clone_real_e2e.py' `
  --engine-build-spec "$releaseInputs\qwen3-tts-runtime-build-spec.v1.json" `
  --engine-output-root $engineOutput `
  --engine-pack-root "$engineOutput\$packageId\$version" `
  --engine-archive "$engineOutput\$packageId\$version-win-x64.zip" `
  --engine-catalog "$engineOutput\$packageId\$version-engine-catalog.json" `
  --package-id $packageId `
  --version $version `
  --public-key '<absolute-existing-test-leaf-public.pem>' `
  --trust-receipt '<absolute-existing-test-leaf-trust-receipt.json>' `
  --trust-policy '<absolute-existing-root-signed-test-policy.json>' `
  --trust-policy-signature '<absolute-existing-root-policy-signature>' `
  --voice-pack-root $voicePackRoot `
  --voice-catalog '<absolute-test-voice-catalog.json>' `
  --voice-pack-id 'test-mentorflow-temporary-reference' `
  --voice-pack-version '0.1.0-test' `
  --cache-root $cacheRoot `
  --evidence-root $evidenceRoot `
  --port $voiceE2ePort `
  --text '老师会用这段文本验证冷启动、重启和缓存。' `
  --style relaxed `
  --reply-kind real-e2e `
  --test-trust-chain `
  --temporary-test-reference
```

成功条件包括：三份完整 PCM WAV、重启前后 PID/generation 均变化、重启后第一次为 cache miss、同 generation 第二次为 exact cache hit、随机端口最终关闭、protected PID snapshots 不变、owned cleanup 无失败。

不要运行 `Stop-Process -Name python`、`taskkill /IM python.exe`、按端口杀进程或停止 8000/8892/9880。发生异常时，只查看 `$evidenceRoot\e2e-report.json`；harness 的 `finally` 仍只清理自己保存的 supervisor/Job Object。
