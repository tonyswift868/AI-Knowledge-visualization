package win.clannad.mentorflow.rag;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/** Account-local text chunk ingestion and hybrid retrieval over the configured vector store. */
@Service
public final class RagService {
    private static final String QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章：";
    private static final int MAX_CHUNKS_PER_DOCUMENT = 100_000;
    private static final int MAX_SELECTED_DOCUMENTS = 500;
    private static final int MAX_TOP_K = 100;
    private final RagRepository repository;
    private final RagEmbedding embedding;
    private final RagVectorStore vectorStore;

    public RagService(RagRepository repository, RagEmbedding embedding, RagVectorStore vectorStore) {
        this.repository = repository;
        this.embedding = embedding;
        this.vectorStore = vectorStore;
    }

    /** Accept one zero-based chunk batch. A document becomes searchable only after all chunks arrive. */
    public Map<String, Object> upload(Map<String, Object> actor, String docId, Map<String, Object> body) {
        String scope = accountScope(actor);
        String requestedId = required(docId, "document_id", 160);
        String title = required(body.get("title"), "title", 320);
        String source = required(body.get("source"), "source", 4096);
        int total = positiveInt(body.get("total_chunks"), "total_chunks", MAX_CHUNKS_PER_DOCUMENT);
        List<Map<String, Object>> chunks = chunkBatch(body.get("chunks"), total);
        try {
            return repository.transaction(scope, true, jdbc -> uploadBatch(jdbc, scope, requestedId, title, source, total, chunks));
        } catch (RagFailure failure) {
            throw failure;
        } catch (RuntimeException failure) {
            throw new RagFailure("RAG_EMBEDDING_UNAVAILABLE", "Could not calculate chunk embeddings", 503);
        }
    }

    private Map<String, Object> uploadBatch(JdbcTemplate jdbc, String scope, String requestedId,
            String title, String source, int total, List<Map<String, Object>> chunks) {
        String canonicalId = resolve(jdbc, scope, requestedId);
        boolean aliasRequest = canonicalId != null && !requestedId.equals(canonicalId);
        Map<String, Object> document = findDocument(jdbc, scope, canonicalId);
        if (document != null && "ready".equals(document.get("status"))) {
            boolean samePayload = aliasRequest
                    ? sameAliasBatch(jdbc, scope, requestedId, total, chunks)
                    : sameExistingBatch(jdbc, scope, canonicalId, total, chunks);
            if (!samePayload)
                throw new RagFailure("RAG_DOCUMENT_ID_CONFLICT", "This document ID already contains different content", 409);
            int canonicalTotal = ((Number) document.get("total_chunks")).intValue();
            return uploadResponse(canonicalId, requestedId, "ready", canonicalTotal, canonicalTotal, aliasRequest);
        }
        if (document == null) {
            jdbc.update("INSERT INTO rag_documents(account_scope,document_id,title,source,total_chunks,status) VALUES (?,?,?,?,?,'uploading')",
                    scope, requestedId, title, source, total);
            canonicalId = requestedId;
        } else {
            if (!title.equals(document.get("title")) || !source.equals(document.get("source"))
                    || total != ((Number) document.get("total_chunks")).intValue())
                throw new RagFailure("RAG_DOCUMENT_METADATA_CONFLICT", "Document metadata changed during upload", 409);
        }

        for (Map<String, Object> chunk : chunks) {
            int seq = (Integer) chunk.get("seq");
            String text = (String) chunk.get("text");
            String page = (String) chunk.get("page");
            String section = (String) chunk.get("section");
            List<byte[]> oldRows = jdbc.query("SELECT embedding FROM rag_chunks WHERE account_scope=? AND document_id=? AND seq=? AND text=? AND page IS NOT DISTINCT FROM ? AND section IS NOT DISTINCT FROM ?",
                    (rs, row) -> rs.getBytes(1), scope, canonicalId, seq, text, page, section);
            if (!oldRows.isEmpty()) continue;

            float[] vector = vectorStore.findReusable(jdbc, scope, text);
            if (vector == null) vector = embedding.embed(text);
            require512(vector);
            vectorStore.upsert(jdbc, scope, canonicalId, seq, text, page, section, vector);
        }

        List<Map<String, Object>> stats = jdbc.query("SELECT count(*) AS n,min(seq) AS low,max(seq) AS high FROM rag_chunks WHERE account_scope=? AND document_id=?",
                (rs, row) -> Map.of("n", rs.getLong("n"), "low", rs.getObject("low") == null ? -1 : rs.getInt("low"), "high", rs.getObject("high") == null ? -1 : rs.getInt("high")), scope, canonicalId);
        long received = (Long) stats.get(0).get("n");
        boolean complete = received == total && ((Integer) stats.get(0).get("low")) == 0
                && ((Integer) stats.get(0).get("high")) == total - 1;
        if (!complete) return uploadResponse(canonicalId, requestedId, "uploading", received, total, false);

        String digest = documentDigest(jdbc, scope, canonicalId);
        List<String> duplicates = jdbc.query("SELECT document_id FROM rag_documents WHERE account_scope=? AND content_sha256=? AND status='ready' AND document_id<>?",
                (rs, row) -> rs.getString(1), scope, digest, canonicalId);
        if (!duplicates.isEmpty()) {
            String existingId = duplicates.get(0);
            String requestedChunkDigests = documentChunkDigests(jdbc, scope, canonicalId, total);
            jdbc.update("DELETE FROM rag_documents WHERE account_scope=? AND document_id=?", scope, canonicalId);
            if (!requestedId.equals(existingId)) {
                jdbc.update("INSERT INTO rag_document_aliases(account_scope,requested_document_id,canonical_document_id,total_chunks,chunk_digests) "
                                + "VALUES (?,?,?,?,?)",
                        scope, requestedId, existingId, total, requestedChunkDigests);
            }
            Map<String, Object> existing = findDocument(jdbc, scope, existingId);
            int canonicalTotal = ((Number) existing.get("total_chunks")).intValue();
            return uploadResponse(existingId, requestedId, "ready", canonicalTotal, canonicalTotal, true);
        }
        jdbc.update("UPDATE rag_documents SET content_sha256=?,status='ready',updated_at=CURRENT_TIMESTAMP WHERE account_scope=? AND document_id=?",
                digest, scope, canonicalId);
        return uploadResponse(canonicalId, requestedId, "ready", total, total, false);
    }

