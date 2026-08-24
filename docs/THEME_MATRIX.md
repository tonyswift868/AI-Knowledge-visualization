# MentorFlow 课堂主题矩阵

状态日期：2026-08-22  
默认主题：`sakura`  
实现：`desktop/classroom_theme.py`、`desktop/classroom_page.py`、`desktop/system_page.py`

## 设计原则

- 樱花粉是公共默认值，不能被开发者个人设置、迁移包或安装包覆盖。
- 每套主题必须改变结构 token，而不只是替换主色：背景图案、表面层级、气泡、边框、阴影、教师舞台、装饰层、纹理、环境叠层、图标处理和动态强度。
- 主题只改变视觉，不改变教师、Provider、课堂状态、学习进度、输入草稿或附件。
- 设置页提供外部主题画廊、预览、应用和“重置为樱花教室”。
- 本机只保存 `appearance/classroom_theme`；安全迁移快照明确不导出该键。

## 16 套主题

| ID | 名称 | 背景/纹理 | 对话与表面 | 教师舞台/环境动态 |
| --- | --- | --- | --- | --- |
| `sakura` | 樱花教室 | 花瓣、丝绸、粉色光影 | soft-card、柔和花瓣阴影 | sakura-window、petal-drift |
| `cream` | 奶油自习室 | 纸张、纤维、暖米色 | paper-note、纸页抬升 | reading-lamp、dust |
| `sky` | 晴空课堂 | 云层、薄雾、蓝白通透 | glass、云朵柔影 | sky-window、cloud-float |
| `night` | 星夜学院 | 星点、绒面、深蓝低眩光 | dark-glow、夜光边缘 | moon-window、star-drift |
| `chalkboard` | 黑板课堂 | 粉笔网格、黑板纹理 | chalk-rounded、虚线粉笔边 | board-frame、chalk-dust |
| `library` | 暖心图书馆 | 木纹、纸张、暖灯 | book-page、木质阴影 | library-lamp、dust-motes |
| `after_school` | 放学后教室 | 窗格夕光、墙面纹理 | rounded、晚霞阴影 | classroom-window、dust-ray |
| `rainy_window` | 雨窗自习 | 雨滴、冷凝、蓝灰玻璃 | frosted、湿玻璃阴影 | rain-window、rain-streaks |
| `watercolor` | 水彩课堂 | 晕染、棉纸、多色水彩 | watercolor、颜料柔影 | painted-easel、wash-blobs |
| `notebook` | 纸张学习手帐 | 横线纸、手帐批注 | notebook、虚线和卷页 | journal-clip、margin-notes |
| `cyber` | 赛博学院 | 电路、碳纤维、霓虹深紫 | neon-panel、霓虹边缘 | holo-stage、scanline |
| `whiteboard` | 极简白板 | 白板、干擦纹理、大留白 | minimal、hairline | marker-board、marker-dots |
| `retro_lab` | 复古电脑实验室 | 像素网格、CRT 扫描 | pixel、双线/像素阴影 | crt-stage、scanlines |
| `autumn` | 秋日课堂 | 枫叶、羊皮纸、橙褐暖色 | warm-card、叶影 | autumn-window、falling-leaves |
| `snow` | 雪日自习室 | 雪花、霜纹、冰蓝 | frost、冰雪柔影 | snow-window、snow-drift |
| `coffee` | 咖啡学习室 | 咖啡环、牛皮纸、木桌 | cafe-card、桌面阴影 | cafe-window、steam |

## 当前验证

- Qt/offscreen：`desktop/tests/test_classroom_theme_manifest.py`、`desktop/tests/test_settings_navigation.py`、`backend/tests/test_classroom_visual.py`、`backend/tests/test_native_desktop_knowledge_map.py`，共 **68 passed**。
- 离线视觉脚本：`scripts/render_classroom_theme_acceptance.py`。
- 当前截图目录：`E:\Codex\Temp\mentorflow-w36-theme-structural-df3370f8b30d419d807dc775d56991e6`。
- `summary.json` 记录 `render_signature_unique=16`、`render_signature_theme_count=16`；签名同时覆盖图案、气泡形态/边宽、阴影、舞台框架、装饰层、纹理、控件 chrome、边框、圆角、图标和背景方向，不能只靠主题名或主色制造唯一性。
- 16 套主题和樱花沉浸模式均成功渲染；所有截图的 `draft_preserved=true`、`session_preserved=true`，沉浸模式还验证顶部壳隐藏、编辑器与恢复按钮保留。
- 人工抽查的樱花、星夜、赛博、手帐和奶油截图可在不看主题名的情况下区分：深色主题的输入/按钮/滚动条不再继承白底粉边，手帐使用纸张横线与虚线控件，赛博使用紧角霓虹面板，樱花继续保持默认粉色软卡片。

## 尚需发布前验证

- 最新源码打包后的真实 Windows 窗口逐主题人工验收；
- 高 DPI、缩放、窄窗口和不同字体环境；
- reduced-motion 下的环境层；
- 将最终截图复制到稳定的发布证据目录，而不是依赖临时目录。
