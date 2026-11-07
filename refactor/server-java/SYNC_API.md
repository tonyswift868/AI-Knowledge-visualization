# 原子同步墓碑接口

`POST /api/enterprise/sync/v1/entities/{kind}/{entity_id}/tombstone`

沿用已验证Actor/Tenant、完整issuer/subject/organization/workspace隔离、事务、RLS、幂等receipt和initialized manifest generation。仅正常拥有该scope的调用者可写；客户端不能用body中的账号字段改变归属。

```json
{"request_id":"course-tombstone-unique","expected_revision":0}
```

请求只含这两个字段。request_id是1–128个ASCII字母/数字或`._:-`；expected_revision为严格整数0–2147483646。payload或其他额外字段被拒绝。

不存在且expected0，单个事务直接插入revision1墓碑，不先提交残缺活动计划：

```json
{"kind":"learning_run","entity_id":"refactor.v1:course_record:<sha256>",
 "revision":1,"deleted":true,"payload":{}}
```

| 情况 | 行为 |
| --- | --- |
| missing，expected0 | 新建墓碑1、成功receipt；同事务推进已initialized的generation |
| missing，expected大于0 | 409 SYNC_STALE_REVISION，不创建实体或成功receipt |
| 已存在live，expected正确 | 原CAS路径，revision+1，deleted=true、payload={} |
| expected落后/超前 | 409 SYNC_STALE_REVISION |
| 已墓碑且expected匹配，新request | 410 SYNC_ENTITY_TOMBSTONED |
| 同request、同新意图、同内容重放 | 返回原不可变成功响应，不重复写入或推进generation |
| 同request不同内容/旧删除意图 | 409 SYNC_IDEMPOTENCY_KEY_REUSED |

新意图摘要在原fingerprint列表前加`tombstone-if-missing-v1`；旧create/update/delete fingerprint不改，不重写历史receipt。新旧意图不能复用同一成功request_id冒充对方。Java与Python备用提供相同路径和语义。

旧`POST .../delete`继续要求expected至少1，missing仍404，已有客户端与旧receipt行为保留。通用bulk仍是每row保存点，可partial成功，不能当任意create+delete pair全成全败协议；新墓碑不依赖两row workaround。

旧服务器没有新route时会404，新客户端保留pending并按兼容策略说明不支持，不退回已知会暴露中间状态的非原子两次写。旧客户端继续旧路径，不会自动获得本修复。已存在半发布坏记录的先pull兼容需客户端单独处理，不能只因新endpoint存在就承诺自动修复旧数据。
