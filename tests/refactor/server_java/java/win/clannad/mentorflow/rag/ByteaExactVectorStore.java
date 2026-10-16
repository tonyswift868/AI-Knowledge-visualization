package win.clannad.mentorflow.rag;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.List;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/** Test-only exact cosine scan for the embedded PostgreSQL fixture without pgvector. */
@Component
@Profile("rag154-test")
public final class ByteaExactVectorStore implements RagVectorStore {
    @Override
    public float[] findReusable(JdbcTemplate jdbc, String scope, String text) {
        List<byte[]> values = jdbc.query("SELECT embedding FROM rag_chunks WHERE account_scope=? AND text=? ORDER BY document_id,seq LIMIT 1",
                (rs, row) -> rs.getBytes(1), scope, text);
        return values.isEmpty() ? null : decode(values.get(0));
    }

    @Override
    public void upsert(JdbcTemplate jdbc, String scope, String documentId, int seq, String text,
                       String page, String section, float[] vector) {
        jdbc.update("INSERT INTO rag_chunks(account_scope,document_id,seq,text,page,section,embedding) VALUES (?,?,?,?,?,?,?) "
                + "ON CONFLICT(account_scope,document_id,seq) DO UPDATE SET text=EXCLUDED.text,page=EXCLUDED.page,section=EXCLUDED.section,embedding=EXCLUDED.embedding",
                scope, documentId, seq, text, page, section, encode(vector));
    }

    @Override
    public List<Candidate> nearest(JdbcTemplate jdbc, String scope, List<String> documentIds,
                                   float[] queryVector, int limit) {
        if (documentIds.isEmpty()) return List.of();
        String marks = String.join(",", java.util.Collections.nCopies(documentIds.size(), "?"));
        Object[] args = new Object[documentIds.size() + 1]; args[0] = scope;
        for (int i = 0; i < documentIds.size(); i++) args[i + 1] = documentIds.get(i);
        byte[] query = encode(queryVector);
        List<Candidate> candidates = jdbc.query("SELECT c.document_id,c.seq,c.text,c.page,c.section,c.embedding FROM rag_chunks c "
                        + "JOIN rag_documents d USING (account_scope,document_id) "
                        + "WHERE c.account_scope=? AND d.status='ready' AND c.document_id IN (" + marks + ")",
                (rs, row) -> new Candidate(rs.getString("document_id"), rs.getInt("seq"), rs.getString("text"),
                        rs.getString("page"), rs.getString("section"), cosine(query, rs.getBytes("embedding"))), args);
        candidates.sort(java.util.Comparator.comparingDouble(Candidate::cosine).reversed()
                .thenComparing(Candidate::documentId).thenComparingInt(Candidate::seq));
        return candidates.stream().limit(limit).toList();
    }

    private static byte[] encode(float[] values) {
        ByteBuffer buffer = ByteBuffer.allocate(values.length * Float.BYTES).order(ByteOrder.LITTLE_ENDIAN);
        for (float value : values) buffer.putFloat(value);
        return buffer.array();
    }

    private static float[] decode(byte[] bytes) {
        ByteBuffer buffer = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
        float[] values = new float[bytes.length / Float.BYTES];
        for (int i = 0; i < values.length; i++) values[i] = buffer.getFloat();
        return values;
    }

    private static double cosine(byte[] leftBytes, byte[] rightBytes) {
        float[] left = decode(leftBytes), right = decode(rightBytes);
        if (left.length != right.length) return 0;
        double dot = 0, leftNorm = 0, rightNorm = 0;
        for (int i = 0; i < left.length; i++) {
            dot += (double) left[i] * right[i]; leftNorm += (double) left[i] * left[i];
            rightNorm += (double) right[i] * right[i];
        }
        return leftNorm == 0 || rightNorm == 0 ? 0 : dot / Math.sqrt(leftNorm * rightNorm);
    }

}
