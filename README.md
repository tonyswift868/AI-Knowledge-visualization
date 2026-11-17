<div align="center">

<img src="docs/images/banner.svg" alt="MentorFlow：带 AI 老师的个人学习软件。说出想学什么，每天学什么它来排" width="100%">

说出想学什么，它帮你排好每天的学习计划；老师上课讲解、出题判分，跟着你的进度走。

[![下载最新版](https://img.shields.io/badge/%E4%B8%8B%E8%BD%BD%E6%9C%80%E6%96%B0%E7%89%88-Windows-d6457a?style=for-the-badge)](https://aiteacher.clannad.win/download)

[官网](https://aiteacher.clannad.win) · [下载](https://aiteacher.clannad.win/download) · [MIT 许可证](LICENSE)

</div>

## 截图

![新建计划：想学什么、学到什么程度、现在的水平、起止日期，每天学多久、每周哪几天能学都能单独设](docs/images/plan-calendar.png)

<p align="center"><sub>新建计划：想学什么、学到什么程度、哪几天有空，填完就排。</sub></p>

## 能做什么

- **对话建计划**：说出目标和截止日期，先看大致安排，确认后再细排到每一天。
- **按天安排**：每天学多久、每周哪几天学都能单独设；落后或超前多少，一眼就能看到。
- **AI 老师上课**：2D 老师讲解，随时可以打断提问，已经会的内容可以跳过。
- **语音**：说话直接转成文字；讲解也可以点开朗读。
- **导入资料**：支持 PDF、Word、PPT、EPUB、Excel、Markdown、网页等，扫描件也能识别文字，还能针对资料提问。
- **练习与复习**：每课都有练习并自动判分，答错的知识点会标成“需要复习”，之后优先再出。
- **知识地图**：把计划里的知识点和它们之间的关系画成图。
- **多设备同步**：换一台电脑登录，计划和进度都在。
- **多家模型可选**：DeepSeek、通义千问、Kimi 等，可以在设置里切换。

## 开始使用

1. 打开[下载页](https://aiteacher.clannad.win/download)下载安装程序，它会自动装上最新版。
2. 安装完成后用邮箱注册。
3. 建第一个学习计划。

## 技术栈

| 部分 | 技术 |
| --- | --- |
| 服务端 | Java 21、Spring Boot 3.5、LangChain4j、PostgreSQL 17 |
| 登录 | Keycloak（OAuth2 / OpenID Connect） |
| 资料检索 | 本地 bge-small-zh 向量模型 + pgvector，按账号隔离 |
| 桌面端 | Python 3.12、PySide6（Qt WebEngine）、React 19、TypeScript、Vite |
| 2D 老师 | PixiJS + Live2D |
| 语音 | SenseVoice 语音识别、GPT-SoVITS 语音合成 |
| 文字识别 | RapidOCR |
| 安装与更新 | PyInstaller、Inno Setup；更新包用 Ed25519 签名校验 |
| 部署 | Docker、nginx、Cloudflare |

## 从源码运行

<details>
<summary>展开</summary>

- 开发运行：`python -m refactor.desktop.account_shell_main`。运行前先按 `refactor/learning/web/package-lock.json` 准备前端依赖，并构建界面。
- 打包：`python scripts/build_learning_installer.py --help`
- 需求和当前状态：[CURRENT_STATE](docs/project/CURRENT_STATE.md)

</details>

## 许可证

[MIT](LICENSE)
