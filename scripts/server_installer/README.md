# 从服务器已有更新文件编译安装程序

复用 `scripts/build_learning_installer.py installer`、现有签名协议、
`per_user_install.verify_tree`、包内双公钥检查、包内容检查与现有 Inno 脚本。
这里只新增内容寻址文件的还原与发布编译结果的协调，不签名、不安装、不启动 App。
Windows 编译仍直接调用 ISCC；Linux 在容器内通过 Wine 调用同一个 ISCC。

## 工具、版本、许可

|组件|固定版本 / 来源|许可|
|---|---|---|
|基础镜像 / Python|`python:3.12.10-slim-bookworm`，官方 Python 镜像，amd64|Python PSF；Debian 各组件原许可|
|Wine|Debian bookworm 的 Wine / wine64 软件包|LGPL-2.1-or-later|
|Inno Setup|7.1.0 x64，[官方发布](https://github.com/jrsoftware/issrc/releases/tag/is-7_1_0)|[Inno Setup License](https://github.com/jrsoftware/issrc/blob/is-7_1_0/license.txt)，允许商用，保留版权及地址|
|简体中文|7.1.0 安装包自带 `Languages/ChineseSimplified.isl`；镜像构建时确认存在|随 Inno 包分发；[官方同版本文件](https://github.com/jrsoftware/issrc/blob/is-7_1_0/Files/Languages/ChineseSimplified.isl)，维护者 Kira|
|PyInstaller|6.16.0，与本机 `installer/build-tools` 一致|GPL-2.0-or-later，含打包分发例外|
|Pillow / cryptography|12.3.0 / 46.0.7，与本机现有环境一致|HPND / Apache-2.0 或 BSD-3-Clause|
|容器做法参考|[amake/innosetup-docker](https://github.com/amake/innosetup-docker)，参考 Wine、虚拟显示、显示点文件的做法|**CC0-1.0，实际不是派工中记忆的 MIT**；未复制镜像或脚本|

Inno 官方下载地址写死为
`https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe`；
SHA256 为 `0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f`，
本机下载计算结果与 [winget 清单](https://github.com/microsoft/winget-pkgs/blob/master/manifests/j/JRSoftware/InnoSetup/7/7.1.0/JRSoftware.InnoSetup.7.installer.yaml) 相同。
不使用浮动 latest 编译器。Python 3.12 是必须的：CArchiveReader 要读取冻结包里的
Python 3.12 code object，不能随意换成 Ubuntu 自带的另一 Python 次版本。

## 本机准备小工具包（不含 payload、对象、私钥）

下面命令只在 runtime 工位执行；`PYTHONPATH` 只为使用现有 build-tools，不安装包。
`--payload` 必须是已经核实的正式构建目录：工具从其中两个 EXE 提取、比较公共信任信息，
再把**公开**公钥写入工具包 `scripts/server_installer/trust.json`。
不会根据待验签 stable.json 里的内容选择信任公钥。此小包需要架构师通过既有可信发布路径传递。

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='D:\MentorFlow\.local\test-tmp\installer\build-tools'
& 'D:\MentorFlow\.venv\Scripts\python.exe' -B scripts/server_installer/make_bundle.py --payload 'D:\MentorFlow\.local\test-tmp\release-2.0.3m\payload' --output 'D:\MentorFlow\.local\test-tmp\server-installer90\mentorflow-server-installer90.tar.gz'
```

交付包只包含本目录、现有构建器、五个 delivery 模块、authority/payload 工具、
原 Inno 脚本、图标及必要的包入口；没有学习代码、模型、安装包或正式私钥。
本批已生成该包，可直接使用。后续这些源文件或信任公钥发生变化时重新生成工具包，
并重新构建同名镜像（见下方显式重建命令）。

## 架构师在服务器首次执行

本机没有 Docker/WSL，本批**未执行 Linux 镜像构建或服务器运行**；以下由架构师执行。
需要 Ubuntu amd64、现有 Docker、bash/flock/tar，以及更新目录和 state 的读写权限。
不依赖服务器的 Python 或 pip。先通过既有传输方式把小包放到
`/opt/mentorflow/state/mentorflow-server-installer90.tar.gz`，然后：

```bash
sudo mkdir -p /opt/mentorflow/state/server-installer-tools
sudo tar -xzf /opt/mentorflow/state/mentorflow-server-installer90.tar.gz -C /opt/mentorflow/state/server-installer-tools
sudo bash /opt/mentorflow/state/server-installer-tools/scripts/server_installer/build_on_server.sh
```

预期输出依次为：

1. `BUILDING_IMAGE`：仅首次自动建 `mentorflow-installer:inno7.1.0-py3.12.10-v1`。
   下载系统依赖、固定 Python 包和官方 Inno；校验官方下载 SHA256；在容器内静默安装编译器。
   **不运行本项目的安装程序**。安装包已包含简体中文，不额外下载语言文件。
2. `VERIFYING / ASSEMBLING / COMPILING`：后续整个容器用 `--network none`，不访问服务器 API，
   只读本地 stable/objects，现有 Ed25519 验签失败即退出。
3. `ASSEMBLED {version, files, bytes, linked, copied, output}`：在
   `/opt/mentorflow/state/installer-build/<版本>/attempt-*/` 下还原树并整体核验。
   对象同磁盘优先硬链接，否则复制；原对象不修改。不要修改临时 payload，因为硬链接共用内容。
4. 现有 installer 步骤输出 EXE 路径、大小、SHA256。编译详细日志是 attempt 下 `inno/inno.log`。
5. `{"status":"PUBLISHED", ...}`：旧文件先备份为 state 下
   `installer-<UTC日期时间>.exe`，新文件复制到 installer 目录的隐藏 `.new` 文件并校验，
   再同目录原子替换 `MentorFlow-Setup.exe`；最后原子写 JSON 格式 `VERSION.txt`
   （`version/sha256/size/built_at`）。元数据替换失败会恢复旧 EXE，旧元数据保留。
   成功删除本次 attempt 的所有中间产物，备份保留供架构师管理。

可用 `sudo cat /opt/mentorflow/update-static/e108/app/installer/VERSION.txt` 和
`sudo sha256sum /opt/mentorflow/update-static/e108/app/installer/MentorFlow-Setup.exe` 核对结果。
第二次运行应输出 `UP_TO_DATE`，不再拼包编译。跳过前同时检查版本、EXE 哈希和大小，
不会把“只有版本文件、没有完整 EXE”误当完成。

首次工具升级/公钥更新需要显式重建镜像，然后继续用同一条运行命令：

```bash
sudo docker build --platform linux/amd64 -t mentorflow-installer:inno7.1.0-py3.12.10-v1 -f /opt/mentorflow/state/server-installer-tools/scripts/server_installer/Dockerfile /opt/mentorflow/state/server-installer-tools
sudo bash /opt/mentorflow/state/server-installer-tools/scripts/server_installer/build_on_server.sh
```

可在隔离服务器目录排练而不写线上：

```bash
sudo env APP_ROOT=/path/to/test/app STATE_ROOT=/path/to/test/state bash /opt/mentorflow/state/server-installer-tools/scripts/server_installer/build_on_server.sh
```

## 每次发版

1. 本机打包并由架构师签名，仍使用现有 release-tools。
2. **只传缺少的 objects**，完成对象上传后替换 `stable/stable.json`。
3. 服务器执行上方 `build_on_server.sh` 一条命令。无需本机上传几百 MB 安装程序。
4. 核对 `PUBLISHED` 和 VERSION/哈希，架构师再按既有办法验证下载。

工具使用文件锁避免两次同时构建；使用 stable 快照，在替换 EXE 前再次核对线上清单未变。
拼包、验签、编译、备份或暂存失败均不会发布半成品；元数据替换的可捕获错误会回滚 EXE。
断电发生在 EXE 与 VERSION 两次重命名之间时，VERSION 可能落后，但 EXE 不会半写；
下一次运行根据哈希/版本不符重新构建。这里不宣称两个独立文件能实现断电时的联合原子事务。

## 常见失败与重试

|输出/证据|判断与处理|
|---|---|
|`Missing bundle trust.json`|使用 make_bundle 生成的小包；不从 stable 自己取公钥|
|Docker 下载、pip 失败|失败在首次建镜像，线上 EXE 未动；查看 Docker 输出与网络/源可达性，再运行原命令|
|官方 Inno SHA256 不符|下载损坏或来源内容变化；不要跳过校验，不换版本，核实固定官方资产|
|中文文件缺失、Wine 非零退出|失败在镜像安装或编译；查看 Docker 输出或 `inno/inno.log`；本批 Linux 未实测，不保证 Wine 兼容性|
|`release authenticity/contract rejected`、过期、key 不信任|检查 stable 是否完整、已正式签名，以及工具包公钥是否与包内一致；不绕过现有验签|
|`missing object` / `object size/hash mismatch`|补传提示的对象或修复损坏文件后重试，线上安装程序未动|
|`stable changed during build`|发布期间另有新清单；原安装程序未动，重新运行处理新版本|
|编译失败|state/installer-build/版本/attempt 下保留签名快照、build-status.json 与 inno.log；payload/output/inno 临时目录会清理|
|`cleanup_warning`|已发布成功，但列出的临时目录未能清理；不要当作未发布，架构师检查路径后清理|
|`Another installer build is running`|已有构建，等待其结束；不要删除锁文件来绕过|

首次预计下载 **0.4–0.9 GB**（Debian Wine/字体依赖、Python 镜像和 wheels），
镜像及 Wine 前缀占盘约 **1.5–3 GB**；含编译、暂存、旧 EXE 备份的峰值另加
约 **1–2.5 GB**（跨磁盘复制 payload 时多约 1.1 GB）。保守预留 **6 GB**，
服务器现有 36 GB 空闲足够。首次约 **10–30 分钟**，后续约 **2–8 分钟**；
这些是 4 核/7 GB 机器的估计，非 Linux 实测，取决于下载与 Wine 编译速度。

## 本机与离线检查

`assemble_payload.py --release ... --objects ... --output ... --public-key ...`
输出目录必须不存在。失败清理隐藏暂存目录，已存在的输出不覆盖。
`run_build.build_on_server(..., compile_fn=...)` 可注入假编译器验证发布/回滚，不执行 EXE。

```powershell
& 'D:\MentorFlow\.venv\Scripts\python.exe' -B -m pytest tests/refactor/installer -q -p no:cacheprovider --basetemp 'D:\MentorFlow\.local\test-tmp\server-installer90\installer-test-tmp'
```

本批真实拼包/Windows 编译证据与全量结果见任务产物目录 `report.md`。
