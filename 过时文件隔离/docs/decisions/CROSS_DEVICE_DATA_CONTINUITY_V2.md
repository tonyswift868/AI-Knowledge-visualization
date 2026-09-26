# 跨设备数据连续性与 Portable Pack V2

Authority: `MAIN3_BATCH03_AND_CROSS_DEVICE_CONTINUITY_AUTHORITY`。
状态：产品合同已接受；V2、全量服务端同步与新设备端到端流程待实现／验收。
本合同不授权执行任何旧用户迁移、issuer 切换或账户 capability 激活。

```text
CROSS_DEVICE_DATA_CONTINUITY = REQUIRED
PORTABLE_PACK_V2 = REQUIRED_NOT_IMPLEMENTED
MIGRATION_EXECUTED = NO
TARGET_STORAGE_STABLE = NO
IDENTITY_ISSUER_STABLE = NO
```

## 数据分层

| Tier | 必须包含 | Authority 与恢复方式 |
| --- | --- | --- |
| SERVER_SYNC_TIER | account/workspace identity；plans/tasks/calendar；progress/mastery；review state；learner/student model；learning runs / supported history；nonsecret settings；asset/model metadata + stable resource ids；其他适合关系／文档数据库保存的结构化用户状态 | 经认证的账户与 workspace 服务端持久化；旧设备离线或不存在时，新设备仍能恢复。客户端本地缓存不拥有唯一副本。 |
| LOCAL_PORTABLE_TIER | large managed user files；raw materials；local-only large binary payloads；用户选择携带的 model binaries | 受控本地持久目录；通过迁移包复制，保留原来源；记录与云端相同的稳定资源 ID。 |
| REBUILD_OR_REDOWNLOAD_TIER | cache；generated indexes；可重建 derived artifacts；具有已验证重新下载来源且用户选择重新下载的 models/assets | 保存重建版本／输入摘要或可信下载来源、内容摘要、版本／许可；按用户选择重建／下载，不把缺失缓存当数据丢失。 |

服务端成功确认必须代表已持久提交。每个受支持实体须有租户／workspace 范围、稳定 ID、
修订／并发控制、幂等写入和删除标记语义；重试、离线队列、冲突、删除传播不得静默丢记录。
云端恢复以当前授权和服务端修订为准；不得靠上传旧 SQLite 覆盖云端的新状态。
“supported history”必须列出可恢复记录类型与保留范围，不能用泛称 history 掩盖未接通的存储。

## 新设备产品流程

Install → 原生 username/password login → server rehydrates cloud account state →
plans/progress/history immediately visible → 本地资源显示 `MISSING_ON_THIS_DEVICE` →
用户选择“导入迁移包” → verify package/account/workspace/hash → restore large local resources →
stable resource ids reconnect them to cloud state。

服务端恢复不等待旧设备在线，不等待大包导入，也不要求用户先导出结构化状态。
缺失的大文件只影响对应资源的打开能力，不能隐藏计划、进度、复习或历史。
账户认证失败、同步离线和本机资源缺失必须分别展示；没有同步证据时不得显示“已恢复”。
重新下载仅在来源与目标内容均可验证时推荐；用户仍可选择携带允许分发的模型二进制。

## Portable Pack V2 模式

| 模式 | 默认内容 | 大文件／模型选择 |
| --- | --- | --- |
| QUICK_EXPORT | 非秘密结构化恢复快照、资源／模型元数据、稳定 ID、范围／版本、完整性清单；在线服务端仍是当前云状态 authority | 默认不带大 payload／模型；可以显式选择部分大资源，清单精确列出已含／未含。 |
| FULL_EXPORT | QUICK 内容及所选 workspace 的全部可携带受管用户大文件／原始资料 | 默认带必须本地保留的大文件；模型可选择携带或使用已验证来源重新下载。无法重下载的本地唯一模型推荐携带；空间／许可限制必须在预览说明。 |

模式键固定为 `QUICK_EXPORT` / `FULL_EXPORT`；另有逐资源的 inclusion 决定，不能只靠模式名
宣称包完整。导出前展示总大小、所需磁盘空间、未包含资源及原因、下载／重建成本和隐私范围。
不因文件“大”自动舍弃唯一副本；若无法包含，必须明确阻塞或由用户接受可审查的排除。

V2 manifest 至少定义 format/schema version、package id、account binding、workspace bindings、
source revision/snapshot、export mode、资源 stable id、分类／包含策略、相对路径、长度、内容 hash、
云端引用／修订、可验证下载描述与重建依赖。资源引用必须在所选账户／空间内闭合。
manifest、成员清单和所有 payload 均纳入完整性校验；未知版本／字段策略必须显式定义。

## 完整性、身份绑定与保密

- 导入前从当前已认证服务端确认稳定 account/workspace 与 membership；包内声明或用户名
  不能授予归属。错误账号／workspace、未知 issuer 映射或权限变化必须 fail closed，写入为零。
- SHA-256 仅检测内容变化，不证明来源。V2 必须认证清单和 payload：使用经审查的认证加密
  容器及密钥管理合同，或绑定到已认证账户导出登记的可信签名；不自创密码协议。
  格式及密码库／参数／密钥恢复设计须单独评审，未完成前不能声称 V2 已可安全使用。
