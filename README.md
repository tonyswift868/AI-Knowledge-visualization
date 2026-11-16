# MentorFlow

面向个人学习的桌面应用，使用重构后的账号外壳、学习计划与课堂。

- 官网：https://aiteacher.clannad.win
- 下载安装：https://aiteacher.clannad.win/download（小安装器，装的始终是最新版）

开发运行：`python -m refactor.desktop.account_shell_main`；先按 `refactor/learning/web/package-lock.json` 准备前端依赖并构建新界面。
正式打包入口：`python scripts/build_learning_installer.py --help`。
需求与当前状态见 [CURRENT_STATE](docs/project/CURRENT_STATE.md)。
