# MentorFlow Java 21 JVM 运行参数

本页记录 `java-backend` 在现有 1 GiB 容器限制下的显式配置。参数不是容量承诺：它们把已量到的堆、元空间、线程和代码缓存形态变成可审查的预算，并给容器保留余量。测量日期：2026-11-11。

## 一览

| 参数 | 生产值 | 依据与目的 |
| --- | --- | --- |
| `-XX:+UseContainerSupport` | 开启 | 让 JVM 以容器限制而不是宿主机内存做人体工学计算；JDK 21 默认开启，这里显式写出边界。 |
| `-XX:+UseG1GC` | G1 | 4 核模拟下，G1 冒烟流 27 次 GC、0 次 Full GC、最长停顿 9.367 ms；Serial 为 68、4、82.917 ms。 |
| `-Xms` / `-Xmx` | `128m` / `320m` | G1 最后一次成功快照的 heap committed 为 135,168 KB（约 132 MiB），RSS 峰值约 233.2 MiB；固定值使 1 GiB 容器的预算可计算。 |
| `-XX:MaxMetaspaceSize` | `96m` | G1 Metaspace committed 42,906 KB、used 42,267 KB；给类元数据约 53 MiB 的上限余量。 |
| `-Xss` | `512k` | 默认 1 MiB 下线程栈 reserved 为 31.7–43.0 MiB；减半并按 128 个线程预算 64 MiB。 |
| `-XX:ReservedCodeCacheSize` | `64m` | 两次测量 code committed 为 16.7–17.4 MiB；保留编译代码增长空间。 |
| `-XX:MaxDirectMemorySize` | `64m` | 本轮没有独立 Direct Buffer 计量；先给直接内存单独护栏，避免与堆争用整 GiB。增长后需重新测量。 |
| `-XX:+ExitOnOutOfMemoryError` | 开启 | 内存不可恢复时让进程退出，由现有 `restart: unless-stopped` 拉起干净实例。 |
| `-Xlog:gc*,safepoint` | `/tmp/mentorflow-gc.log`，5×4 MiB | 保留 GC 与 safepoint 证据；滚动上限约 20 MiB，落在已有 128 MiB `/tmp` tmpfs。 |
| Heap dump | 不设置 | `/tmp` 没有持久 dump 挂载，320 MiB 堆上限的 dump 可能挤满 128 MiB tmpfs，且可能含资料内容；需另开持久路径和审批后再启用。 |

现有 truststore 参数和 `-Djava.io.tmpdir=/tmp` 保持不变；`-XX:ActiveProcessorCount=4` 只用于本机复现线上 `nproc=4`，不写入生产，因为 backend 没有 Docker CPU quota/cpuset 限制。

## 参数怎么定

### 容器感知与 GC：`UseContainerSupport`、`UseG1GC`

`UseContainerSupport` 让 JVM 的资源人体工学使用 cgroup 可见资源。线上只读探针得到 `nproc=4`，backend 的 `NanoCpus=0`、`CpuQuota=0`、`CpuPeriod=0`、`CpusetCpus=`，所以生产不锁死处理器数；测量时用 `-XX:ActiveProcessorCount=4` 复现核数。

本机是 Windows JDK，同版本 Windows port 不暴露 `UseContainerSupport` flag；因此本机只用 `MaxRAM=1g` 模拟容器，其他参数用 `java -version` 做了选项解析检查。生产目标是 Linux Temurin 21，正式上线前仍应在 Linux 镜像启动 smoke 中确认该平台 flag（本任务不部署）。

同一 JDK 21、`-XX:MaxRAM=1g`、同一 RAG154 真实服务流程的有效结果如下：

| 指标 | SerialGC | G1GC |
| --- | ---: | ---: |
| 测试耗时 | 62.43 s | 34.82 s |
| GC 事件数 | 68 | 27 |
| Full GC | 4 | 0 |
| 最长停顿 | 82.917 ms | 9.367 ms |
| 服务 GC 日志观察到的堆使用高水位 | 90M -> 67M（容量 116M） | 82M -> 82M（容量 109M；含一次 Cleanup） |
| GC.heap_info 稳定快照 used | 89,382K（约 87.3 MiB） | 32,263K（约 31.5 MiB） |
| RSS 采样峰值 | 313.6 MiB | 233.2 MiB |
| 线程采样峰值 | 55 | 47 |

