# 账号资料服务端：Java 226 与 Python 226 恢复

当前公网后端为 Java `mentorflow-java-server:226`，数据库迁移版本为 0046。Python `mentorflow-slim-backend:226-python-fallback` 是已真机验证的备用，当前保持停止；可按本文件先准备、健康检查和内部对照，再在有授权时切流。Java 219 的 `rollback226` 镜像仍留存，但它不兼容 0046，不能作为已验证的数据库回退方案。服务器实际状态与验证范围见 [ARCH-09]、[RAG-01] 及 237/238-MF01 回执。

当前 Python 备用工具位于 `/opt/mentorflow/state/release238/deploy`，入口环境文件为 `release238/context.sh`；当前 Java 部署工具位于 `/opt/mentorflow/releases/java-profile-237/refactor/server-java/deploy`。备份和旧 Java 镜像保留。任何服务器操作均须对应用户授权，不要重跑已结束的后台 job。

## 1. 镜像、库与不可变项

- Java：`mentorflow-java-server:226`；实际 JAR SHA256 `39419dc9e31d7f60577547b2fb63aa9ee6395b556fd0ec08a61b96a7375a8077`。
- Python：`mentorflow-slim-backend:226-python-fallback`；image ID `sha256:2adb3d025759308a6f312cd25db0f804cc168bdf38ffaddc53003fe42549d83c`。Python 219 镜像只认旧 schema，不能接当前 0046。
- `/opt/mentorflow/state/release226/mentorflow-before226.dump`、旧 Java219 的 rollback226 标签保留；已有 0046 不重复迁移，不 downgrade，不覆盖备份。
- 备用操作只协调 `backend`、`oidc-loopback-proxy`，不操作 Java、postgres、migrate 或初始化服务；不执行 remove-orphans。

## 2. Compose 参数与 dry-run 的边界

python-compose.sh 从既有 Python backend 的 Compose labels 获取项目、项目目录、全部配置层，network45 最后；保留 `BACKEND_ENV`。`start` 用 `up -d --no-deps backend oidc-loopback-proxy`，`recreate` 用同样两个服务并加 `--force-recreate`，`stop` 先停代理再停 backend。

`PYTHON_COMPOSE_PROFILES` 未设置时仍兼容原来的 `account-core-mvp` 默认；**显式设为空字符串**可复现 234/238 真机成功的无 profile 命令。238 的 context 包含：

```sh
export BACKEND_ENV=/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env
export PYTHON_FALLBACK_OVERRIDE=/opt/mentorflow/state/release226/python226.override.yml
export PYTHON_COMPOSE_PROFILES=
```

服务器 Compose 5.5.0 支持 `COMPOSE_DRY_RUN=1`，可检查参数、目标服务和文件组合，但不能证明容器能否启动或健康。真机恢复已通过；如干跑出现旧容器 `backend exited (0)`，先检查它是不是正常停止留下的状态，不能只凭干跑结论判断备用不可用。也不能用 `config` 或假 Docker 代替真实健康与内部对照。

## 3. 已验证的备用准备与切流步骤

先保持 Java 接流量，记录 Java 容器 ID、StartedAt、镜像 ID、JAR，再在独立后台 job 中执行准备。以下路径是实际 238 环境；有变更时先核实容器 labels，不猜 Compose 参数。

```sh
. /opt/mentorflow/state/release238/context.sh
test "$(sh "$DEPLOY/cutover.sh" status)" = java

# 普通恢复；启动并等实际 healthy，暂不切入口。
sh "$DEPLOY/restore-python.sh" --prepare-only

# 若需按 234/238 的完整重建方式恢复，仅重建 Python pair：
# sh "$DEPLOY/python-compose.sh" recreate
# sh "$DEPLOY/restore-python.sh" --prepare-only

docker exec -i mentorflow-e1-preview-backend-1 python - < "$DEPLOY/probe.py"
# 必须 9/9，两个服务 ready，邮件状态一致后才切流。
sh "$DEPLOY/restore-python.sh"

for path in live startup ready; do
  code=$(curl -ksS --max-time 10 -o /dev/null -w '%{http_code}' "https://89.45.45.28/health/$path")
  printf '%s %s\n' "$path" "$code"
  test "$code" = 200 || exit 1
done
# --java-only 表示检查单个目标，此时目标是公网 Python 路由。
python3 "$DEPLOY/probe.py" --java https://89.45.45.28 --java-only

# 演练成功后立即回 Java，再检查一次。
sh "$DEPLOY/cutover.sh" java
python3 "$DEPLOY/probe.py" --java https://89.45.45.28 --java-only
sh "$DEPLOY/stop-python.sh"
```

命令清单中的任一步失败都不能继续往下执行。生产演练必须像 238 的 `prepare.sh` / `drill.sh` 一样置于带失败处理的 job，并启动全程健康采样：任何公网非 200，立即切 Java、停 Python，确认恢复后结束任务，不再演练。不能因 SSH 断开触发回退；重连等原 job。不要只复制上述多行命令而丢掉失败处理。

restore-python.sh 的 `--prepare-only` 验证 backend healthy、Python OIDC running，但不改 upstream；无参数维持“准备后切 Python”的接口。启动或健康检查失败时，仅在当前入口 Java 时清理 Python pair；Python 正在接流量时不会误停它。stop-python.sh 仍要求入口已是 Java。

已验证的恢复路径覆盖从停止状态启动、等待 healthy、内部 probe、短暂切流、切回 Java 和停备用。最新一轮健康检查为 25 轮、75 次请求均成功；公网切换演练 181 轮、543 次健康请求均成功。采样约每 0.5 秒一次，不能排除两次采样之间的瞬断。具体执行记录见 238-MF01 回执。

## 4. 后台任务、健康证据与验证边界

后台恢复任务以 `done` 且 exit code 0 判定操作完成；SSH断开或 UNKNOWN 本身不代表应用失败。每轮具体任务状态应读该次任务记录，不重跑已结束任务。

当前部署由 Java 接流量，Python 备用保持停止；Java backend/OIDC 的镜像与 JAR未被备用演练替换。真机恢复验证只证明备用启动、合同 probe 与切换路径；probe 使用虚假令牌，不能据此声称已登录账号的头像读写或其他用户功能通过。对应采样与任务记录见 238-MF01 回执。

## 5. Java 226 回退保留

Java 服务异常时，已验证的恢复路径是先准备并检查 Python 226，再按具体授权切流；步骤见上节。旧 Java 219 回退标签虽保留，但不兼容当前 0046，不能用作可行回退路径。未验证数据库降级，不能以降级数据库处理回滚。

日志计数继续使用 Java deploy/access-counts.py，只输出白名单路由、状态和计数。登录后用户功能由用户亲测，不用 401 代替。指定临时包用完才清理，运行源码、当前挂载、备份、回退镜像与服务器任务证据保留。
