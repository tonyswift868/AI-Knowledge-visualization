# MentorFlow 教师假动作审计

状态：`VERIFIED_CURRENT_WORKTREE`  
日期：2026-08-22

## 硬规则

以下行为不得被命名为教师动作：

- 整个模型、图片、Canvas 或教师舞台的 `scale`；
- 整体 `translate`、跳动、摆动；
- 整体 `rotate`、倾斜、skew 或拉伸；
- 没有模型骨骼、原生 motion/expression 或安全内部参数依据的 CSS 动画。

摄像机 fit、镜像、用户缩放/平移和首次淡入只属于查看与布局控制，不能由 `thinking`、`praising` 等教学状态触发。

## 渲染器结论

| 渲染器 | 当前边界 | 结论 |
| --- | --- | --- |
| `image2d` | 静态插画、形态切换、环境效果 | 不公开 wave/think/talk；整图呼吸与语义 keyframes 已删除 |
| `layered2d` | 项目自有分层关节姿态 | 保留关节级变化；根 pelvis 跳动删除 |
| Spine 回退 | 静态图片 + 非角色环境动态 | `teacher-breathe` 整图呼吸删除 |
| `live2d2d` | 原生 motion/expression/安全 Core 参数曲线 | physics destination 禁写；动作不改 layout/position/scale/rotation |
| `dragonbones2d` | 真实 armature 动画 | 只播放资源实际登记动作，不用 CSS 补造 |

## 自动与运行时证据

- `no_fake_whole_model_motion.test.ts` 守护 image/layered/Spine/Live2D；
- `live2d_action_runner_v2.test.ts` 验证真实参数范围、physics 零写入、transform 不变、冻结/恢复；
- 7 个 Live2D/舞台测试文件：**59 passed**；
- Playwright `live2d_motion_inspector.spec.ts`：**1 passed**；
- 浏览器同一 stage 的动作前后 bounding box 完全相等；
- 待机图睁眼、BLINK 冻结图闭眼，调试状态为 `animation: BLINK`；
- 证据：`E:\Codex\Temp\mentorflow-w36-live2d-final3-evidence`；
- `npm run build`：PASS。

## 允许保留的动态

- 雨滴、花瓣、星点、扫描线、光晕等课堂背景氛围；
- 皮肤切换淡入淡出；
- 用户主动镜像、缩放、平移与构图；
- 模型内部骨骼、网格、物理、motion、expression 与安全参数动画。

## 未完成

当前证据已经证明 BLINK 不是假动作，也证明动作路径不改变舞台 transform；尚未对所有 semantic action 做真实设备逐项人眼质量评估，也未验证所有 GPU、高 DPI、长期运行和正式安装包。未逐项验证的动作必须继续标为未验证。