因此显式选择 G1：在这个负载下吞吐和停顿都更好，且没有 Full GC。这里不是把一次冒烟结果当成生产 SLA；完整 RAG355 数据缺失，长期容量仍需真实压测。

面试问：“G1 一定比 Serial 快吗？”答：不一定；小堆、低并发时 Serial 可能更简单。这里是用相同 JDK、内存上限和负载测出 G1 的停顿/耗时更低，才选它，并保留回滚到 Serial 的测量证据。

### 固定堆：`-Xms128m -Xmx320m`

线上现状是 1 GiB `mem_limit`，历史默认值是初始堆 16 MiB、最大堆 256 MiB。有效测量中，G1 最后一次成功 NMT 快照的 Java Heap committed 为 135,168 KB，Serial 为 42,388 KB；G1 RSS 峰值约 233.2 MiB。于是初始堆取约 128 MiB，接近该流程的稳定 committed 量，减少运行中扩容；最大堆取 320 MiB，给 RAG 峰值留出增长空间，同时不把容器上限误当成 Java heap 上限。

固定 `-Xms/-Xmx` 与 `MaxRAMPercentage` 的区别：百分比会随容器上限变化，适合统一镜像多种规格；本服务还要给 native extraction、缓存、线程和 `/tmp` 留空间，固定值更容易做代码审查和回滚。1 GiB 上限改变时必须重新做预算，不自动沿用 320 MiB。

不设时，JVM 会按容器人体工学自行选择，默认值可能随 JDK、容器限制或镜像变化，非堆和 `/tmp` 会与堆竞争；设得过小则可能在资料导入时 OOM。因此 `320m` 是本次负载和预算的护栏，不是容量保证。

### 元空间：`-XX:MaxMetaspaceSize=96m`

元空间存放类元数据，属于 native 内存，不计入 Java heap。G1 测到 committed 42,906 KB、used 42,267 KB；96 MiB 仍有约 53 MiB 的增长空间。若不设上限，类加载或代理增长会直接挤压 1 GiB 容器；若设得过小，则会以 `OutOfMemoryError: Metaspace` 失败。出现该错误时应先看类加载量，再调整上限，不能盲目加堆。

面试问：“为什么 MaxMetaspace 不能和 Xmx 共用？”答：两者是不同的内存区域；Xmx 只限制 heap，元空间默认在 native 区增长，所以预算要单列。

### 线程栈：`-Xss512k`

默认线程栈约 1 MiB。NMT 快照显示 Serial 31 个线程的 stack reserved 31,744 KB、G1 42 个线程的 stack reserved 43,008 KB，和 1 MiB/线程相符。改为 512 KiB，并按 128 个线程做容器预算，栈项上限为 64 MiB。若未来线程数超过这个预算，先量线程峰值和调用深度再改；栈过小会以 `StackOverflowError` 失败，不能只为省内存无限压缩。

### 代码缓存：`-XX:ReservedCodeCacheSize=64m`

代码缓存保存 JIT 编译后的机器码。两次有效测量的 NMT Code committed 为 Serial 16,705 KB、G1 17,400 KB；64 MiB 给编译代码和分层编译留出余量。未设上限时它会和其他 native 区竞争；过小则可能提早停止编译、吞吐下降。若将来出现 code-cache 满或编译退化，应根据 JIT 日志和 NMT 调整。

### 直接内存：`-XX:MaxDirectMemorySize=64m`

本轮没有单独观测 Direct Buffer，NMT 的 `Other` 不能当作直接内存读数，所以这里明确记录为未测。64 MiB 是独立护栏，不声称已证明业务只需 64 MiB；若 RAG/HTTP 客户端增加大块 direct buffer，应先量峰值再上调，并从容器预算中扣除同等空间。未设置时直接内存可与 heap/native 共同争用容器上限，故不设护栏的风险更难定位。

