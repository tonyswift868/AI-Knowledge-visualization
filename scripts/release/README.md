# Windows 手动发布流水线

## 现成项目、来源与许可证

|工具|固定版本／来源|许可证与用途|
|---|---|---|
|GitHub 官方 Actions|[checkout](https://github.com/actions/checkout)、[setup-python](https://github.com/actions/setup-python)、[setup-node](https://github.com/actions/setup-node)、[upload-artifact](https://github.com/actions/upload-artifact)，均 v7|MIT；检出、运行环境与小 artifact|
|Python / Node|3.12.10 / 24.13.0；mf-publish仅需Python|PSF / MIT；Windows runner，不用 Wine|
|PyInstaller / Pillow / cryptography|6.16.0 / 12.3.0 / 46.0.7|GPL 加打包分发例外 / HPND / Apache-2.0 或 BSD；沿用现有安装步骤|
|Inno Setup|[7.1.0 官方安装包](https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe)|[自由许可，可商用](https://github.com/jrsoftware/issrc/blob/is-7_1_0/license.txt)；仅在 GitHub runner 静默安装|
|SQLite runtime DLL|[sqlite.org 3.54.1 Windows x64](https://sqlite.org/2026/sqlite-dll-win-x64-3540100.zip)|公有领域；发布构建校验归档 SHA3-256 `c2d09c8babb9399277055c057c97a53f1ffd7c5f11d0c1f2118ffdfa39f2daa4` 与解包 `sqlite3.dll` SHA256 `4b59d3cded9dc1608544160f3b4b10ac974c6ac0d6ffc23064e5f4c8dbd05aea`，并将它替换进 App 的 PyInstaller Analysis|
|actionlint|[1.7.12](https://github.com/rhysd/actionlint/releases/tag/v1.7.12)|MIT；只在本机检查，GitHub 不跑测试|
|7zr.exe|保留已核对的官方 7-Zip standalone 解压器，SHA256 见 inputs/reference.json|[7-Zip LGPL 与部分代码许可](https://www.7-zip.org/license.txt)，[公开源码](https://github.com/ip7z/7zip)；没有改二进制|
|现有 MentorFlow 组件|build_learning_installer.py、assemble_payload.py、delivery/update_protocol.py、verify_tree|沿用本仓库授权；不另造签名、清单、安装或更新机制|

Inno 安装包 SHA256 固定为
`0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f`。
中文语言文件使用官方包自带的 `Languages/ChineseSimplified.isl`。

`inputs/` 是从已知可信的 2.0.1 冻结包保留下来的四份公开打包输入与身份记录：
API 配置、CA、公钥、7zr、语音资源目录。哈希与原打包总控的固定输入一致，
没有私钥、模型 Key 或用户数据。12 MB 的语音目录是文件清单，不是模型。
inputs/.gitattributes 禁用文本换行转换，保证 Git for Windows 的 autocrlf
不会改变这些按字节固定哈希的公开输入。
完整 App 的 73 条 Python 依赖全部写死，含真实冻结分析中发现的 9 个可选依赖，避免 CI
因少装 h2、socksio 等而改变运行行为。前端始终用仓库 package-lock + npm ci。
mf-publish 不安装 Node；bootstrap 单独用 requirements-bootstrap.txt 锁住 PyInstaller、
Qt、HTTP 与验签所需的最小依赖闭包。
SQLite DLL 是独立于 Python requirements 的锁定输入：本机 freeze 与 GitHub
mf-build 共用 `scripts/release/sqlite_runtime.py`，只在本次工作目录下载、验归档和 DLL
字节；不会改动 Python 安装目录或系统 DLL。
若配置 MF_BUILD_TOOLS，版本检查与冻结子进程都优先该目录；本机实际使用的
packaging 为此目录中的 26.3，而非 .venv 中的 26.2，锁定表按前者保留。

## 额度红线与三个入口

只有 `workflow_dispatch`，不监听 push、PR、schedule、workflow_run；不用矩阵，
GitHub 不跑测试。三个工作流共享 `mentorflow-release`、`queue: max`、
`cancel-in-progress: false`，最多一个运行，其余排队。不要反复点按钮。
队列依据开始等待顺序处理，不保证手动点击的顺序。

|工作流|输入|时限|预计占用 Windows runner 分钟*|
|---|---|---|---|
|mf-installer|version|40|10–25（离线备用包）|
|mf-build|version、notes、mandatory、mandatory_reason、base_versions（空格分隔）、build_only|60|15–40|
|mf-publish|version|40|仅 bootstrap 输入变化时编核心/小壳；日常验签和发布 stable|

\* 估计，不是 GitHub 实测；冷缓存、网络会影响耗时，额度与计费以
[当前官方说明](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
及仓库 Billing 为准。首次三次合计预计 35–90 runner 分钟；时限总上界 140 分钟。
只有 unsigned-release.json 和 build-evidence.json 做 artifact，保留 3 天；
payload、objects、安装程序不做 artifact。pip/npm 缓存可复用；缓存也占额度，
管理员应按仓库 Billing 观察，无需自动跑清理工作流。

实测（2026-10-11，用 API 汇总 10 月各任务起止时间，每个任务按分钟向上取整）：
- 共 22 次运行、156 分钟 Windows 机时：mf-build 10 次 45.6 分钟，mf-publish 9 次 66.0 分钟，mf-installer 3 次 33.5 分钟。
- 发一版不编安装程序约 8 分钟，编安装程序约 15 分钟。

实测（2026-10-17，同样方法，汇总账号两个私有仓库 10 月 1 日以来全部 46 次运行）：全部跑在 Windows 上，共 276 分钟机时，按 Windows 两倍计 552 分钟；免费额度 2000 分钟还剩约 1448，折合 Windows 机时约 724 分钟。最近几次发版（只发更新）每次约 mf-build 4.5～5.5 分钟 + mf-publish 1 分钟，折合约 12～14 分钟额度。账单周期若不是从 1 号算起，以 Billing 页为准。
- artifact 占 3.3 MB，缓存 417 MB。

本机 Git 凭据读不到账户的 Billing 接口（返回 404），剩余额度以 github.com → Settings → Billing 为准。

官方 GitHub 已支持 [queue: max](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)，
但 actionlint 1.7.12 尚不认识这个字段。运行下面的本机检查：它先严格检查
触发器、queue 的值、并发组、时限与无矩阵/测试，再调用 actionlint，**只豁免**
“不认识 concurrency.queue”这一条诊断，其他诊断照常失败。上游支持后去掉豁免。

```powershell
python -B scripts/release/check_workflows.py --actionlint '<actionlint.exe>'
python -B -m pytest tests/refactor -q -p no:cacheprovider
```

## 首次启用（由 MF-01 在另行授权后部署）

1. 用户在仓库 Actions Secrets 填 `MF_SSH_KEY`，使用现有 mentorflow-admin
   的 SSH 管理密钥；**正式发布签名私钥绝不进入 GitHub**。确认 Billing 的
   超额上限为 0。主机公钥已从指定 known_hosts 公钥行固定在工作流和脚本中。
2. MF-01 检查实际 nginx vhost，将 `/download` 精确路由到固定小壳
   `/updates/app/releases/bootstrap/MentorFlow-Setup.exe`。本仓库的
   `refactor/server-java/deploy/prepare_bootstrap_download_route.py` 只从审阅过的
   配置副本生成候选文件；它不写源配置、不 reload nginx。对候选执行 diff 和 `nginx -t`
   后才按独立授权部署。静态服务继续开启 disable_symlinks。
3. 架构师确认该提交已合入默认分支，仓库允许官方 Actions，管理员可免密 sudo，
   服务端有 Python 3（仅标准库）。确认 objects 与 releases 在同一文件系统，
   update-static 可读取公开文件，且 `releases/bootstrap` 与对象同属公开静态根目录。
   先核对服务器磁盘余量。
3. 首次小壳与核心由正常 `mf-publish` 在 promote stable 之前自动构建、核验并发布；
   不再使用 mf-installer 设置在线下载指针。
4. 任意一次失败即停下来查日志，**不启动无依据的重试**。员工没有推送、触发
   Actions、SSH 或上线；上述操作只有架构师在用户同意后做。

## 以后发布一版的顺序

1. 员工在本机做产品测试，架构师审阅合并并由用户授权推送。
2. 架构师手动点 **mf-build**，选择目标代码 ref，填新版本号与说明，
   基线填服务器 releases 已保留的版本；正常发版 build_only=false。
   必须更新时填 mandatory=true 与 security/protocol/data_compatibility 中的理由。
   非强制版本会保留已有 mandatory floor。冻结前对照固定环境；清单 source_sha
   必须与检出提交一致。只向 `.staging-<v>/objects/` 上传服务器缺少的对象，
   单批 tar/SCP，逐个检查 SHA256 与大小，已有对象不覆盖。
3. 架构师下载该次运行的 **unsigned-<v>** artifact，在自己的本机运行：

```powershell
python -B scripts/release/sign_and_stage.py --artifact '<下载的ZIP或解压目录>' --private-key '<本机正式私钥路径>' --work '<本机独立签名工作目录>' --ssh-identity '<现有SSH管理密钥路径>'
```

   脚本只调用原 signer，核对私钥与固定公钥匹配；不打印私钥，不把私钥复制
   到工作目录或上传。只传签好的 stable.json 到 `.staging-<v>/stable/`。
   本机工作目录保留签好的清单，便于人工核对。
   Windows 本机和 Actions 共用 SSHServer：远程命令固定为 `sudo -n python3 -`，
   server_ops 源码和请求经标准输入传入，不在远程命令行内联整段源码；
   清单仍由 SCP 传输，私钥不进入标准输入或上传内容。传输或远端执行失败照常报错。
4. 架构师手动点 **mf-publish**，填 version。流水线先比对 core/stub 输入指纹：
   核心输入包含 delivery 源码闭包、公钥、feed、协议下限和依赖锁；核心变化时编译并
   发布到不可变 `releases/bootstrap/<fingerprint>/bootstrap-core.zip`。stub 指纹另算，
   绑定小壳源码、工具锁与核心 SHA256；变化时才编译并原子切换固定
   `releases/bootstrap/MentorFlow-Setup.exe`。新 stable 依赖新 core 时 core 和 stub
   先发布成功，pipeline 才验签并 promote stable。任何 bootstrap 构建/发布失败都不切 stable。
   core 不变时确认服务器不可变文件仍存在且 SHA256 正确，不重编；stable 版本不参与指纹。
5. mf-publish 只验签、发布对象与清单、原子切 stable，最后清理 staging；不下载全部对象、
   不拼完整 payload、不编完整安装包。mf-installer 是单独手动入口，只生成版本目录下的
   离线包，不切换固定在线 stub。
6. 架构师核对版本目录 stable.json、bootstrap 指纹和客户端检查更新；部署前由 MF-01
   验证 `/download` 路由及固定小壳地址。不得在本任务触发 workflow 或部署服务器。

Bootstrap 的 immutable core 发布、fixed stub 原子替换、stable promote 按此顺序执行。
core 发布成功但 stub 失败时旧 stub 和 stable 保留；重试可复用已发布且已验 hash 的 core。
stub 发布成功但 stable promote 失败时旧 stable 仍有效，stub 可自举当前旧 stable。
mf-installer 离线包发布失败不影响在线 stub 或 stable。

## 目录与脚本

```text
/opt/mentorflow/update-static/e108/app/
  objects/<sha256>
  stable/stable.json
  stable/latest.json  # 小型未签名指针；最终判断只信已验签 stable.json
  .staging-<version>/objects/<sha256>
  .staging-<version>/stable/stable.json
  releases/<version>/stable.json
  releases/<version>/MentorFlow-Setup-<version>.exe  # mf-installer离线备用包
  releases/bootstrap/<fingerprint>/bootstrap-core.zip  # 不可变可信核心
  releases/bootstrap/<fingerprint>/bootstrap-core.json
  releases/bootstrap/MentorFlow-Setup.exe  # 固定在线小壳，原子替换
  releases/bootstrap/bootstrap-state.json  # CI 输入指纹与构件哈希
/opt/mentorflow/state/stable-<旧版本>-before-<新版本>-<随机标识>.json
/opt/mentorflow/state/installer-before-releases-<随机标识>.exe
```

在线小壳发布前在 `releases/bootstrap/` 中暂存临时文件、核对大小和 SHA256，
再用 `os.replace` 原子替换固定文件；immutable core 用版本指纹目录且禁止内容冲突。
`/download` 的 nginx 路由待 MF-01 在另获授权后切换到 bootstrap 固定入口。

## 更新地址、清单压缩与小指针

MF_FEED 默认值为旧 IP `https://89.45.45.28/mentorflow/updates/app/stable/stable.json`，
签名清单中的 objects 地址也随此根地址生成，并由 build_release.py 强制检查（BUG-60）：
2.0.9 及更早的客户端只从自己检查更新的那个来源（IP）下载文件，清单里写域名地址它们会判
“更新校验未通过”。2.0.10 起的客户端把 IP 地址映射成“先域名、后 IP”，所以清单写 IP 不影响新客户端走域名。
新客户端只对这两个固定根地址作同路径镜像回退，不接受任意跨站 URL。
域名用系统信任根，IP 用现有随包 account-api.pem 加入信任；不关闭 TLS 校验。
域名连接/证书/HTTP/解压/清单验签或下载内容校验失败时退回 IP；取消不回退。
两个固定来源各最多一个网络线程，域名超时后即使旧 DNS/请求仍未结束，IP 也能
独立开始；同一来源不会无限追加挂起线程。其他测试/自定义来源仍保持单来源限制。
文件仍是 identity、Range 字节续传及 SHA256/大小校验，坏的远程分片重下，
完整缓存对象被改动仍报错，不把本地损坏当镜像错误忽略。

stable.json 接受 Content-Encoding: gzip，HTTPX 解压后对原始清单字节验签；
大小限制针对解压后的内容，网络下载统计仍用实际传输字节。objects 不压缩。

mf-publish 在同一次 promote 操作中保存 stable/latest.json：
`version`、`metadata_version`、`sha256`、`size`（清单原始字节的指纹和长度）。
先原子写 stable.json，再原子写 latest.json，通常约 140 字节。两个文件不能
一次 rename 同时切换，因此读到跨切换状态时以已验签的清单为准并记录不一致；
写指针失败会报错并保留 staging，重跑 promote 可补齐，不产生半截 JSON。

检查先取指针（最多 2 KiB，每次请求总等待上限 5 秒）；与本机已验签清单的
版本、metadata_version、指纹、长度都相同，且缓存重新通过当前有效期与高水位
检查时，不下完整清单。缓存仍有新版/强制更新时照常提示，不能误判已是最新。
有变化、无有效缓存、指针缺失或不合法则读完整清单。指针不授权版本、不改强制线；
指针与验签清单不一致时记录 pointer_mismatch，采用验签清单，不被未签名提示拦住。
架构师合入后下一次正常发布即生成指针；本任务不连接服务器部署或补写文件。

### 任意旧版本的文件复用

`base_versions` 与 `incremental_assets` 保留供老客户端使用，新客户端不再拿它们
作为是否允许增量的门槛。检查阶段用本机文件清单估算；用户开始下载后，逐个核对
实际 App 文件的大小与 SHA256，和目标签名清单相同就本地独立复制，仅下载缺失、
不可读或指纹不同的文件。没有本机清单也可按实际文件复用；不会在登录检查时扫描
整包。实际扫描后更新预估下载量。复制时再校验，完成后整体 verify_tree，原子替换
及失败回滚不变；本机文件被改过不会因版本号/大小相同而被信任。

## 安装程序保留规则

每次离线安装程序发布成功后，按数值版本号保留最大的
3 个版本的 exe，数量集中在 server_ops.INSTALLER_KEEP_COUNT。更早版本只删除
`MentorFlow-Setup-<版本>.exe`；目录与 stable.json 永远保留，objects 不变。
在线 bootstrap stub 不参与离线安装包保留规则。不是按日期、文件名字符串或“现有 exe 数量”排序。

清理前后逐文件记录日志，发布返回值的 retention 包含 deleted、removed_bytes、
released_bytes、protected_latest 和 events。removed_bytes 是删掉路径的内容字节；
released_bytes 只计最后一个硬链接被删除的内容字节，不代表磁盘块的精确释放量。
重复执行不再删除额外文件。清理失败会报错，已发布的离线包不回滚，
staging 保留以便架构师核对失败位置。

需要旧版离线包时仍可手动运行 mf-installer：保留的 stable.json 用于验签、从 objects
重编，exe 在原目录中校验后原子放回，清单不变。该入口不切换 `/download` 在线小壳。

`pipeline.py` 的 installer/ensure-bootstrap/promote/stage/build/cleanup 都接受 `--local-server-root`，
此模式不创建 SSH 客户端、不联网取对象，真实编译仍要指定现有 `--compiler`。
本机绝不自动安装 Inno；自动安装官方 Inno 只在 GitHub runner 开启。

```powershell
python -B scripts/release/pipeline.py stage --version 2.0.3 --unsigned '<unsigned-release.json>' --objects '<构建objects>' --work '<任务目录/upload>' --local-server-root '<模拟服务器>'
python -B scripts/release/pipeline.py promote --version 2.0.3 --work '<任务目录/promote>' --local-server-root '<模拟服务器>'
python -B scripts/release/pipeline.py ensure-bootstrap --compiler '<已有ISCC.exe>' --work '<任务目录/bootstrap>' --local-server-root '<模拟服务器>'
python -B scripts/release/pipeline.py installer --version 2.0.3 --compiler '<已有ISCC.exe>' --work '<任务目录/installer>' --local-server-root '<模拟服务器>'
python -B scripts/release/pipeline.py cleanup --version 2.0.3 --work '<任务目录/cleanup>' --local-server-root '<模拟服务器>'
```

直接在本机用迁移后的总控，参数仍为原来的 version/baseline/valid-days/
base-versions/notes/output-dir，新增环境变量去掉机器路径：

```powershell
$env:MF_TEMP_ROOT='<独立临时目录的父目录>'
$env:MF_BUILD_TOOLS='<已有PyInstaller附加工具目录；已安装时可不设>'
$env:MF_NODE_DIR='<node.exe所在目录；在PATH里时可不设>'
$env:MF_ISCC='<已有ISCC.exe；freeze不用编译器>'
python -B scripts/release/build_release.py --version '<新版本>' --baseline '<提交>' --valid-days 1096 --base-versions 2.0.2 --notes '<说明>' --output-dir '<MF_TEMP_ROOT下的任务目录>' --previous-release '<已验签旧stable.json>' --base-release '2.0.2=<历史stable.json>'
```

不传 previous-release 时才读取配置 feed。`--build-only` 允许重建当前版本，
不会允许发布同版本；`--keep-work` 为 CI 暂留待上传 objects。
`--allow-dirty` 仅供本机改打包脚本的隔离验证，工作流不使用。
原 `.local/release-tools/build_release.py` 未改，保留现有本机入口。
