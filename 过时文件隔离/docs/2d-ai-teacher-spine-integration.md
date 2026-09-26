# 2D AI 教师角色路线

## 已确认的目标形态

参考页面不是恋活/PMX 3D，也不是单张静态图。页面创建了
`spine-player-canvas`，并加载 `spine-player-4.1.54.js`。它的核心是
Spine 2D 骨骼动画：高质量分层立绘负责画面，运行时只负责骨骼、网格、
换装、动作和表情。

MentorFlow 当前先使用原创透明立绘完成界面、状态和教学交互验收。旧版
恋活工具保留在折叠迁移区，默认不会启动 3D 预览。

## 正式 Spine 接入边界

不要自行解析 `.skel`、`.atlas` 或贴图。获得有效的 Spine Editor 授权和
有权使用的教师角色导出包后，直接采用官方
`@esotericsoftware/spine-player`，并锁定与导出文件一致的运行时版本。

每个教师资源包应包含：

```text
teacher-id/
  teacher.skel        # 或 teacher.json
  teacher.atlas
  teacher.png         # 可有多张图集
  teacher.manifest.json
```

ZIP 内的 `teacher.manifest.json` 使用相对路径，描述资源和教学语义：

```json
{
  "format": "mentorflow.spine-teacher",
  "runtime_version": "4.1",
  "license": {
    "spine_editor_license_confirmed": true,
    "character_rights_confirmed": true
  },
  "skeleton": {
    "binary": "teacher.skel",
    "atlas": "teacher.atlas"
  },
  "animations": {
    "idle": "idle",
    "listening": "listen",
    "thinking": "think",
    "explaining": "talk",
    "encouraging": "happy"
  },
  "skins": {
    "default": "teacher",
    "formal": "teacher-formal"
  }
}
```

在“AI 教师角色库”上传 ZIP 后，后端会：

- 阻止路径穿越、符号链接、未知文件和异常展开体积；
- 验证 Spine 4.1、授权确认、骨骼、atlas、贴图与五种动画映射；
- 将资源保存到本地教师登记表，并生成同源运行时 URL；
- 把新教师设为当前 Spine 教师。

也可用 `VITE_SPINE_TEACHER_MANIFEST_URL` 指定已经部署的同格式运行时
清单。没有激活的资源包、清单无效或加载失败时，网站自动回退到原创
透明立绘，不启动 Spine WebGL。

网站只把教学状态映射到动画名：

```text
等待输入 -> idle
用户输入 -> listening
模型生成中 -> thinking
流式回答 -> explaining
答对/完成 -> encouraging
```

播放器应在标签页隐藏时暂停，角色不可见时停止绘制，并在显示后只恢复一
次。换教师时销毁旧播放器和纹理，避免 WebGL 上下文与显存泄漏。

## 授权边界

- Spine Runtimes 源码公开，但受 Spine Runtimes License Agreement 约束，
  不是无条件 MIT 依赖。
- 集成和分发前需要确认有效的 Spine Editor 授权。
- 角色骨骼、图集、动作和换装资源必须另行确认商业使用权。
- 不复制参考页面或游戏中的角色资源。

## 当前落地

- `SpineTeacherPlayer.tsx`：官方 Spine 4.1.54 播放器封装、授权门禁、
  标签页/可见性暂停和销毁。
- `spine_teacher_registry.py`：ZIP 安全校验、授权确认、存储、激活与
  同源资源服务。
- `TeacherCompanionStage.tsx`：2D 教师舞台、教学状态和动画状态映射。
- `mentor-iris.png`：原创成人教师透明立绘。
- `teacher-avatar.css`：呼吸、氛围、状态和响应式布局。
- `teacher_companion_stage.test.tsx`：2D 模式、状态切换和提问交互契约。
