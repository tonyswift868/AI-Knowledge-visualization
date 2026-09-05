# Desktop 登录主题交付

Dispatch: `MF-A-LOGIN-SMOOTH-20260905-01`

Task: `MENTORFLOW_DESKTOP_LOGIN_THEME_V1`

Base: `f458db4261af6f62786d551ac9bc0974a3429b46`

## 当前成果与界面边界

`mentorflow-desktop-v1` 是 Keycloak 26.7.3 官方 `keycloak` 登录主题的
小型子主题。内容区直接显示 MentorFlow、账号、密码、官方密码显隐按钮、
单一登录按钮和原位错误提示。使用 `desktop/style.py` 现有中文字体、粉色、
背景色、10px 圆角；没有外部字体或新前端依赖。

主题去掉重复 realm 横幅与卡片边框/阴影，保持自然文档流。极小窗口或长错误
允许一层页面滚动，不用隐藏溢出来伪装无裁切。没有新增跳转按钮、窗口、成功
中转页面或确认动作。必要认证挑战仍使用官方模板，不能隐藏或跳过。

固定的 07 接口边界：主题提供内容区和唯一产品标题/登录表单；Desktop 提供
外层窗口、直接加载授权地址、记住登录和 bootstrap。主题不提供第二套
Remember Me 开关，也不接管原生登录持久化。此交付**不表示现有 Desktop
启动流程已经改为单窗口**，这部分属于 07；本任务不写 Desktop，也不等待 07。

## OIDC 与安全边界

realm JSON 的唯一语义变化是 `mentorflow-desktop.attributes.login_theme`。
realm 默认主题、其他客户端、client_id/azp、mentorflow-api audience mapper、
basic/sub、PKCE S256、回调、scope 均保持原值。state、nonce、TLS、token
交换与校验代码未改。Direct Access Grants 保持关闭；没有新增密码 API、
密码提取桥接、自定义 JS 验证、日志记录或自动填写。

模板保留官方 POST `url.loginAction`、credentialId、条件分支、错误消毒、
aria-live 和密码显隐模块。注册与密码找回仅沿用 realm 已有开关和真实官方
URL，不新增账号功能。注册当前关闭；找回开关开启，但邮箱投递未在本任务验证。
其他错误和认证挑战仍使用官方文案，未伪造全部中文化完成。

## 最小发布接线（本任务没有执行部署）

沿用既有 MVP 的完整 exact Git source release 布局与
`deploy/enterprise/staging/rp05/compose.account-core-mvp.yml`，只新增只读挂载：

```text
../../themes/mentorflow-desktop-v1
  -> /opt/keycloak/themes/mentorflow-desktop-v1:ro
```

不需要构建 Keycloak/Backend/Worker/Docling/Frontend 或安装 Node/React。
后续授权发布需保留完整主题目录、realm JSON、Compose 和 readiness 脚本的
相对路径；单独复制 Compose 文件不构成可运行发布。该挂载仅接入 Account
Core MVP；通用 RP-05 打包器/其他 profile 未扩展主题发布。

新建 realm 可导入客户端主题属性。**已存在的 realm 不会因 `--import-realm`
重启而更新**：后续授权操作员先确认运行 Keycloak 为 26.7.3、主题目录可读，
再在 `mentorflow` realm 的 `mentorflow-desktop` 客户端设置 Login theme 为
`mentorflow-desktop-v1`（即 `attributes.login_theme`）。不能修改 realm 全局
Login theme、认证流、其它属性或其他客户端。先留存该客户端原主题属性用于
回滚，不导出或记录秘密。

MVP 的 `keycloak-ready` 新增只读检查：当期待的客户端主题属性缺失/不匹配时
以 exit 31 失败；它不会自行调用更新接口。其它 profile 不传期待主题变量，
既有 readiness 行为不变。属性检查不是实际模板渲染或登录验收。

发布时沿用既有服务启动/重建流程使只读挂载生效；生产保留主题缓存设置。
回滚需还原客户端原 Login theme 属性与对应源版本/挂载，并按既有发布机制
重启 Keycloak 使缓存失效；只改 Git 文件不足以回滚已保存的客户端属性。
不修改数据库结构、业务 API、用户数据、凭据或其它运行服务。

## 验证与明确待运行项

已运行：

- `python -m pytest tests/theme/test_login_theme.py -q -p no:cacheprovider`：11 passed。
  覆盖官方模板还原哈希、单表单、错误/显隐/安全分支保留、完整 realm 语义差异、
  完整 Compose 语义差异、原主题 token、只读失败关闭的绑定检查。
  POSIX shell 实测正确绑定通过、错误/缺失绑定 exit 31、不启用主题检查时通过。
- `tests/theme/login_theme_preview.cjs`：9 个静态浏览器场景 PASS。
  100%/125%/150% 对应 480×600 物理内容矩形的 CSS/DPR 模拟，每档分别覆盖
  正常、错误、长错误；横向无裁切、无嵌套滚动、正常表单可完整显示。
  Tab 顺序、Enter 触发表单提交事件、官方密码显隐 JS 及 aria-label 均通过。
- 人工查看静态截图；唯一品牌、表单和原位错误可见。截图/JSON 仅为静态证据。
- readiness shell `sh -n` 和浏览器测试脚本 `node --check` 均通过。

静态运行需要已有 Playwright/Chromium 和提取的官方 themes + themes-vendor
26.7.3 JAR（将两个 JAR 的 `theme` 目录合并在临时目录）；设置
`MF_PLAYWRIGHT_MODULE`、可选 `MF_CHROMIUM_EXECUTABLE`、
`MF_KEYCLOAK_THEME_ASSETS`，执行：

```text
node tests/theme/login_theme_preview.cjs <绝对临时输出目录>
```

本轮未安装 Java/Docker/Keycloak 运行环境，也未访问服务器或使用账号密码。
以下是 **NOT_RUN / PENDING_REAL_KEYCLOAK**，不是 PASS：

1. 真实 FreeMarker/Keycloak 渲染、正确密码完成授权、错误密码服务器校验。
2. 真实 Qt 窗口 100%/125%/150% Windows 缩放、Tab/Enter 与密码显隐。
3. 必要 OTP/WebAuthn/验证邮箱/重置密码等挑战及正常回调直接返回 Desktop。
4. 已存在 realm 的客户端主题激活、主题资源 HTTP 加载、其它客户端登录回归。

静态 fixture 不执行 FreeMarker，也不连接认证端点；错误文字由 fixture 设置，
Enter 的网络提交被拦截。它不能代替以上真实验收。

交付状态：源码主题与最小接线 READY_FOR_INTEGRATION；真实认证/单窗口端到端
体验待后续授权环境验收。Desktop 写入 0，VPS 修改 0，打包/部署/推送 0。
