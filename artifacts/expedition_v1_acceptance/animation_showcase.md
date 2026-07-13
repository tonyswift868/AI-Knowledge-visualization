# 动画展示验收

当前后端事件流已覆盖：命中、暴击、闪避/未命中、治疗、护盾获得/吸收/破碎、状态施加/结算/移除、净化、驱散、能量变化、反击、嘲讽、死亡、回合和战斗结束。

战斗事件的 `animation_hint` 与 `timestamp_offset` 可供 Phaser 播放；事件不允许前端重写。减弱动效与 1x/2x/4x 播放控制属于前端集成项，本侧边车未改动前端。

PNG 联系表：`portrait_contact_sheet.png`；原始 SVG 清单：`asset_manifest.json`。
