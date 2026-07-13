# 数据库与迁移验收

- ORM 模型：`backend/app/expedition_orm.py`，覆盖远征档案、奖励 ledger、五个训练槽、24 角色定义/持有/星级/觉醒、装备、宠物、阵容、gacha、关卡、战斗事件、里程碑、皮肤和资产注册表。
- Alembic：`backend/alembic/versions/0010_learning_expedition_v1.py`，只做 additive `create_all`，降级为空操作以避免删除用户数据。
- 启动兼容：`init_orm()` 注册新模型并为已存在 profile 补齐 `gacha_state` 列。
- 幂等约束：奖励 ledger、抽卡记录、阵容位置/角色、宠物、里程碑均有唯一键或唯一约束。
- 本次验证：临时 SQLite 建库通过；新增测试用临时库运行 6/6 通过；全量后端回归 106/106 通过。
