package rag154;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import win.clannad.mentorflow.rag.RagEmbedding;

/** Small local-only benchmark entry point; it performs no remote model calls. */
public final class RagEmbeddingProbe154 {
    private static final String[] CHUNKS = {
        "关系数据库事务通过隔离级别控制并发访问时可见的数据。",
        "B 树索引支持等值条件和范围条件，也可以按排序顺序返回数据。",
        "HTTP POST 请求将数据发送给服务器，请求体类型由 Content-Type 声明。",
        "JavaScript Promise 表示异步操作最终完成或失败及其结果值。",
        "全文检索索引使用倒排结构寻找包含查询词的文档。",
        "数据库锁用于协调并发事务对共享行和表的访问。",
        "向量余弦相似度衡量两个向量方向的接近程度。",
        "SQL 查询计划可通过 EXPLAIN 命令查看。",
        "HTTPS 在 HTTP 之上使用 TLS 保护传输数据。",
        "PostgreSQL MVCC 让查询读取某一时刻的数据快照。"
    };

    private RagEmbeddingProbe154() {}

    public static void main(String[] args) throws Exception {
        RagEmbedding embedding = new RagEmbedding();
        System.out.println("READY " + ProcessHandle.current().pid());
        System.out.flush();
        System.in.read();

        long coldStart = System.nanoTime();
        float[] first = embedding.embed(CHUNKS[0]);
        long coldNanos = System.nanoTime() - coldStart;
        if (first.length != 512) throw new IllegalStateException("Expected 512 dimensions, got " + first.length);

        List<Double> warmMillis = new ArrayList<>();
        for (int i = 0; i < 40; i++) {
            long start = System.nanoTime();
            float[] vector = embedding.embed(CHUNKS[i % CHUNKS.length]);
            warmMillis.add((System.nanoTime() - start) / 1_000_000d);
            if (vector.length != 512) throw new IllegalStateException("Embedding dimension changed");
        }
        Collections.sort(warmMillis);
        double average = warmMillis.stream().mapToDouble(Double::doubleValue).average().orElse(0);
        System.out.printf(java.util.Locale.ROOT,
                "RESULT dimensions=%d cold_ms=%.3f warm_avg_ms=%.3f warm_p50_ms=%.3f warm_p95_ms=%.3f%n",
                first.length, coldNanos / 1_000_000d, average,
                warmMillis.get(warmMillis.size() / 2), warmMillis.get((int) Math.ceil(warmMillis.size() * .95) - 1));
        System.out.flush();
    }
}