- 包含私有学习数据的 V2 包必须具备静态保密能力。解密凭据由独立安全通道提供，
  不写进包、manifest、日志或 receipt；不能使用账号登录密码作为便携包凭据。
- password、access token、refresh token、provider secret、私钥、provider session 和可用
  credential reference 均禁止进入包。非秘密 settings 用字段白名单；SQL／JSON／附件
  也需检查，不能仅按文件名排除。密钥及可执行下载链接中的秘密不得进入元数据。
- 受管用户文件按内容与用途分类；疑似含凭据的原始文件必须拒绝纳入并明确原因。
  用户不能通过“FULL”覆盖凭据禁令。普通私有学习内容依赖加密保护，不能声称 hash 保密。
- 校验大小／数量／解压比、路径穿越、重复和大小写冲突、硬链接／符号链接／reparse point、
  archive 特殊成员和 TOCTOU；先完整验证再在受控 staging 解包，失败清理仅限本次 staging。

## 非破坏导入与幂等性

源设备、源目录和包永不改写或删除。只复制至已验证的 durable local root；Temp、程序安装目录、
未知账户根均不得作为真实迁移目标。导入不能覆盖目标已有不同内容，更不能覆盖云端事实。
相同 stable id + 相同 hash 复用；不同 hash 隔离为显式冲突并保持双方原件。新路径映射
只更新本设备资源位置，不能重建云端 stable id 或把另一账户资源重绑进当前账户。

服务端／本地 receipt 以 account + workspace + package identity/content digest 及资源 ID 记录
验证与提交阶段；重复导入成功返回同一结果，不产生重复学习记录或重复资源。
本地复制与云端登记之间的中断必须可恢复：暂存、完整校验、原子发布、登记／重试顺序要有
故障注入证明；不能把已有 0036 ledger 表存在当成该导入事务已经实现。

## 分类与推荐优先

产品根据来源谱系、归属证据、stable id、修订、内容 hash、完整性和可重建性自动分类并推荐。
相同内容去重、仅路径变化重连、明确的新记录追加；主来源与开发副本标记必须来自证据。
CRLF/LF 等价只可在已知文本类型按明示规范比较；保留原字节，不能替代包的逐字 hash。
只有真正内容冲突、未证明归属或不同用户意图需要选择；先呈现人话差异和推荐理由。
不得把数百条 workspace 技术清单当最终产品选择流程。私有 84-workspace chooser 仅为 QA
证据；当前 03 wizard 是无执行器的预览壳，尚无自动分类／推荐 producer 或最终产品接线。

## 当前实现与缺口

[account_portability.py](../../desktop/account_portability.py) 当前 schema 为 V1，模式
`QUICK_ACCOUNT_EXPORT` / `FULL_PORTABLE_EXPORT`，明确排除 `model_binaries`，为明文 ZIP。
它只支持指定账号的新／空 vault 导入；不能当成已恢复云端 workspace 上的 V2 资源附加导入。
`MISSING_ON_DEVICE` 是现有枚举；产品合同的 `MISSING_ON_THIS_DEVICE` 需要显式适配，
不得仅改名即宣称新设备流程完成。V1 继续保持原合同；不得静默用 V1 输出冒充 V2。

现有 ORM／enterprise 表、bootstrap、资源元数据和 import ledger 仅是实现基础。
本轮没有交付全 SERVER_SYNC_TIER 的写入／恢复 API、客户端 rehydration 协议、V2 加密容器、
可恢复资源导入事务或分类推荐引擎。这些缺口在 queue 独立跟踪，Batch03 source PASS 不解锁迁移。

04 的只读证据证明历史记录存在、尚未证明数据丢失；没有事前完整 baseline，不能声称所有
记录／原字节完整。目标仍为 Temp；localhost → public issuer 的 identity namespace、subject
映射和 durable vault root 尚未稳定。须在真实迁移前证明相同账号／workspace／stable resource id
贯穿切换，禁止把旧 workspace 或其他 subject 数据直接重绑到当前账户。
归一化证据见 [04 continuity receipt](../governance/receipts/20260913-main3-batch03-continuity-evidence.json)。

## 解除迁移门禁的验收

1. 两设备独立 durable roots；旧设备完全离线，新设备登录即可看到同步提交的所有支持类型。
2. 逐类端到端写入／重试／恢复／删除／并发冲突覆盖，A/B 租户与 workspace 隔离成立。
3. QUICK/FULL + 可选模型矩阵；缺失资源可见；验证下载或导入后 stable id 原样重连。
4. 错账号／空间、篡改包、秘密、路径攻击拒绝且零写入；同包重复与中断恢复无重复／覆盖。
5. 源数据前后摘要一致；目标冲突保留双方；空间不足／断电恢复；加密及密钥恢复验收。
6. durable root 与 localhost/public issuer 连续性已证明；私有 QA 清单由智能推荐流程替代。
7. 取得真实旧用户数据迁移的独立执行授权；此前始终 `MIGRATION_EXECUTED = NO`。
