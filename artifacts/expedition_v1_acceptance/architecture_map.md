# 架构地图

```text
Learning Run terminal
        │ trusted publisher
        ▼
VerifiedLearningRewardLedgerORM ──► ExpeditionProfileORM
        │                               │
        │                               ├─ TrainingSlot / HeroOwnership / Stars / Awakening
        │                               ├─ LineupPreset / LineupMember
        │                               ├─ GachaPullRecord / Equipment / Pets
        │                               └─ StageProgress / BattleRecord / BattleEventLog
        │
        └── workspace_id + learning_run_id + idempotency_key

Hero/Enemy/Stage declarative catalog ──► BattleEngine(seed)
                                             │ append-only events
                                             ▼
                                      API battle event stream
                                             │ render only
                                             ▼
                                      Phaser battle surface
```

边界原则：服务端拥有奖励、抽卡、战斗结果和事件；前端只提交意图并播放已持久化事件。
