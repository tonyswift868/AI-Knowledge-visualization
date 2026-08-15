# MentorFlow 云端运行边界

本地安装包可以通过 `E:\MentorFlowData\cloud_endpoint.json` 连接远程 MentorFlow API：

```json
{
  "api_base_url": "https://api.example.com"
}
```

该文件只包含公开的 HTTPS 地址，不包含数据库密码、短信密钥或 Provider 密钥。文件存在但格式错误时，桌面端会失败关闭，不会静默退回本地数据库。

## 云端服务端

服务器端仍需由运维环境提供持久化存储和 HTTPS 反向代理。最小单机部署可以把当前两个 SQLite 数据库和上传目录放在云服务器的持久化磁盘；桌面客户端只通过 HTTPS API 访问它们。发布前必须完成：

- `MENTORFLOW_AUTH_REQUIRED=1`；
- `MENTORFLOW_COOKIE_SECURE=1`，并将 `MENTORFLOW_ALLOWED_ORIGINS` 设置为实际 HTTPS 前端来源（不要使用 `*` 搭配凭据）；
- `MENTORFLOW_DB`、`MENTORFLOW_ORM_DB` 和上传目录指向云服务器持久化盘；
- 反向代理只开放 HTTPS，应用本身不暴露数据库端口；
- 云端凭据使用部署平台的 Secret 注入：设置 `MENTORFLOW_CREDENTIAL_STORE=environment`，并使用 `credential_store.environment_secret_name("mfcred_...")` 计算变量名；业务 API 不提供写入或删除云端 Secret 的能力；
- 腾讯短信直连使用官方 `tencentcloud-sdk-python`，并设置 `MENTORFLOW_SMS_PROVIDER=tencent`、短信应用 ID、已审核签名和模板 ID；模板需要两个变量（验证码、有效分钟数）；
- 数据库、上传资料和日志做加密备份，备份恢复通过完整性检查；
- 所有工作区、任务和资料查询继续经过当前用户的归属检查。

这只是把数据主副本移到云服务器的最小部署。若需要多实例、高可用或更大规模，应再将 ORM 数据迁移到托管 PostgreSQL，将资料迁移到私有对象存储，并由服务端生成限时下载链接；桌面端不保存任何云数据库凭据。

## 腾讯短信字段对应

云端直连腾讯短信时，当前适配器使用以下配置：

- `MENTORFLOW_TENCENT_SMS_SDK_APP_ID`：短信控制台中创建应用后得到的 `SmsSdkAppId`；
- `MENTORFLOW_TENCENT_SMS_SIGN_NAME`：审核通过的国内短信签名正文，不是签名 ID；
- `MENTORFLOW_TENCENT_SMS_TEMPLATE_ID`：审核通过的验证码模板 ID；模板变量顺序必须是验证码、有效分钟数；
- `MENTORFLOW_TENCENT_SECRET_ID_REF` 与 `MENTORFLOW_TENCENT_SECRET_KEY_REF`：只保存 `mfcred_...` 引用，真实 SecretId/SecretKey 由云端部署 Secret 注入。

桌面本地版不应把腾讯 SecretId/SecretKey 放入 EXE、SQLite 或配置文件。若要让本地版发送真实短信，应配置一个独立 HTTPS 网关：桌面版只保存网关 URL、发送路径和 Credential Locker 引用，网关再调用腾讯短信。没有网关或云函数时，本地版只能完成验证码状态机演示，不能发送真实短信。

网关的最小合同是：接受桌面版发送的 `to`、`message`、`purpose` 和
`idempotency_key` JSON，并在真正接受供应商发送后返回 HTTP 2xx 与
`{"accepted":true}`。HTTP 200 但缺少该字段会被客户端视为发送失败，避免错误地告诉用户“短信已发送”；`idempotency_key` 必须用于网关侧去重。

## 两个软件

如果两个软件共用账号，服务端为每条业务数据增加产品范围（例如 `product_id`），并在每个读写接口执行用户和产品双重过滤。若两个软件完全独立，则使用不同的 API 和数据库。不能仅靠客户端传入的产品名或工作区标题做隔离。
