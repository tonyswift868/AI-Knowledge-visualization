# MentorFlow Qwen3-TTS 0.6B 离线引擎包构建

本构建链面向 `win-x64`、本机离线推理的 Qwen3-TTS 0.6B Base 引擎。它不下载模型、不安装依赖、不启动服务，也不把开发虚拟环境直接发布给用户。只有显式 `--build` 会复制文件；只给 `--spec` 时等价于 `--plan`。

## 当前输入审计（2026-08-08）

以下是本机当前状态的只读审计，不代表已经形成可发布引擎包：

| 输入 | 当前证据 | 结论 |
| --- | --- | --- |
| Qwen3-TTS 官方源码 | GitHub commit `022e286b98fbec7e1e916cb940cdf532cd9f488e`；`SOURCE_RECEIPT.json` 已记录逐文件 SHA256 | 来源已固定 |
| Qwen 源码许可证 | Apache-2.0；`LICENSE` SHA256 `a44a6081c73ad75f0255bb2bb5cab74ef1829565a895a24e53a4f11290ab7655` | 可作为 Qwen 代码许可证证据，但仍须在 build spec 中显式映射到组件 |
| 0.6B Base 模型 | Hugging Face revision `5d83992436eae1d760afd27aff78a71d676296fc`；运行所需 11 个文件共 `2,516,100,892` bytes；allowlist tree SHA256 `3b927a09324c8d56379011ca2b85b97c8a31db8661e1bb31664d5f62c6a72fac`；最终代码 generation `modelmat3-20260808t0945z` 已生成 canonical source receipt 与 inventory，并通过独立 `--verify-only` | curated 模型材料已形成；原 27-file Hugging Face checkout 保持只读且不能直接作为 exact model root |
| 主模型权重 | `model.safetensors`：`1,829,344,272` bytes；SHA256 `180b3b10eb1c9f1b4db7806d5475bae3071c0243c299d49926bab1da3b6946f6` | 与 Hugging Face 下载 metadata 一致 |
| 语音 tokenizer 权重 | `speech_tokenizer/model.safetensors`：`682,293,092` bytes；SHA256 `836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258` | 与 Hugging Face 下载 metadata 一致 |
| portable Python | python.org 3.12.10 embeddable archive、PSF license/NOTICE、exact allowlist tree 与 v2 source receipt 已固定；receipt 逐项绑定 `.exe`/`.dll`/`.pyd`/`.cat` 的文件 SHA、`Valid` Authenticode 状态、publisher 和证书 thumbprint | 可作为 builder 输入；任何签名缺失、文件漂移或 publisher/status 不符都会 fail closed |
| 生产 runner | `backend/runtime/qwen_base_clone_server.py` 已实现 pack-relative root、固定 loopback、nonce-HMAC、完整 binding/receipt、双参考 prompt cache 和单飞合成；已通过协议假模型测试与构建器静态扫描 | 源码契约已具备；尚未进入完整签名包，也未做真实模型冷启动/重启验收 |
| 精确运行依赖 | 12Hz-only v2 已固定 48 包、19,065 个文件、`4,756,115,734` bytes；portable Python 离线导入 smoke 通过；许可证证据保留 103 个 wheel 内 occurrence + tokenizers 的 1 个外部 supplement = 104 | 技术闭包已形成并由 release-material consumer 独立复核；仍不能替代完整签名包和真实模型服务验收 |
| 发布签名 | 测试链工具已存在，但没有创建或启用 production key/root；固定 production registry 仍为空 | 不可发布；构建器和运行时会 fail closed |

因此，当前真实状态是“portable Python、48 包依赖和 curated 模型材料已固定，runner/production trust/真实声音验收仍未齐”。不要把现有 `.venv`、原 Hugging Face checkout 或开发 runner 压缩后发给用户。

## 构建规范

构建规范 schema 为 `mentorflow.qwen3-tts-runtime-build-spec.v1`。每个组件必须声明：

- 固定 `id`、`name`、`version`、`supplier` 和 SPDX license expression；
- 一个本机 source root 和精确 `include` allowlist；
- allowlist 的 `expected_tree_sha256`；
- 带 SHA256 的 JSON 来源 receipt；
- 带 SHA256 的许可证/NOTICE 文件；
- 固定且与组件类型相符的目标根目录。

