# 226 账号资料：部署与 Python 备用恢复

Java 226 当前正式服务，Python 226 备用已在 238 真机启动并通过内部对照、切入公网、切回 Java 演练。最终入口 Java、Python 两容器停止但保留，Java 容器未重建/重启。数据库维持 `0046_account_profiles`。本文件的服务器操作仍须有相应用户任务授权；不要再次执行已结束的 job。

当前可用的备用工具：`/opt/mentorflow/state/release238/deploy`，环境文件 `release238/context.sh`。Java 部署工具仍在 `/opt/mentorflow/releases/java-profile-237/refactor/server-java/deploy`。备份、旧 Java 回退配置和 `mentorflow-java-server:rollback226` 标签继续保留；不降级、不恢复整库。

## 1. 镜像、库与不可变项

- Java：`mentorflow-java-server:226`；实际 JAR SHA256 `39419dc9e31d7f60577547b2fb63aa9ee6395b556fd0ec08a61b96a7375a8077`。
- Python：`mentorflow-slim-backend:226-python-fallback`；image ID `sha256:2adb3d025759308a6f312cd25db0f804cc168bdf38ffaddc53003fe42549d83c`。不要用只认 0045 的 Python219 镜像接 0046。
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

服务器 Compose 5.5.0 支持 `COMPOSE_DRY_RUN=1`，仍可用来检查参数、目标服务和文件组合。**dry-run 不证明启动失败或成功，也不等于健康验证。** 234 的成功无 profile recreate 命令在 238 dry-run 中也返回 `backend exited (0)`；日志显示这个状态来自先前被正常停止的旧容器，实际执行同一启动动作一次成功。模拟启动后的健康依赖检查会看到旧容器状态。不能再仅凭这种错误否定真实备用，也不能用 config/假 Docker 作为备用可用的证据。

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

238 真机执行的是无 profile `recreate` → `--prepare-only` → 内部 probe；演练时又用 restore 的普通 `start` 路径，再切 Python。因此重建启动、普通 up、prepare-only、切流、停备用均有真机证据。

随后补验从已停止状态直接运行 `restore-python.sh --prepare-only`：UTC 22:42:48–22:42:56 再次实际启动、healthy、内部 probe 9/9，入口全程 Java；22:42:59 停备用完成。`jobs/resume` 与 `jobs/health-resume` 均成功，25轮75次健康请求全200。该检查没有再次切流，证明普通恢复不仅在已运行容器上幂等，也能从停止状态直接恢复。

## 4. 后台任务、健康证据与验证边界

既有 job.sh 用 nohup 独立执行，记录 started、output.log、exit_code、done；只有 done+退出码0代表成功，SSH非零和UNKNOWN不代表应用失败。查询 238 已完成任务：

```sh
sh /opt/mentorflow/state/release238/deploy/job.sh status /opt/mentorflow/state/release238/jobs/prepare
sh /opt/mentorflow/state/release238/deploy/job.sh status /opt/mentorflow/state/release238/jobs/drill
sh /opt/mentorflow/state/release238/deploy/job.sh status /opt/mentorflow/state/release238/jobs/health
```

238 三项均 SUCCEEDED 0。UTC 2026-10-28 22:35:59–22:36:08 准备；22:37:23 开始切 Python，22:37:26 公网 probe 9/9 后立即切 Java，22:37:27 公网 probe 再次 9/9，22:37:35 停备用后结束。181 轮三路径采样全200，两个方向观测中断均0秒（约0.5秒间隔，不排除间隔内瞬断）。Java backend 和 OIDC 的容器 ID、启动时间、镜像、重启次数前后不变，JAR一致。

服务器证据在 `/opt/mentorflow/state/release238`：`jobs/*`、`health-window.jsonl`、`java.before/current`、`jar.before/current`、准备与演练脚本。guard 只读 health，不读响应正文；失败才执行恢复。没有操作数据库、注册账号或读取用户令牌；probe使用虚假令牌合同，不能据此声称已登录资料读写或头像上传通过。

## 5. Java 226 回退保留

Java 正式版本要另行部署时，仍用 237 的 prepare-config、java-compose、checked-step 和独立 job。Python 已可先准备/probe后承接流量；如服务端检查真正失败，旧 Java 回退用 `/opt/mentorflow/state/release237/rollback-deploy` 和 rollback.sh，不恢复数据库。不要把 Java up/recreate 混入仅修 Python 的任务。旧回退 dry-run 与 234/236 实际回退证据保留。

日志计数继续使用 Java deploy/access-counts.py，只输出白名单路由、状态和计数。登录后用户功能由用户亲测，不用 401 代替。指定临时包用完才清理，运行源码、当前挂载、备份、回退镜像与服务器任务证据保留。
