# MentorFlow 语音运行时与发布文档索引

MentorFlow 的生产默认路径只有已验签并激活的离线 teacher voice pack。最终用户不需要配置 TTS API，也不会因为打开页面或填写模型 API Key 而启动语音模型；合成只在用户点击朗读后发生。

`MENTORFLOW_TEACHER_VOICE_PROVIDER=local` 是旧 listener 协议的运维兼容入口，不是生产默认。它还必须同时设置 `MENTORFLOW_ALLOW_LEGACY_LOCAL_VOICE=1`，并通过 `MENTORFLOW_QWEN3_TTS_CONFIG` 或 `MENTORFLOW_GPT_SOVITS_CONFIG` 指向本地盘符上的显式绝对普通文件。路径不得使用 `~`、环境变量、相对路径、UNC/网络共享、Windows device path、符号链接或重解析点；映射到网络共享的盘符也会被拒绝。Windows 保留设备 basename 按 Win32 规则拒绝，包括大小写/扩展/尾点空格变体，以及 ISO-8859-1 官方别名 `COM¹`、`COM²`、`COM³`、`LPT¹`、`LPT²`、`LPT³`；实现不会对其它 Unicode 字符做泛化兼容折叠。配置中的服务地址只能是带显式端口的纯 loopback HTTP origin。MentorFlow 不会为兼容入口发现、启动、预热、停止、杀死或重试任何进程；`/voice/warm` 只校验本地配置和报告状态，不探测 listener。

仓库中的 `backend/config/voice/qwen3_tts_runtime.json` 只是默认关闭的 legacy 配置模板，不会被自动选为生产运行时；其 `api_url` 也默认为空，不提供固定端口。运维若确需兼容旧 listener，必须在显式配置副本中填写经过验证的 loopback origin。

两种旧协议都采用拒绝额外字段的精确 JSON schema。Qwen 配置必须使用 `schema_version=1`、`protocol=mentorflow.legacy-qwen3-tts-direct-loopback.v1`、`engine=qwen3-tts`；GPT-SoVITS 配置必须使用 `schema_version=1`、`protocol=mentorflow.legacy-gpt-sovits-direct-loopback.v1`、`engine=gpt-sovits`，并且字段集合只能包含这三个标识及 `reference_audio_rights_confirmed`、`api_url`、`ref_audio_path`、`prompt_lang`、`prompt_text`、`release_slot`。缺少标识、标识不匹配或包含未知字段都会 fail closed。

## 操作文档

- [Qwen Base Clone 真实 E2E 门禁与命令](qwen-base-clone-real-e2e.md)：隔离发布验收 harness、owned process 与真实音频证据的门禁；不是日常启动器。
- [Qwen3-TTS wheel license supplement contract](qwen3-tts-license-supplement-contract.md)：已实现并测试的精确补充许可证合同；当前 48 包闭包保留 103 个 wheel 内 occurrence，并为 tokenizers 固定 1 个外部 supplement，共 104 个法务证据 occurrence。
- [Qwen3-TTS 离线引擎包构建](qwen3-tts-runtime-pack-build.md)：可迁移 win-x64 engine pack 的输入、构建、验签与离线约束。
- [语音/引擎发布密钥操作规程](voice-release-key-operations.md)：离线 Ed25519 发布密钥、信任链、轮换和验证流程。

这些文档里的盘符、目录和端口是某次隔离工作站命令的可替换示例，不是 MentorFlow 架构默认。新环境应把示例值替换成运维明确选择并验证的绝对路径，例如 `<runtime-cache-root>`、`<model-root>`、`<release-input-root>`；生产代码不能根据相邻 checkout、用户名或固定盘符推导资源位置。

## 当前离线材料状态（2026-08-08）

- 12Hz-only dependency closure 已固定为 48 包、19,065 个文件；portable Python 3.12.10 的隔离导入 smoke 与 PE 依赖审计已通过，但没有加载模型、启动服务或使用 GPU。
- 模型材料必须先由 `scripts/prepare_qwen3_tts_model_materials.py` 从普通 Hugging Face checkout 选择受信 exact 11，再写入新的 generation-scoped `model/`；模型文件使用 `CopyFileW(bFailIfExists=TRUE)`，证据文件使用 `CreateFileW(CREATE_NEW)`，两者均不覆盖。source 与 generation/model/evidence 的相等或任一方向祖先/后代关系会按规范化、大小写不敏感的 Windows 路径语义在任何写入前拒绝，reparse/alias 不能绕过。`evidence/` 是同 generation 的 sibling，不能放进传给 builder 的模型根。准备模式拒绝已有目标，`--verify-only` 只读复核。
- 上述材料闭包不是整套发布 PASS。runner 独立可发布 license/NOTICE/source receipt、production trust root、真实模型冷启动/重启合成，以及最终声音权利与人工听审仍是 NO-GO 门禁。

## 仅开发诊断脚本

- `scripts/generate_qwen3_tts_original_candidates.py`：使用固定模型 revision 生成原创教师候选音频和审计材料，供授权、人工听审与后续训练选择。它当前包含开发工作站材料位置，只能在隔离开发流程中显式审阅后运行；输出不是签名声包、生产模型或最终声音授权证据，不能随应用启动。
- `scripts/trace_qwen_runtime_testlike_imports.py`：在已验证 portable Python 与 audited wheels 上追踪 Qwen 推理导入链中的 test-like 模块。它只生成诊断证据，不修改 portable tree，也不能替代无 fallback import smoke、真实模型 E2E 或发布验签。

两者都不是最终用户入口、后台服务、安装步骤或 production bootstrap 依赖。它们不得由 Web 请求、应用启动或 voice pack 激活流程自动调用。