组件类型与目标位置固定如下：

| kind | target_root |
| --- | --- |
| `portable_python` | `runtime/python` |
| `runner` | `runtime/runner` |
| `model` | `runtime/models/qwen3-tts-12hz-0.6b-base` |
| `dependency` | `runtime/python/Lib/site-packages` |

`portable_python` 必须是 `python.org-embeddable`，包含 `python.exe`、版本化 Python DLL 和只引用相对路径的 `python*._pth`。它的 source receipt 必须精确匹配 v2 schema、官方 archive/PSF 证据、allowlist tree，并完整覆盖每个 `.exe`/`.dll`/`.pyd`/`.cat` 的文件字节、SHA256、`Valid` Authenticode 状态、受信 publisher 和证书 thumbprint。`runner` 必须使用独立 source receipt、独立 license 与 NOTICE，禁止复用 Qwen 模型许可证；license expression 必须由项目权利人显式确认并通过命令行传入，构建链不会替项目发明 `LicenseRef` 或推定再分发授权。依赖组件必须使用带精确版本的 PyPI purl，并与 `dependency_lock` 中的名称、版本、wheel 哈希、`METADATA`、`RECORD` 和许可证证据完全一致。组件实际选择的文件、wheel 中的全部非目录成员及 `RECORD` 的全部行必须是同一个精确集合；wheel 目录项明确忽略，`RECORD` 自身的 hash/size 必须留空。删除任一代码文件后，即使重新计算 tree、receipt 和 lock 也会被拒绝。模型必须固定为 `Qwen/Qwen3-TTS-12Hz-0.6B-Base` 的 40 位 revision。

`runtime_gates` 必须把 runtime root、host、port、device 和 NVIDIA 最低驱动门槛全部暴露为环境变量；root resolution 必须是 `pack-relative`，不支持驱动时必须 fail closed。runner 入口固定为 `runtime/runner/qwen_base_clone_server.py`，并必须实际引用这些环境变量名。应用只从受信 engine capability 构造固定 Python/runner 启动计划；voice manifest 不能提供 command、URL、host 或 port。

签名配置只接受 Ed25519，并同时固定私钥文件 SHA256、受信公钥文件 SHA256 和 raw public key SHA256。私钥永不进入输出包。

## 命令

```powershell
$modelSourceRoot = '<absolute-existing-huggingface-checkout>'
$modelMaterialGenerationId = '<new-unique-model-material-generation-id>'
$modelMaterialGenerationRoot = "<absolute-E-material-root>\v1\generations\$modelMaterialGenerationId"
$modelRoot = "$modelMaterialGenerationRoot\model"
$voiceBuildSpec = '<absolute-path-to-build-spec.json>'
$voiceOutputRoot = '<absolute-output-root>'
$installedPackRoot = '<absolute-installed-pack-root>'
$trustedPublicKey = '<absolute-trusted-public-key.pem>'
$engineCatalog = '<absolute-engine-catalog.json>'
$engineArchive = '<absolute-win-x64.zip>'

# 原 checkout 只读；目标 generation 必须预先不存在。只复制受信 exact 11。
# source 与 generation/model/evidence 任一方向的相等、祖先/后代或 Windows alias 都会在写前拒绝。
# 模型文件使用 CopyFileW(bFailIfExists=TRUE)，证据使用 CreateFileW(CREATE_NEW)，均不覆盖。
python scripts/prepare_qwen3_tts_model_materials.py `
  --source-root $modelSourceRoot `
  --generation-root $modelMaterialGenerationRoot `
  --generation-id $modelMaterialGenerationId

