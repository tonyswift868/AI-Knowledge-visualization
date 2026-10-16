package win.clannad.mentorflow.rag;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/** Production pgvector implementation; nearest-neighbor ordering uses the cosine HNSW index. */
@Component
@Profile("!rag154-test")
public final class PgVectorStore implements RagVectorStore {
    @Override
    public float[] findReusable(JdbcTemplate jdbc, String accountScope, String text) {
        List<String> rows = jdbc.query("SELECT embedding::text FROM rag_chunks WHERE account_scope=? AND text=? LIMIT 1",
                (rs, row) -> rs.getString(1), accountScope, text);
        if (rows.isEmpty()) return null;
        String value = rows.get(0).strip();
        if (value.length() < 2 || value.charAt(0) != '[' || value.charAt(value.length() - 1) != ']')
            throw new RagFailure("RAG_VECTOR_INVALID", "Stored embedding vector is malformed", 503);
        String[] parts = value.substring(1, value.length() - 1).split(",");
        if (parts.length != 512) throw new RagFailure("RAG_VECTOR_INVALID", "Stored embedding vector has an invalid dimension", 503);
        float[] vector = new float[parts.length];
        for (int i = 0; i < parts.length; i++) vector[i] = Float.parseFloat(parts[i].strip());
        return vector;
    }

    @Override
    public void upsert(JdbcTemplate jdbc, String accountScope, String documentId, int seq,
            String text, String page, String section, float[] vector) {
        jdbc.update("INSERT INTO rag_chunks(account_scope,document_id,seq,text,page,section,embedding) "
                        + "VALUES (?,?,?,?,?,?,CAST(? AS vector)) "
                        + "ON CONFLICT(account_scope,document_id,seq) DO UPDATE SET text=EXCLUDED.text,"
                        + "page=EXCLUDED.page,section=EXCLUDED.section,embedding=EXCLUDED.embedding",
                accountScope, documentId, seq, text, page, section, literal(vector));
    }

    @Override
    public List<Candidate> nearest(JdbcTemplate jdbc, String accountScope, List<String> documentIds,
            float[] queryVector, int limit) {
        if (documentIds.isEmpty() || limit <= 0) return List.of();
        jdbc.execute("SET LOCAL hnsw.iterative_scan = strict_order");
        int efSearch = Math.max(40, Math.min(1_000, limit));
        jdbc.queryForObject("SELECT set_config('hnsw.ef_search', ?, true)", String.class, Integer.toString(efSearch));
        String placeholders = String.join(",", java.util.Collections.nCopies(documentIds.size(), "?"));
        String sql = "SELECT c.document_id,c.seq,c.text,c.page,c.section,"
                + "(1 - (c.embedding <=> CAST(? AS vector))) AS cosine "
                + "FROM rag_chunks c JOIN rag_documents d ON d.account_scope=c.account_scope AND d.document_id=c.document_id "
                + "WHERE c.account_scope=? AND d.status='ready' AND c.document_id IN (" + placeholders + ") "
                + "ORDER BY c.embedding <=> CAST(? AS vector) LIMIT ?";
        List<Object> arguments = new ArrayList<>(documentIds.size() + 4);
        arguments.add(literal(queryVector)); arguments.add(accountScope); arguments.addAll(documentIds);
        arguments.add(literal(queryVector)); arguments.add(limit);
        return jdbc.query(sql, (rs, row) -> new Candidate(rs.getString("document_id"), rs.getInt("seq"),
                rs.getString("text"), rs.getString("page"), rs.getString("section"), rs.getDouble("cosine")), arguments.toArray());
    }

    private static String literal(float[] vector) {
        if (vector == null || vector.length != 512)
            throw new RagFailure("RAG_VECTOR_INVALID", "Embedding vector must have 512 dimensions", 503);
        StringBuilder result = new StringBuilder("[");
        for (int i = 0; i < vector.length; i++) {
            float value = vector[i];
            if (!Float.isFinite(value)) throw new RagFailure("RAG_VECTOR_INVALID", "Embedding vector contains a non-finite value", 503);
            if (i > 0) result.append(',');
            result.append(String.format(Locale.ROOT, "%.9g", value));
        }
        return result.append(']').toString();
    }
}