### OOM 行为与 dump

`-XX:+ExitOnOutOfMemoryError` 在不可恢复的 OOM 后退出 JVM。现有 Compose 使用 `restart: unless-stopped`，因此让容器重新启动比保留一个半死进程更可诊断；这不是数据恢复机制，应用仍需依赖现有持久化流程。

没有设置 `-XX:+HeapDumpOnOutOfMemoryError`：dump 可能接近 `Xmx=320m`，而 backend 只有 128 MiB `/tmp`，且 dump 可能包含用户资料/请求内容，当前没有批准的持久 dump 卷。若需要诊断，应另开持久目录、权限和保留策略后再加 `-XX:HeapDumpPath`，不能把 dump 写进现有 tmpfs。

### GC 日志：统一日志与滚动

配置为：

```text
-Xlog:gc*,safepoint:file=/tmp/mentorflow-gc.log:time,uptime,level,tags:filecount=5,filesize=4M
```

它同时记录 GC 和 safepoint，包含时间、运行时长、级别和标签；最多保留 5 个 4 MiB 文件，约 20 MiB。日志在 `/tmp`，随容器重启清掉，避免把临时 GC 证据误当成持久审计；需要长期趋势时应接入外部日志，而不是扩大 1 GiB 容器内的文件。

## 内存预算（1 GiB 容器）

| 项目 | 预算 |
| --- | ---: |
| Java heap（`Xmx`） | 320 MiB |
| Metaspace（上限） | 96 MiB |
| 线程栈（512 KiB × 128） | 64 MiB |
| Code cache | 64 MiB |
| Direct memory | 64 MiB |
| JVM/GC/符号/编译器/分配器等其他 native | 160 MiB |
| `/tmp` tmpfs（缓存、native 解压、GC 日志的上限） | 128 MiB |
| **合计** | **896 MiB** |
| 容器上限 | 1,024 MiB |
| **保留余量** | **128 MiB（12.5%）** |

这是一张保守的同时占用预算：tmpfs 是可用上限而非启动即分配，线程栈的 128 线程也是预算上限；Direct memory 本轮未实测。余量用于 RSS 采样未覆盖的瞬时 native 分配和内核开销，所以不能从这张表推出容量或并发 SLA。

## 测量边界与复核

- JDK：`D:\MentorFlow\.local\java\jdk-21.0.12.1+1`，Temurin 21.0.12.1+1 LTS；模拟参数为 `-XX:MaxRAM=1g -XX:ActiveProcessorCount=4`。
- 负载：`test_rag154.py::test_rag154_search_filters_selected_documents_and_ranks_relevant_passages`，启动真实服务、导入并检索；Serial 与 G1 各一次有效运行，均 `1 passed`。
- 量具：GC 统一日志、NMT、`GC.heap_info`、RSS/线程采样；GC 日志服务段观察到的使用高水位为 Serial 90M、G1 82M，稳定快照 used 为约 87.3/31.5 MiB；这些是本次冒烟流的观测值，不是容量上限。最终 attach 有一次 `AttachNotSupportedException` 竞态，但此前快照已成功保存。
- 限制：RAG355 所需 `RAG355_DATA` 不存在，不能把本轮数字当完整容量压测；RSS/线程采样点少；Direct Buffer 未单独测量；GC 日志包含嵌入式 PostgreSQL 等其他 JVM 段，表中只取服务 JVM 段。

## 官方资料（查阅日期：2026-11-11）

- Java 21 `java` 选项：<https://docs.oracle.com/en/java/javase/21/docs/specs/man/java.html>
- G1 总览：<https://docs.oracle.com/en/java/javase/21/gctuning/garbage-first-g1-garbage-collector1.html>
- G1 调优：<https://docs.oracle.com/en/java/javase/21/gctuning/garbage-first-garbage-collector-tuning.html>
- Java 21 GC 调优总览：<https://docs.oracle.com/en/java/javase/21/gctuning/index.html>
- `jcmd`、`VM.flags`、`GC.heap_info`：<https://docs.oracle.com/en/java/javase/21/docs/specs/man/jcmd.html>
