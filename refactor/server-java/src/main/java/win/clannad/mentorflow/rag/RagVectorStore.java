package win.clannad.mentorflow.rag;

import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;

/** Persistence and nearest-neighbor operations for the fixed 512-dimensional RAG embedding. */
public interface RagVectorStore {
    void upsert(JdbcTemplate jdbc, String accountScope, String documentId, int seq,
                String text, String page, String section, float[] vector);

    /** Reuse an already stored vector for identical text within this account, if present. */
    float[] findReusable(JdbcTemplate jdbc, String accountScope, String text);

    List<Candidate> nearest(JdbcTemplate jdbc, String accountScope, List<String> documentIds,
                            float[] queryVector, int limit);

    record Candidate(String documentId, int seq, String text, String page, String section, double cosine) {}
}