    private static boolean sameExistingBatch(JdbcTemplate jdbc, String scope, String id,
            int total, List<Map<String, Object>> chunks) {
        Map<String, Object> doc = findDocument(jdbc, scope, id);
        if (doc == null || total != ((Number) doc.get("total_chunks")).intValue()) return false;
        for (Map<String, Object> chunk : chunks) {
            List<Integer> matches = jdbc.query("SELECT 1 FROM rag_chunks WHERE account_scope=? AND document_id=? AND seq=? AND text=?",
                    (rs, row) -> 1, scope, id, chunk.get("seq"), chunk.get("text"));
            if (matches.isEmpty()) return false;
        }
        return true;
    }

    /** Alias requests retain their own seq/text manifest even when canonical chunking differs. */
    private static boolean sameAliasBatch(JdbcTemplate jdbc, String scope, String aliasId,
            int total, List<Map<String, Object>> chunks) {
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT total_chunks,chunk_digests FROM rag_document_aliases WHERE account_scope=? AND requested_document_id=?",
                (rs, row) -> Map.of("total_chunks", rs.getInt("total_chunks"), "chunk_digests", rs.getString("chunk_digests")),
                scope, aliasId);
        if (rows.isEmpty() || total != ((Number) rows.get(0).get("total_chunks")).intValue()) return false;
        String[] expected = ((String) rows.get(0).get("chunk_digests")).split(",", -1);
        if (expected.length != total) return false;
        for (Map<String, Object> chunk : chunks) {
            int seq = (Integer) chunk.get("seq");
            if (!expected[seq].equals(textDigest((String) chunk.get("text")))) return false;
        }
        return true;
    }

    /** Fixed-width digests, ordered by seq, keep alias replay data small and non-reversible. */
    private static String documentChunkDigests(JdbcTemplate jdbc, String scope, String id, int total) {
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT seq,text FROM rag_chunks WHERE account_scope=? AND document_id=? ORDER BY seq",
                (rs, row) -> Map.of("seq", rs.getInt("seq"), "text", rs.getString("text")), scope, id);
        if (rows.size() != total) throw new IllegalStateException("RAG alias manifest requires a complete document");
        StringBuilder manifest = new StringBuilder(total * 65);
        for (int i = 0; i < rows.size(); i++) {
            if (((Number) rows.get(i).get("seq")).intValue() != i)
                throw new IllegalStateException("RAG alias manifest requires contiguous chunk sequences");
            if (i > 0) manifest.append(',');
            manifest.append(textDigest((String) rows.get(i).get("text")));
        }
        return manifest.toString();
    }

    private static String textDigest(String text) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception error) { throw new IllegalStateException("Could not hash a RAG chunk", error); }
    }

    /** One row per canonical content object; aliases retain IDs issued by clients/plans. */
    public List<Map<String, Object>> documents(Map<String, Object> actor) {
        String scope = accountScope(actor);
        return repository.transaction(scope, false, jdbc -> {
            List<Map<String, Object>> rows = jdbc.query("SELECT d.document_id,d.title,d.source,d.total_chunks,d.status,d.created_at,"
                    + "(SELECT count(*) FROM rag_chunks c WHERE c.account_scope=d.account_scope AND c.document_id=d.document_id) AS received_chunks "
                    + "FROM rag_documents d WHERE d.account_scope=? ORDER BY d.created_at,d.document_id",
                    (rs, row) -> {
                        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
                        result.put("document_id", rs.getString("document_id"));
                        result.put("title", rs.getString("title")); result.put("source", rs.getString("source"));
                        result.put("total_chunks", rs.getInt("total_chunks")); result.put("received_chunks", rs.getLong("received_chunks"));
                        result.put("status", rs.getString("status")); result.put("created_at", rs.getTimestamp("created_at").toInstant().toString());
                        result.put("aliases", new ArrayList<String>());
                        return result;
                    }, scope);
            for (Map<String, Object> row : rows) {
                @SuppressWarnings("unchecked") List<String> aliases = (List<String>) row.get("aliases");
                aliases.addAll(jdbc.query("SELECT requested_document_id FROM rag_document_aliases WHERE account_scope=? AND canonical_document_id=? ORDER BY requested_document_id",
                        (rs, index) -> rs.getString(1), scope, row.get("document_id")));
            }
            return rows;
        });
    }

    /** Resolve requested document IDs, retrieve account-local ready chunks and rank in Java. */
    public Map<String, Object> search(Map<String, Object> actor, Map<String, Object> body) {
        String scope = accountScope(actor);
        String query = required(body.get("query"), "query", 16_000);
        int k = body.containsKey("k") ? positiveInt(body.get("k"), "k", MAX_TOP_K) : 5;
        List<String> requestedIds = stringList(body.get("document_ids"), "document_ids", MAX_SELECTED_DOCUMENTS);
        if (requestedIds.isEmpty()) return Map.of("passages", List.of());
        try {
            float[] queryVector = embedding.embed(QUERY_PREFIX + query.strip());
            require512(queryVector);
            return repository.transaction(scope, false, jdbc -> {
                LinkedHashSet<String> canonicalIds = new LinkedHashSet<>();
                for (String requested : requestedIds) {
                    String resolved = resolve(jdbc, scope, requested);
                    if (resolved != null && findDocument(jdbc, scope, resolved) != null) canonicalIds.add(resolved);
                }
                if (canonicalIds.isEmpty()) return Map.of("passages", List.of());
                List<RagVectorStore.Candidate> ranked = vectorStore.nearest(jdbc, scope,
                        new ArrayList<>(canonicalIds), queryVector, Math.min(10_000, Math.max(k * 10, 50)));
                List<PassageScore> scored = new ArrayList<>(ranked.size());
                for (RagVectorStore.Candidate passage : ranked) {
                    double score = 0.8 * ((passage.cosine() + 1.0) / 2.0) + 0.2 * bigramJaccard(query, passage.text());
                    scored.add(new PassageScore(passage, score));
                }
                scored.sort(Comparator.comparingDouble(PassageScore::score).reversed()
                        .thenComparing(item -> item.passage().documentId()).thenComparingInt(item -> item.passage().seq()));
                List<Map<String, Object>> passages = new ArrayList<>();
                for (PassageScore item : scored.stream().limit(k).toList()) {
                    LinkedHashMap<String, Object> value = new LinkedHashMap<>();
                    value.put("document_id", item.passage().documentId()); value.put("seq", item.passage().seq());
                    value.put("text", item.passage().text()); value.put("page", item.passage().page());
                    value.put("section", item.passage().section()); value.put("score", Math.round(item.score() * 1_000_000d) / 1_000_000d);
                    passages.add(value);
                }
                return Map.of("passages", passages);
            });
        } catch (RagFailure failure) {
            throw failure;
        } catch (RuntimeException failure) {
            throw new RagFailure("RAG_EMBEDDING_UNAVAILABLE", "Could not calculate the search embedding", 503);
        }
    }

    /** Deleting a canonical ID removes the deduplicated content and its aliases. Deleting an alias unlinks only that ID. */
    public Map<String, Object> delete(Map<String, Object> actor, String docId) {
        String scope = accountScope(actor);
        String requested = required(docId, "document_id", 160);
        return repository.transaction(scope, true, jdbc -> {
            List<String> target = jdbc.query("SELECT canonical_document_id FROM rag_document_aliases WHERE account_scope=? AND requested_document_id=?",
                    (rs, row) -> rs.getString(1), scope, requested);
            if (!target.isEmpty()) {
                jdbc.update("DELETE FROM rag_document_aliases WHERE account_scope=? AND requested_document_id=?", scope, requested);
                return Map.of("deleted", true, "content_deleted", false, "document_id", target.get(0));
            }
            List<String> existing = jdbc.query("SELECT document_id FROM rag_documents WHERE account_scope=? AND document_id=?",
                    (rs, row) -> rs.getString(1), scope, requested);
            if (existing.isEmpty()) throw new RagFailure("RAG_DOCUMENT_NOT_FOUND", "Document was not found", 404);
            jdbc.update("DELETE FROM rag_documents WHERE account_scope=? AND document_id=?", scope, requested);
            return Map.of("deleted", true, "content_deleted", true, "document_id", requested);
        });
    }

    private static String resolve(JdbcTemplate jdbc, String scope, String id) {
        List<String> aliases = jdbc.query("SELECT canonical_document_id FROM rag_document_aliases WHERE account_scope=? AND requested_document_id=?",
                (rs, row) -> rs.getString(1), scope, id);
        if (!aliases.isEmpty()) return aliases.get(0);
        List<String> docs = jdbc.query("SELECT document_id FROM rag_documents WHERE account_scope=? AND document_id=?",
                (rs, row) -> rs.getString(1), scope, id);
        return docs.isEmpty() ? null : docs.get(0);
    }

    private static Map<String, Object> findDocument(JdbcTemplate jdbc, String scope, String id) {
        if (id == null) return null;
        List<Map<String, Object>> rows = jdbc.query("SELECT document_id,title,source,total_chunks,status,content_sha256 FROM rag_documents WHERE account_scope=? AND document_id=?",
                (rs, row) -> {
                    LinkedHashMap<String, Object> item = new LinkedHashMap<>();
                    item.put("document_id", rs.getString("document_id")); item.put("title", rs.getString("title"));
                    item.put("source", rs.getString("source")); item.put("total_chunks", rs.getInt("total_chunks"));
                    item.put("status", rs.getString("status")); item.put("content_sha256", rs.getString("content_sha256"));
                    return item;
                }, scope, id);
        return rows.isEmpty() ? null : rows.get(0);
    }

    private static String documentDigest(JdbcTemplate jdbc, String scope, String id) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            jdbc.query("SELECT text FROM rag_chunks WHERE account_scope=? AND document_id=? ORDER BY seq", rs -> {
                byte[] text = rs.getString(1).getBytes(StandardCharsets.UTF_8);
                digest.update(text);
            }, scope, id);
            return HexFormat.of().formatHex(digest.digest());
        } catch (Exception error) { throw new IllegalStateException("Could not hash the document", error); }
    }

    private static Map<String, Object> uploadResponse(String canonical, String requested, String status,
            long received, int total, boolean deduplicated) {
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("document_id", canonical); result.put("requested_document_id", requested);
        result.put("status", status); result.put("received_chunks", received); result.put("total_chunks", total);
        result.put("deduplicated", deduplicated); return result;
    }

    private static List<Map<String, Object>> chunkBatch(Object value, int total) {
        if (!(value instanceof List<?> list) || list.isEmpty() || list.size() > total)
            throw invalid("chunks", "must be a non-empty list no larger than total_chunks");
        List<Map<String, Object>> chunks = new ArrayList<>();
        Set<Integer> seen = new LinkedHashSet<>();
        for (Object item : list) {
            if (!(item instanceof Map<?, ?> raw)) throw invalid("chunks", "each chunk must be an object");
            int seq = positiveOrZeroInt(raw.get("seq"), "seq", total - 1);
            if (!seen.add(seq)) throw invalid("seq", "must not repeat in a batch");
            String text = requiredOriginalText(raw.get("text"), "text", 2_000_000);
            String page = optionalLocation(raw.get("page"), "page");
            String section = optionalLocation(raw.get("section"), "section");
            LinkedHashMap<String, Object> chunk = new LinkedHashMap<>();
            chunk.put("seq", seq); chunk.put("text", text); chunk.put("page", page); chunk.put("section", section);
            chunks.add(chunk);
        }
        chunks.sort(Comparator.comparingInt(chunk -> (Integer) chunk.get("seq")));
        return chunks;
    }

    private static List<String> stringList(Object value, String field, int max) {
        if (!(value instanceof List<?> list) || list.size() > max) throw invalid(field, "must be a list");
        LinkedHashSet<String> result = new LinkedHashSet<>();
        for (Object item : list) result.add(required(item, field, 160));
        return new ArrayList<>(result);
    }

    private static String optionalLocation(Object value, String field) {
        if (value == null) return null;
        if (value instanceof Number number) return number.toString();
        if (value instanceof String text) return text.isBlank() ? null : text.strip();
        throw invalid(field, "must be text or a number");
    }

    private static String accountScope(Map<String, Object> actor) {
        if (actor == null || !"HUMAN".equals(actor.get("actor_kind")))
            throw new RagFailure("RAG_ACCOUNT_REQUIRED", "RAG documents require an authenticated human account", 403);
        String issuer = required(actor.get("issuer"), "issuer", 500);
        String subject = required(actor.get("subject_id"), "subject_id", 300);
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest((issuer + "\u0000" + subject).getBytes(StandardCharsets.UTF_8)));
        } catch (Exception error) { throw new IllegalStateException(error); }
    }

    private static String required(Object value, String field, int max) {
        if (!(value instanceof String text) || text.isBlank() || text.length() > max)
            throw invalid(field, "must be non-empty text no longer than " + max + " characters");
        return text.strip();
    }

    /** Preserve whitespace at both chunk edges because uploaded text is the source used by retrieval. */
    private static String requiredOriginalText(Object value, String field, int max) {
        if (!(value instanceof String text) || text.isBlank() || text.length() > max)
            throw invalid(field, "must be non-empty text no longer than " + max + " characters");
        return text;
    }

    private static int positiveInt(Object value, String field, int max) {
        int parsed = positiveOrZeroInt(value, field, max);
        if (parsed == 0) throw invalid(field, "must be positive");
        return parsed;
    }

    private static int positiveOrZeroInt(Object value, String field, int max) {
        if (!(value instanceof Number number) || number.doubleValue() != number.intValue()) throw invalid(field, "must be an integer");
        int parsed = number.intValue();
        if (parsed < 0 || parsed > max) throw invalid(field, "is outside the allowed range");
        return parsed;
    }

    private static RagFailure invalid(String field, String detail) {
        return new RagFailure("RAG_INVALID_REQUEST", field + " " + detail, 422);
    }

    private static void require512(float[] vector) {
        if (vector == null || vector.length != 512)
            throw new RagFailure("RAG_VECTOR_INVALID", "Embedding vector must have 512 dimensions", 503);
        for (float value : vector)
            if (!Float.isFinite(value)) throw new RagFailure("RAG_VECTOR_INVALID", "Embedding vector contains a non-finite value", 503);
    }

    private static double bigramJaccard(String left, String right) {
        Set<String> a = bigrams(left), b = bigrams(right);
        if (a.isEmpty() || b.isEmpty()) return 0;
        int intersection = 0; for (String item : a) if (b.contains(item)) intersection++;
        return (double) intersection / (a.size() + b.size() - intersection);
    }

    private static Set<String> bigrams(String text) {
        String normalized = Normalizer.normalize(text, Normalizer.Form.NFKC).toLowerCase(Locale.ROOT)
                .replaceAll("[\\p{P}\\p{Z}\\s]+", "");
        LinkedHashSet<String> result = new LinkedHashSet<>();
        if (normalized.length() == 1) result.add(normalized);
        for (int i = 0; i + 1 < normalized.length(); i++) result.add(normalized.substring(i, i + 2));
        return result;
    }

    private record PassageScore(RagVectorStore.Candidate passage, double score) {}
}