# 独立只读复核 canonical receipt/inventory、exact tree 与 plain-file 门禁。
python scripts/prepare_qwen3_tts_model_materials.py `
  --generation-root $modelMaterialGenerationRoot `
  --generation-id $modelMaterialGenerationId `
  --verify-only

# 默认即 plan：读取、校验、计算哈希，不复制文件
python scripts/build_qwen3_tts_runtime_pack.py --spec $voiceBuildSpec

# 与默认行为相同的显式写法
python scripts/build_qwen3_tts_runtime_pack.py --plan --spec $voiceBuildSpec

# 只有此模式复制文件并签名
python scripts/build_qwen3_tts_runtime_pack.py --build --spec $voiceBuildSpec --output-root $voiceOutputRoot

# 用包外受信公钥验证已解压目录
python scripts/build_qwen3_tts_runtime_pack.py --verify-only `
  --pack-root $installedPackRoot `
  --catalog $engineCatalog `
  --archive $engineArchive `
  --package-id mentorflow-qwen3-tts-runtime `
  --version 0.1.0 `
  --trusted-public-key $trustedPublicKey `
  --trusted-public-key-sha256 <raw-ed25519-public-key-sha256>
```

构建成功后输出：

- `engine-manifest.json`（`mentorflow.qwen-engine-manifest.v1`，与运行时 adapter 共用同一正式契约）
- `engine-manifest.sig`（Ed25519 直接签署 canonical manifest 原始 bytes）
- `SBOM.spdx.json`
- `THIRD_PARTY_NOTICES_INDEX.json`
- `THIRD_PARTY_NOTICES.txt`
- `FILES.sha256`
- deterministic `win-x64.zip`
- `mentorflow.qwen-engine-catalog.v1` catalog（绑定 kind/package/version、archive SHA、manifest SHA 和 key id）
- allowlist 中的 portable Python、runner、0.6B 模型与精确依赖

构建器拒绝路径逃逸、Windows 盘符/UNC/ADS/设备名/尾点尾空格/控制字符、symlink/reparse point、hardlink、未声明文件或空目录、未知或缺失许可证、缺失或伪造 receipt、tree hash 不符、依赖 lock 不闭合、Gradio、cache、tests、training/finetuning、`pyvenv.cfg`、用户目录、API key/secret、runner 绝对 Windows 路径及不受信签名配置。模型必须与内置受信 repository、revision 及 11 个运行文件的 path/bytes/SHA/tree 完全一致；依赖必须同时匹配实际 wheel、METADATA、RECORD、canonical purl、tree hash 和 wheel 内许可证 SHA。

构建器与后端 adapter 调用同一份 Windows-safe relative path validator：不会先 `strip` 或把反斜杠转换为斜杠后再接受签名路径，并统一拒绝控制字符、`<>:"|?*`、ADS、完整 Windows 设备名（含 `COM¹`/`LPT¹` 别名）、尾点尾空格及大小写冲突。adapter 会比较实际文件集合和由 manifest 文件父路径唯一推导出的目录集合，因此额外空目录也会失败；包内任一 `st_nlink > 1` 的文件都会失败。文件在安全枚举后仍从已核验的 lexical root 重新 open/fstat/SHA256，并在运行时注册及首次 prepare 前按签名 inventory 再次复核，避免把较早的目录检查当成实际读取证据。

## 尚未满足的真实发布门槛

1. 把现有 pack-relative runner 纳入签名 allowlist，生成独立 runner source receipt/license/NOTICE，并完成无开发机路径的真实冷启动、模型加载、双风格合成、退出和重启测试。
2. 将已通过 producer/consumer 与 portable smoke 的 12Hz-only 48 包 dependency closure 纳入最终签名发布审计；继续保持 Gradio、训练、测试与 cache 排除门禁。
3. 对 portable Python v2 receipt 与 live Authenticode 结果做独立发布审计；不得用静态 receipt 代替目标机复核。
4. curated 模型 generation 已绑定 revision、exact 11 的逐文件 SHA256 与 tree；最终 build spec 仍须把该 receipt 与单独的 Qwen Apache-2.0 许可证证据正确映射到模型组件。
5. 建立受信 Ed25519 release key 配置与私钥保管流程。
6. 在目标 Windows 机器上验证 NVIDIA driver/CUDA/显存门槛，并完成离线安装、重启、断网合成与用户侧播放验收。

以上材料齐全前，`--plan` 应当失败；这是预期的发布安全门槛，不应通过关闭校验绕过。
