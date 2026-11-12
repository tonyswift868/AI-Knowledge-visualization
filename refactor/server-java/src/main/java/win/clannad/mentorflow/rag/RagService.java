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
        if (Boolean.TRUE.equals(body.get("repair_existing"))) {
            List<String> manifest = repairManifest(body.get("repair_manifest"), total, chunks);
            String contentDigest = required(body.get("repair_content_sha256"), "repair_content_sha256", 64);
            if (!contentDigest.matches("[0-9a-f]{64}"))
                throw invalid("repair_content_sha256", "must be a SHA-256 digest");
            try {
                return repository.transaction(scope, true, jdbc -> repairBatch(
                        jdbc, scope, requestedId, title, source, manifest, contentDigest, chunks));
            } catch (RagFailure failure) {
                throw failure;
            } catch (RuntimeException failure) {
                throw new RagFailure("RAG_EMBEDDING_UNAVAILABLE", "Could not calculate chunk embeddings", 503, failure);
            }
        }
        try {
            return repository.transaction(scope, true, jdbc -> uploadBatch(jdbc, scope, requestedId, title, source, total, chunks));
        } catch (RagFailure failure) {
            throw failure;
        } catch (RuntimeException failure) {
            throw new RagFailure("RAG_EMBEDDING_UNAVAILABLE", "Could not calculate chunk embeddings", 503, failure);
        }
    }

    /** Append only missing content while preserving every previously stored chunk row. */
    private Map<String, Object> repairBatch(JdbcTemplate jdbc, String scope, String requestedId,
            String title, String source, List<String> manifest, String contentDigest,
            List<Map<String, Object>> chunks) {
        String canonicalId = resolve(jdbc, scope, requestedId);
        Map<String, Object> document = findDocument(jdbc, scope, canonicalId);
        if (document == null || !requestedId.equals(canonicalId))
            throw new RagFailure("RAG_DOCUMENT_ID_CONFLICT", "Append repair requires the original document ID", 409);
        if (!"ready".equals(document.get("status")))
            throw new RagFailure("RAG_DOCUMENT_ID_CONFLICT", "Append repair requires an already-ready document", 409);
        if (!title.equals(document.get("title")) || !source.equals(document.get("source")))
            throw new RagFailure("RAG_DOCUMENT_METADATA_CONFLICT", "Document metadata changed during repair", 409);

        String existingContentDigest = documentDigest(jdbc, scope, canonicalId);
        if (contentDigest.equals(existingContentDigest)) {
            long received = jdbc.queryForObject(
                    "SELECT count(*) FROM rag_chunks WHERE account_scope=? AND document_id=?",
                    Long.class, scope, canonicalId);
            int total = ((Number) document.get("total_chunks")).intValue();
            LinkedHashMap<String, Object> result = new LinkedHashMap<>(decorateUpload(jdbc,scope,requestedId,
                    uploadResponse(canonicalId, requestedId, "ready", received, total, false)));
            result.put("repair_complete", true);
            return result;
        }
        List<String> duplicateContent = jdbc.query("SELECT document_id FROM rag_documents WHERE account_scope=? "
                + "AND content_sha256=? AND document_id<>? LIMIT 1",
                (rs, index) -> rs.getString(1), scope, contentDigest, canonicalId);
        if (!duplicateContent.isEmpty())
            throw new RagFailure("RAG_DOCUMENT_REPAIR_CONFLICT",
                    "Complete local content already belongs to another canonical document", 409);

        Map<String, Long> desiredCounts = new LinkedHashMap<>();
        for (String digest : manifest) desiredCounts.merge(digest, 1L, Long::sum);
        List<String> storedDigests = jdbc.query(
                "SELECT text FROM rag_chunks WHERE account_scope=? AND document_id=? ORDER BY seq",
                (rs, index) -> textDigest(rs.getString("text")), scope, canonicalId);
        Map<String, Long> presentCounts = new LinkedHashMap<>();
        for (String digest : storedDigests) presentCounts.merge(digest, 1L, Long::sum);
        boolean existingIsSubset = presentCounts.entrySet().stream()
                .allMatch(entry -> entry.getValue() <= desiredCounts.getOrDefault(entry.getKey(), 0L));
        if (!existingIsSubset)
            throw new RagFailure("RAG_DOCUMENT_REPAIR_CONFLICT",
                    "Existing document chunks are not a subset of the complete local source", 409);
        long missing = desiredCounts.entrySet().stream()
                .mapToLong(entry -> Math.max(0L, entry.getValue() - presentCounts.getOrDefault(entry.getKey(), 0L)))
                .sum();
        Integer next = jdbc.queryForObject(
                "SELECT COALESCE(max(seq),-1)+1 FROM rag_chunks WHERE account_scope=? AND document_id=?",
                Integer.class, scope, canonicalId);
        int nextSeq = next == null ? 0 : next;
        long target = storedDigests.size() + missing;
        if (target > MAX_CHUNKS_PER_DOCUMENT)
            throw invalid("repair_manifest", "would exceed the maximum document chunk count");
        jdbc.update("UPDATE rag_documents SET total_chunks=? WHERE account_scope=? AND document_id=?",
                (int) target, scope, canonicalId);
        jdbc.update("UPDATE rag_source_chunks SET total_chunks=? WHERE account_scope=? AND document_id=? AND seq<?",
                (int) target, scope, canonicalId, (int) target);

        for (Map<String, Object> chunk : chunks) {
            String digest = chunkDigest(chunk);
            long wanted = desiredCounts.getOrDefault(digest, 0L);
            long present = presentCounts.getOrDefault(digest, 0L);
            if (present >= wanted) continue;
            String text = (String) chunk.get("text");
            String page = (String) chunk.get("page");
            String section = (String) chunk.get("section");
            float[] vector = vectorStore.findReusable(jdbc, scope, text);
            if (vector == null) vector = embedding.embed(text);
            require512(vector);
            vectorStore.upsert(jdbc, scope, canonicalId, nextSeq++, text, page, section, vector);
            presentCounts.put(digest, present + 1L);
        }

        boolean complete = desiredCounts.entrySet().stream()
                .allMatch(entry -> presentCounts.getOrDefault(entry.getKey(), 0L) >= entry.getValue());
        long received = jdbc.queryForObject(
                "SELECT count(*) FROM rag_chunks WHERE account_scope=? AND document_id=?",
                Long.class, scope, canonicalId);
        if (complete) {
            String digest = documentDigest(jdbc, scope, canonicalId);
            List<String> conflict = jdbc.query("SELECT document_id FROM rag_documents WHERE account_scope=? "
                    + "AND content_sha256=? AND document_id<>? LIMIT 1",
                    (rs, index) -> rs.getString(1), scope, digest, canonicalId);
            if (!conflict.isEmpty())
                throw new RagFailure("RAG_DOCUMENT_REPAIR_CONFLICT",
                        "Append repair would duplicate another canonical document; no existing document was removed", 409);
            // Append-only repair preserves existing physical byte ranges, including alias layouts.
            jdbc.update("UPDATE rag_documents SET total_chunks=?,content_sha256=?,status='ready',updated_at=CURRENT_TIMESTAMP "
                    + "WHERE account_scope=? AND document_id=?", (int) received, digest, scope, canonicalId);
        }
        LinkedHashMap<String, Object> result = new LinkedHashMap<>(decorateUpload(jdbc,scope,requestedId,
                uploadResponse(canonicalId, requestedId, "ready", received, (int) target, false)));
        result.put("repair_complete", complete);
        return result;
    }

    private static List<String> repairManifest(Object value, int total, List<Map<String, Object>> chunks) {
        if (!(value instanceof List<?> list) || list.size() != total)
            throw invalid("repair_manifest", "must contain one digest per local chunk");
        List<String> manifest = new ArrayList<>(total);
        for (Object item : list) {
            if (!(item instanceof String digest) || !digest.matches("[0-9a-f]{64}"))
                throw invalid("repair_manifest", "contains an invalid digest");
            manifest.add(digest);
        }
        for (Map<String, Object> chunk : chunks) {
            int seq = (Integer) chunk.get("seq");
            if (!manifest.get(seq).equals(chunkDigest(chunk)))
                throw invalid("repair_manifest", "does not match the supplied chunk");
        }
        return manifest;
    }

    private static String chunkDigest(Map<String, Object> chunk) {
        return textDigest((String) chunk.get("text"));
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
            Map<String, Object> promotedManifest = uploadManifest(jdbc, scope, requestedId);
            if (promotedManifest != null) {
                int originalTotal = ((Number) promotedManifest.get("total_chunks")).intValue();
                recordSourceLayout(jdbc,scope,requestedId,canonicalId,originalTotal,chunks);
                return decorateUpload(jdbc,scope,requestedId,uploadResponse(canonicalId, requestedId, "ready", originalTotal, originalTotal, true));
            }
            recordSourceLayout(jdbc,scope,requestedId,canonicalId,aliasRequest?((Number)uploadAliasTotal(jdbc,scope,requestedId)).intValue():canonicalTotal,chunks);
            return decorateUpload(jdbc,scope,requestedId,uploadResponse(canonicalId, requestedId, "ready", canonicalTotal, canonicalTotal, aliasRequest));
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
            storeSourceLayout(jdbc, scope, requestedId, existingId, canonicalId);
            jdbc.update("DELETE FROM rag_documents WHERE account_scope=? AND document_id=?", scope, canonicalId);
            if (!requestedId.equals(existingId)) {
                jdbc.update("INSERT INTO rag_document_aliases(account_scope,requested_document_id,canonical_document_id,total_chunks,chunk_digests) "
                                + "VALUES (?,?,?,?,?)",
                        scope, requestedId, existingId, total, requestedChunkDigests);
            }
            Map<String, Object> existing = findDocument(jdbc, scope, existingId);
            int canonicalTotal = ((Number) existing.get("total_chunks")).intValue();
            return decorateUpload(jdbc,scope,requestedId,uploadResponse(existingId, requestedId, "ready", canonicalTotal, canonicalTotal, true));
        }
        jdbc.update("UPDATE rag_documents SET content_sha256=?,status='ready',updated_at=CURRENT_TIMESTAMP WHERE account_scope=? AND document_id=?",
                digest, scope, canonicalId);
        storeSourceLayout(jdbc, scope, requestedId, canonicalId, canonicalId);
        return decorateUpload(jdbc,scope,requestedId,uploadResponse(canonicalId, requestedId, "ready", total, total, false));
    }

    private static void storeSourceLayout(JdbcTemplate jdbc,String scope,String logicalId,String canonicalId,String sourceDocumentId){
        String canonicalDigest=documentDigest(jdbc,scope,canonicalId);
        if(!canonicalDigest.equals(documentDigest(jdbc,scope,sourceDocumentId)))
            throw new RagFailure("RAG_SOURCE_LAYOUT_CONFLICT","Source chunks do not match canonical content",409);
        jdbc.update("WITH source AS (SELECT seq,text,page,section,octet_length(convert_to(text,'UTF8')) byte_length, "
                        +"count(*) OVER() total_chunks,COALESCE(sum(octet_length(convert_to(text,'UTF8'))) OVER (ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0) start_byte "
                        +"FROM rag_chunks WHERE account_scope=? AND document_id=?) INSERT INTO rag_source_chunks "
                        +"(account_scope,document_id,canonical_document_id,seq,total_chunks,text_sha256,byte_length,start_byte,end_byte,page,section) "
                        +"SELECT ?,?,?,seq,total_chunks,encode(sha256(convert_to(text,'UTF8')),'hex'),byte_length,start_byte,start_byte+byte_length,page,section FROM source "
                        +"ON CONFLICT(account_scope,document_id,seq) DO NOTHING",scope,sourceDocumentId,scope,logicalId,canonicalId);
    }

    private static boolean sameExistingBatch(JdbcTemplate jdbc, String scope, String id,
            int total, List<Map<String, Object>> chunks) {
        Map<String, Object> doc = findDocument(jdbc, scope, id);
        if (doc == null) return false;
        Map<String, Object> manifest = uploadManifest(jdbc, scope, id);
        if (manifest != null) return sameAliasBatch(manifest, total, chunks);
        if (total != ((Number) doc.get("total_chunks")).intValue()) return false;
        for (Map<String, Object> chunk : chunks) {
            List<Integer> matches = jdbc.query("SELECT 1 FROM rag_chunks WHERE account_scope=? AND document_id=? AND seq=? AND text=?",
                    (rs, row) -> 1, scope, id, chunk.get("seq"), chunk.get("text"));
            if (matches.isEmpty()) return false;
        }
        return true;
    }

    private static Map<String, Object> uploadManifest(JdbcTemplate jdbc, String scope, String id) {
        List<Map<String, Object>> manifests = jdbc.query(
                "SELECT total_chunks,chunk_digests FROM rag_document_upload_manifests "
                        + "WHERE account_scope=? AND document_id=?",
                (rs, row) -> Map.of("total_chunks", rs.getInt("total_chunks"),
                        "chunk_digests", rs.getString("chunk_digests")), scope, id);
        return manifests.isEmpty() ? null : manifests.get(0);
    }

    /** Alias requests retain their own seq/text manifest even when canonical chunking differs. */
    private static boolean sameAliasBatch(JdbcTemplate jdbc, String scope, String aliasId,
            int total, List<Map<String, Object>> chunks) {
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT total_chunks,chunk_digests FROM rag_document_aliases WHERE account_scope=? AND requested_document_id=?",
                (rs, row) -> Map.of("total_chunks", rs.getInt("total_chunks"), "chunk_digests", rs.getString("chunk_digests")),
                scope, aliasId);
        return !rows.isEmpty() && sameAliasBatch(rows.get(0), total, chunks);
    }

    private static boolean sameAliasBatch(Map<String, Object> manifest, int total,
            List<Map<String, Object>> chunks) {
        if (total != ((Number) manifest.get("total_chunks")).intValue()) return false;
        String[] expected = ((String) manifest.get("chunk_digests")).split(",", -1);
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

    /** Store only verified source metadata; source text remains in the shared canonical chunks. */
    private Map<String, Object> recordSourceLayout(JdbcTemplate jdbc,String scope,String logicalId,String canonicalId,int total,List<Map<String,Object>> chunks) {
            Map<String, Object> document = findDocument(jdbc, scope, canonicalId);
            if (document == null || !"ready".equals(document.get("status")))
                throw new RagFailure("RAG_DOCUMENT_NOT_FOUND", "Ready document was not found", 404);
            String manifest = sourceManifest(jdbc, scope, logicalId, canonicalId, total);
            String[] hashes = manifest.split(",", -1);
            if (hashes.length != total)
                throw new RagFailure("RAG_SOURCE_LAYOUT_CONFLICT", "Source manifest is incomplete", 409);
            List<int[]> totals=jdbc.query("SELECT min(total_chunks),max(total_chunks) FROM rag_source_chunks WHERE account_scope=? AND document_id=?",
                    (rs,row)->new int[]{rs.getInt(1),rs.getInt(2)},scope,logicalId);
            if(totals.get(0)[0]!=0&&(totals.get(0)[0]!=total||totals.get(0)[1]!=total))
                throw new RagFailure("RAG_SOURCE_LAYOUT_CONFLICT", "Source layout chunk count changed between batches", 409);
            for (Map<String, Object> chunk : chunks) {
                int seq = (Integer) chunk.get("seq");
                if (!hashes[seq].equals(chunkDigest(chunk)))
                    throw new RagFailure("RAG_SOURCE_LAYOUT_CONFLICT", "Source chunk does not match its saved manifest", 409);
            }
            List<Map<String, Object>> old = jdbc.query("SELECT seq,total_chunks,text_sha256,byte_length,start_byte,end_byte,page,section "
                            + "FROM rag_source_chunks WHERE account_scope=? AND document_id=? ORDER BY seq",
                    (rs, row) -> {
                        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
                        value.put("seq", rs.getInt("seq")); value.put("total_chunks", rs.getInt("total_chunks"));
                        value.put("text_sha256", rs.getString("text_sha256")); value.put("byte_length", rs.getLong("byte_length"));
                        value.put("start_byte", rs.getObject("start_byte")); value.put("end_byte", rs.getObject("end_byte"));
                        value.put("page", rs.getString("page")); value.put("section", rs.getString("section")); return value;
                    }, scope, logicalId);
            if (!old.isEmpty() && old.size() == total && old.stream().allMatch(row -> row.get("start_byte") != null)) {
                if (!sameSourceLayout(old, chunks, total))
                    throw new RagFailure("RAG_SOURCE_LAYOUT_CONFLICT", "Known source layout cannot be replaced", 409);
                return layoutResponse(jdbc, scope, logicalId, canonicalId, total);
            }
            for (Map<String, Object> chunk : chunks) {
                int seq = (Integer) chunk.get("seq"); String text = (String) chunk.get("text");
                String digest = textDigest(text); long bytes = text.getBytes(StandardCharsets.UTF_8).length;
                List<Map<String, Object>> existing = jdbc.query("SELECT text_sha256,byte_length,page,section,total_chunks,canonical_document_id FROM rag_source_chunks "
                                + "WHERE account_scope=? AND document_id=? AND seq=?",
                        (rs, row) -> Map.of("text_sha256", rs.getString(1), "byte_length", rs.getLong(2),
                                "page", rs.getString(3) == null ? "" : rs.getString(3), "section", rs.getString(4) == null ? "" : rs.getString(4),
                                "total_chunks",rs.getInt(5),"canonical_document_id",rs.getString(6)),
                        scope, logicalId, seq);
                if (!existing.isEmpty()) {
                    Map<String, Object> row = existing.get(0);
                    if (!digest.equals(row.get("text_sha256")) || bytes != ((Number) row.get("byte_length")).longValue()
                            || total!=((Number)row.get("total_chunks")).intValue()||!canonicalId.equals(row.get("canonical_document_id"))
                            || !java.util.Objects.equals(chunk.get("page") == null ? "" : chunk.get("page"), row.get("page"))
                            || !java.util.Objects.equals(chunk.get("section") == null ? "" : chunk.get("section"), row.get("section")))
                        throw new RagFailure("RAG_SOURCE_LAYOUT_CONFLICT", "Source layout metadata conflicts with an earlier batch", 409);
                    continue;
                }
                jdbc.update("INSERT INTO rag_source_chunks(account_scope,document_id,canonical_document_id,seq,total_chunks,text_sha256,byte_length,page,section) "
                        + "VALUES (?,?,?,?,?,?,?,?,?)", scope, logicalId, canonicalId, seq, total, digest, bytes,
                        chunk.get("page"), chunk.get("section"));
            }
            List<Map<String, Object>> saved = jdbc.query("SELECT seq,text_sha256,byte_length FROM rag_source_chunks "
                            + "WHERE account_scope=? AND document_id=? ORDER BY seq",
                    (rs, row) -> Map.of("seq", rs.getInt(1), "text_sha256", rs.getString(2), "byte_length", rs.getLong(3)),
                    scope, logicalId);
            if (saved.size() == total && saved.stream().allMatch(row -> ((Number) row.get("seq")).intValue() >= 0)) {
                for(int i=0;i<total;i++) if(((Number)saved.get(i).get("seq")).intValue()!=i)
                    throw new RagFailure("RAG_SOURCE_LAYOUT_CONFLICT", "Source sequence is incomplete", 409);
                Map<Integer, long[]> ranges = verifySavedLayoutRanges(jdbc, scope, canonicalId, logicalId, saved, total);
                for (int i = 0; i < total; i++) {
                    long[] range = ranges.get(i);
                    if (range == null) throw new RagFailure("RAG_SOURCE_LAYOUT_CONFLICT", "Source chunks do not map to canonical text", 409);
                    jdbc.update("UPDATE rag_source_chunks SET start_byte=?,end_byte=? WHERE account_scope=? AND document_id=? AND seq=?",
                            range[0], range[1], scope, logicalId, i);
                }
            }
            return layoutResponse(jdbc, scope, logicalId, canonicalId, total);
    }

    private static String sourceManifest(JdbcTemplate jdbc, String scope, String logicalId, String canonicalId, int total) {
        List<String> aliases = jdbc.query("SELECT total_chunks,chunk_digests FROM rag_document_aliases WHERE account_scope=? AND requested_document_id=?",
                (rs, row) -> rs.getInt(1) == total ? rs.getString(2) : null, scope, logicalId);
        if (!aliases.isEmpty() && aliases.get(0) != null) return aliases.get(0);
        Map<String, Object> upload = uploadManifest(jdbc, scope, logicalId);
        if (upload != null && ((Number) upload.get("total_chunks")).intValue() == total)
            return (String) upload.get("chunk_digests");
        List<String> saved=jdbc.query("SELECT seq,text_sha256 FROM rag_source_chunks WHERE account_scope=? AND document_id=? ORDER BY seq",
                (rs,row)->rs.getInt(1)+":"+rs.getString(2),scope,logicalId);
        if(saved.size()==total&&saved.stream().allMatch(item->item.matches("\\d+:[0-9a-f]{64}"))){
            StringBuilder manifest=new StringBuilder();for(int i=0;i<total;i++){String[] pair=saved.get(i).split(":",2);if(Integer.parseInt(pair[0])!=i)return "";if(i>0)manifest.append(',');manifest.append(pair[1]);}return manifest.toString();
        }
        if (!logicalId.equals(canonicalId))
            throw new RagFailure("RAG_SOURCE_LAYOUT_CONFLICT", "No source manifest is available", 409);
        return documentChunkDigests(jdbc, scope, canonicalId, total);
    }

    private static boolean sameSourceLayout(List<Map<String, Object>> old, List<Map<String, Object>> chunks, int total) {
        if (old.size() != total) return false;
        for (Map<String, Object> chunk : chunks) {
            int seq = (Integer) chunk.get("seq"); Map<String, Object> row = old.get(seq);
            if (!textDigest((String) chunk.get("text")).equals(row.get("text_sha256"))
                    || !java.util.Objects.equals(chunk.get("page"), row.get("page"))
                    || !java.util.Objects.equals(chunk.get("section"), row.get("section"))) return false;
        }
        return true;
    }

    private static Map<Integer,long[]> verifySavedLayoutRanges(JdbcTemplate jdbc,String scope,String canonicalId,String logicalId,
            List<Map<String,Object>> saved,int total){
        List<Map<String,Object>> direct=jdbc.query("WITH physical AS (SELECT seq,text,octet_length(convert_to(text,'UTF8')) len, "
                        +"COALESCE(sum(octet_length(convert_to(text,'UTF8'))) OVER (ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0) pstart FROM rag_chunks WHERE account_scope=? AND document_id=?), "
                        +"layout AS (SELECT seq,byte_length,text_sha256,COALESCE(sum(byte_length) OVER (ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0) start_byte FROM rag_source_chunks WHERE account_scope=? AND document_id=?), "
                        +"totals AS (SELECT (SELECT COALESCE(sum(len),0) FROM physical) pbytes,(SELECT COALESCE(sum(byte_length),0) FROM layout) sbytes), "
                        +"pieces AS (SELECT l.seq,l.start_byte,l.byte_length,l.text_sha256,string_agg(substring(convert_to(p.text,'UTF8') FROM GREATEST(0,l.start_byte-p.pstart)::int+1 "
                        +"FOR (LEAST(l.start_byte+l.byte_length,p.pstart+p.len)-GREATEST(l.start_byte,p.pstart))::int), ''::bytea ORDER BY p.seq) content "
                        +"FROM layout l JOIN physical p ON p.pstart<l.start_byte+l.byte_length AND p.pstart+p.len>l.start_byte GROUP BY l.seq,l.start_byte,l.byte_length,l.text_sha256) "
                        +"SELECT x.seq,x.start_byte,x.start_byte+x.byte_length end_byte FROM pieces x CROSS JOIN totals t "
                        +"WHERE t.sbytes<=t.pbytes AND octet_length(x.content)=x.byte_length AND encode(sha256(x.content),'hex')=x.text_sha256 ORDER BY x.seq",
                (rs,row)->Map.of("seq",rs.getInt(1),"start",rs.getLong(2),"end",rs.getLong(3)),scope,canonicalId,scope,logicalId);
        if(direct.size()==total){Map<Integer,long[]> ranges=new LinkedHashMap<>();for(Map<String,Object> row:direct)ranges.put(((Number)row.get("seq")).intValue(),new long[]{((Number)row.get("start")).longValue(),((Number)row.get("end")).longValue()});return ranges;}
        List<Map<String,Object>> physical=jdbc.query("SELECT seq,encode(sha256(convert_to(text,'UTF8')),'hex') digest,octet_length(convert_to(text,'UTF8')) byte_length, "
                        +"COALESCE(sum(octet_length(convert_to(text,'UTF8'))) OVER (ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0) start_byte "
                        +"FROM rag_chunks WHERE account_scope=? AND document_id=? ORDER BY seq",
                (rs,row)->Map.of("seq",rs.getInt(1),"digest",rs.getString(2),"byte_length",rs.getLong(3),"start",rs.getLong(4)),scope,canonicalId);
        if(physical.size()!=total) throw new RagFailure("RAG_SOURCE_LAYOUT_CONFLICT", "Source layout cannot be mapped to canonical text", 409);
        Map<String,Map<String,Object>> byHash=new LinkedHashMap<>();Set<String> repeated=new LinkedHashSet<>();
        for(Map<String,Object> row:physical)if(byHash.putIfAbsent((String)row.get("digest"),row)!=null)repeated.add((String)row.get("digest"));
        if(!repeated.isEmpty()) throw new RagFailure("RAG_SOURCE_LAYOUT_CONFLICT", "Repeated physical chunks make source order ambiguous", 409);
        Map<Integer,long[]> ranges=new LinkedHashMap<>();
        for(Map<String,Object> row:saved){Map<String,Object> target=byHash.get(row.get("text_sha256"));if(target==null||((Number)target.get("byte_length")).longValue()!=((Number)row.get("byte_length")).longValue())throw new RagFailure("RAG_SOURCE_LAYOUT_CONFLICT", "Source layout cannot be mapped to canonical chunks", 409);int seq=((Number)row.get("seq")).intValue();long start=((Number)target.get("start")).longValue();ranges.put(seq,new long[]{start,start+((Number)row.get("byte_length")).longValue()});}
        return ranges;
    }

    private static Map<String,Object> layoutResponse(JdbcTemplate jdbc,String scope,String logical,String canonical,int total){
        List<Map<String,Object>> rows=jdbc.query("SELECT count(*) n,count(start_byte) located FROM rag_source_chunks WHERE account_scope=? AND document_id=?",
                (rs,row)->Map.of("n",rs.getInt("n"),"located",rs.getInt("located")),scope,logical);
        int received=((Number)rows.get(0).get("n")).intValue(),located=((Number)rows.get(0).get("located")).intValue();
        LinkedHashMap<String,Object> result=new LinkedHashMap<>();result.put("document_id",canonical);result.put("requested_document_id",logical);
        result.put("source_layout_status",received==0?"unknown":received<total?"incomplete":located==total?"known":"incomplete");
        result.put("received_chunks",received);result.put("total_chunks",total);return result;
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
                Map<String, Object> promotedManifest = uploadManifest(jdbc, scope, (String) row.get("document_id"));
                if (promotedManifest != null && "ready".equals(row.get("status"))) {
                    int logicalTotal = ((Number) promotedManifest.get("total_chunks")).intValue();
                    row.put("total_chunks", logicalTotal);
                    row.put("received_chunks", (long) logicalTotal);
                }
                List<Map<String,Object>> layouts=new ArrayList<>();
                layouts.add(layoutInfo(jdbc,scope,(String)row.get("document_id"),((Number)row.get("total_chunks")).intValue()));
                for(String alias:aliases){
                    Integer aliasTotal=jdbc.queryForObject("SELECT total_chunks FROM rag_document_aliases WHERE account_scope=? AND requested_document_id=?",Integer.class,scope,alias);
                    layouts.add(layoutInfo(jdbc,scope,alias,aliasTotal==null?0:aliasTotal));
                }
                row.put("source_layouts",layouts);
            }
            return rows;
        });
    }

    /** Resolve requested document IDs, retrieve account-local ready chunks and rank in Java. */
    public Map<String, Object> search(Map<String, Object> actor, Map<String, Object> body) {
        String scope = accountScope(actor);
        String query = required(body.get("query"), "query", 16_000);
        int k = body.containsKey("k") ? positiveInt(body.get("k"), "k", MAX_TOP_K) : 5;
        Object sourceLayoutFlag=body.get("source_layout_v1");
        if(body.containsKey("source_layout_v1")&&!(sourceLayoutFlag instanceof Boolean))throw invalid("source_layout_v1","must be a boolean");
        final boolean sourceLayoutMode=Boolean.TRUE.equals(sourceLayoutFlag);
        List<String> requestedIds = stringList(body.get("document_ids"), "document_ids", MAX_SELECTED_DOCUMENTS);
        if (requestedIds.isEmpty()) return searchResponse(List.of(), List.of());
        try {
            float[] queryVector = embedding.embed(QUERY_PREFIX + query.strip());
            require512(queryVector);
            return repository.transaction(scope, false, jdbc -> {
                LinkedHashSet<String> canonicalIds = new LinkedHashSet<>();
                Map<String, List<String>> requestedByCanonical = new LinkedHashMap<>();
                Map<String, String> contentDigests = new LinkedHashMap<>();
                for (String requested : requestedIds) {
                    String resolved = resolve(jdbc, scope, requested);
                    Map<String,Object> document=resolved==null?null:findDocument(jdbc,scope,resolved);
                    if (document != null) {
                        canonicalIds.add(resolved);
                        contentDigests.put(resolved,(String)document.get("content_sha256"));
                        requestedByCanonical.computeIfAbsent(resolved, ignored -> new ArrayList<>()).add(requested);
                    }
                }
                if (canonicalIds.isEmpty()) return searchResponse(List.of(), List.of());
                List<RagVectorStore.Candidate> ranked = vectorStore.nearest(jdbc, scope,
                        new ArrayList<>(canonicalIds), queryVector, Math.min(10_000, Math.max(k * 10, 50)));
                Comparator<LocatedPassage> order=Comparator.comparingDouble(LocatedPassage::score).reversed()
                        .thenComparing(LocatedPassage::documentId).thenComparing(LocatedPassage::seq,Comparator.nullsLast(Comparator.naturalOrder()));
                Map<String,LocatedPassage> best=new LinkedHashMap<>();
                Map<String,String> sourceStates = new LinkedHashMap<>();
                Map<String,CanonicalRanges> physicalLayouts = new LinkedHashMap<>();
                List<Map<String,Object>> warnings=new ArrayList<>();
                Set<String> warned=new LinkedHashSet<>();
                for (RagVectorStore.Candidate passage : ranked) {
                    double score = 0.8 * ((passage.cosine() + 1.0) / 2.0) + 0.2 * bigramJaccard(query, passage.text());
                    List<String> logicalIds = requestedByCanonical.getOrDefault(passage.documentId(), List.of());
                    CanonicalRanges physical=physicalLayouts.computeIfAbsent(passage.documentId(),id->canonicalRanges(jdbc,scope,id));
                    long[] candidateRange = physical.bySeq().getOrDefault(passage.seq(),new long[]{0,0});
                    String hitDigest=textDigest(passage.text());
                    for(String logicalId:logicalIds) {
                        String state=sourceStates.computeIfAbsent(logicalId,id->sourceStatus(jdbc,scope,id));
                        List<LocatedPassage> located=sourcePassages(jdbc,scope,logicalId,passage,sourceLayoutMode,
                                "known".equals(state),candidateRange,k,hitDigest,contentDigests.get(passage.documentId()));
                        for(LocatedPassage item:located){
                            String key=item.documentId()+"\u0000"+(item.seq()==null?hitDigest:item.seq());
                            LocatedPassage old=best.get(key);
                            if(old==null||score>old.score())best.put(key,item.withScore(score));
                            if(best.size()>k){
                                var worst=best.entrySet().stream().max((a,b)->order.compare(a.getValue(),b.getValue())).orElseThrow();
                                best.remove(worst.getKey());
                            }
                        }
                        if(located.isEmpty()&&!"known".equals(state)&&warned.add(logicalId)){
                            LinkedHashMap<String,Object> warning=new LinkedHashMap<>();warning.put("document_id",logicalId);
                            warning.put("code","RAG_SOURCE_LAYOUT_UNKNOWN");warning.put("message","来源位置未知，需同步本地块元数据");warnings.add(warning);
                        }
                    }
                }
                List<LocatedPassage> scored=new ArrayList<>(best.values());
                scored.sort(order);
                List<Map<String, Object>> passages = new ArrayList<>();
                Map<String,String> textCache=new LinkedHashMap<>();
                for (LocatedPassage item : scored.stream().limit(k).toList()) {
                    LinkedHashMap<String, Object> value = new LinkedHashMap<>();
                    value.put("document_id", item.documentId()); value.put("seq", item.seq());
                    String cacheKey=item.canonicalId()+":"+item.startByte()+":"+item.endByte();
                    String passageText=item.text()==null?textCache.computeIfAbsent(cacheKey,ignored->rangeText(jdbc,scope,item.canonicalId(),item.startByte(),item.endByte(),physicalLayouts.get(item.canonicalId()))):item.text();
                    value.put("text", passageText); value.put("page", item.page());
                    value.put("section", item.section()); value.put("location_status",item.locationStatus());
                    value.put("source_layout_status",item.locationStatus());
                    value.put("score", Math.round(item.score() * 1_000_000d) / 1_000_000d);
                    passages.add(value);
                }
                return searchResponse(passages,warnings);
            });
        } catch (RagFailure failure) {
            throw failure;
        } catch (RuntimeException failure) {
            throw new RagFailure("RAG_EMBEDDING_UNAVAILABLE", "Could not calculate the search embedding", 503, failure);
        }
    }

    private static Map<String,Object> searchResponse(List<Map<String,Object>> passages,List<Map<String,Object>> warnings){
        LinkedHashMap<String,Object> response=new LinkedHashMap<>();response.put("passages",passages);response.put("source_warnings",warnings);return response;
    }

    private static List<LocatedPassage> sourcePassages(JdbcTemplate jdbc,String scope,String logicalId,RagVectorStore.Candidate hit,
            boolean sourceLayoutMode,boolean known,long[] candidate,int k,String hitDigest,String contentDigest){
        String condition=known?" AND start_byte<? AND end_byte>?":" AND text_sha256=?";
        Object[] parameters=known?new Object[]{scope,logicalId,hit.documentId(),candidate[1],candidate[0],k}
                :new Object[]{scope,logicalId,hit.documentId(),hitDigest,1};
        List<Map<String,Object>> layout=jdbc.query("SELECT seq,total_chunks,start_byte,end_byte,page,section,text_sha256 FROM rag_source_chunks "
                        +"WHERE account_scope=? AND document_id=? AND canonical_document_id=?"+condition+" ORDER BY seq LIMIT ?",
                (rs,row)->{LinkedHashMap<String,Object> value=new LinkedHashMap<>();value.put("seq",rs.getInt("seq"));value.put("total",rs.getInt("total_chunks"));value.put("start",rs.getObject("start_byte"));value.put("end",rs.getObject("end_byte"));value.put("page",rs.getString("page"));value.put("section",rs.getString("section"));value.put("hash",rs.getString("text_sha256"));return value;},parameters);
        // A candidate gives every mapped chunk the same score; only its first k seqs can enter global top k.
        if(known){
            List<LocatedPassage> out=new ArrayList<>();
            for(Map<String,Object> row:layout){long start=((Number)row.get("start")).longValue(),end=((Number)row.get("end")).longValue();if(start<candidate[1]&&end>candidate[0]){
                out.add(new LocatedPassage(logicalId,((Number)row.get("seq")).intValue(),null,(String)row.get("page"),(String)row.get("section"),"known",0,hit.documentId(),start,end));
            }}return out;
        }
        // Old aliases have hashes but no recoverable pages/sections. Return only an exact proven source chunk.
        String digest=hitDigest;List<String> manifests=jdbc.query("SELECT chunk_digests FROM rag_document_aliases WHERE account_scope=? AND requested_document_id=? UNION ALL SELECT chunk_digests FROM rag_document_upload_manifests WHERE account_scope=? AND document_id=?",
                (rs,row)->rs.getString(1),scope,logicalId,scope,logicalId);
        if(logicalId.equals(hit.documentId())&&manifests.isEmpty()&&layout.isEmpty()){
            if(sourceLayoutMode)return List.of(new LocatedPassage(logicalId,null,hit.text(),null,null,"missing",0,null,0,0));
            return List.of(new LocatedPassage(logicalId,hit.seq(),hit.text(),hit.page(),hit.section(),"unknown",0,null,0,0));
        }
        if(logicalId.equals(hit.documentId())&&manifests.isEmpty()&&!layout.isEmpty()){
            for(Map<String,Object> row:layout)if(digest.equals(row.get("hash")))return List.of(sourceLayoutMode
                    ?new LocatedPassage(logicalId,null,hit.text(),null,null,"missing",0,null,0,0)
                    :new LocatedPassage(logicalId,((Number)row.get("seq")).intValue(),hit.text(),null,"来源位置未知","unknown",0,null,0,0));
        }
        if(!manifests.isEmpty()){String[] hashes=manifests.get(0).split(",",-1);for(int i=0;i<hashes.length;i++)if(hashes[i].equals(digest))return List.of(sourceLayoutMode
                ?new LocatedPassage(logicalId,null,hit.text(),null,null,"missing",0,null,0,0)
                :new LocatedPassage(logicalId,i,hit.text(),null,"来源位置未知","unknown",0,null,0,0));
            if(sourceLayoutMode&&hashes.length==1&&hashes[0].equals(contentDigest))
                return List.of(new LocatedPassage(logicalId,null,hit.text(),null,null,"missing",0,null,0,0));
        }
        if(sourceLayoutMode&&layout.stream().anyMatch(row->digest.equals(row.get("hash"))))
            return List.of(new LocatedPassage(logicalId,null,hit.text(),null,null,"missing",0,null,0,0));
        if(sourceLayoutMode&&!logicalId.equals(hit.documentId()))return List.of();
        if(!sourceLayoutMode)return List.of(new LocatedPassage(logicalId,hit.seq(),hit.text(),hit.page(),hit.section(),"unknown",0,null,0,0));
        return List.of(new LocatedPassage(logicalId,null,hit.text(),null,null,"missing",0,null,0,0));
    }

    /** Read lengths once per canonical search result, without transferring document text. */
    private static CanonicalRanges canonicalRanges(JdbcTemplate jdbc,String scope,String canonical){
        List<long[]> lengths=jdbc.query("SELECT seq,octet_length(text) FROM rag_chunks WHERE account_scope=? AND document_id=? ORDER BY seq",
                (rs,row)->new long[]{rs.getInt(1),rs.getLong(2)},scope,canonical);
        Map<Integer,long[]> bySeq=new LinkedHashMap<>();
        java.util.NavigableMap<Long,Integer> byStart=new java.util.TreeMap<>();
        long start=0;
        for(long[] chunk:lengths){int seq=(int)chunk[0];bySeq.put(seq,new long[]{start,start+chunk[1]});byStart.put(start,seq);start+=chunk[1];}
        return new CanonicalRanges(bySeq,byStart);
    }

    /** Select only byte slices intersecting one logical source chunk; never materialize the whole document. */
    private static String rangeText(JdbcTemplate jdbc,String scope,String canonical,long start,long end,CanonicalRanges ranges){
        var first=ranges.byStart().floorEntry(start);
        var last=ranges.byStart().lowerEntry(end);
        if(first==null||last==null||end>ranges.bySeq().get(last.getValue())[1])
            throw new IllegalStateException("Source range is outside canonical text");
        List<byte[]> parts=jdbc.query("WITH p AS (SELECT seq,text,octet_length(text) len, "
                        +"?::bigint+COALESCE(sum(octet_length(text)) OVER (ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0) pstart "
                        +"FROM rag_chunks WHERE account_scope=? AND document_id=? AND seq>=? AND seq<=?) SELECT substring(convert_to(text,'UTF8') FROM GREATEST(0,?-pstart)::int+1 "
                        +"FOR (LEAST(?,pstart+len)-GREATEST(?,pstart))::int) FROM p WHERE pstart<? AND pstart+len>? ORDER BY seq",
                (rs,row)->rs.getBytes(1),first.getKey(),scope,canonical,first.getValue(),last.getValue(),start,end,start,end,start);
        java.io.ByteArrayOutputStream bytes=new java.io.ByteArrayOutputStream();for(byte[] part:parts)bytes.writeBytes(part);
        return bytes.toString(StandardCharsets.UTF_8);
    }

    /** Delete one logical document ID while keeping content shared by its remaining aliases. */
    public Map<String, Object> delete(Map<String, Object> actor, String docId) {
        String scope = accountScope(actor);
        String requested = required(docId, "document_id", 160);
        return repository.transaction(scope, true, jdbc -> {
            List<String> target = jdbc.query("SELECT canonical_document_id FROM rag_document_aliases WHERE account_scope=? AND requested_document_id=?",
                    (rs, row) -> rs.getString(1), scope, requested);
            if (!target.isEmpty()) {
                jdbc.update("DELETE FROM rag_source_chunks WHERE account_scope=? AND document_id=?", scope, requested);
                jdbc.update("DELETE FROM rag_document_aliases WHERE account_scope=? AND requested_document_id=?", scope, requested);
                return deleteResponse(false, target.get(0));
            }
            List<Map<String, Object>> existing = jdbc.query("SELECT document_id,title,source,total_chunks,status,content_sha256,created_at,updated_at "
                            + "FROM rag_documents WHERE account_scope=? AND document_id=? FOR UPDATE",
                    (rs, row) -> {
                        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
                        value.put("document_id", rs.getString("document_id"));
                        value.put("title", rs.getString("title")); value.put("source", rs.getString("source"));
                        value.put("total_chunks", rs.getInt("total_chunks")); value.put("status", rs.getString("status"));
                        value.put("content_sha256", rs.getString("content_sha256"));
                        value.put("created_at", rs.getTimestamp("created_at")); value.put("updated_at", rs.getTimestamp("updated_at"));
                        return value;
                    }, scope, requested);
            if (existing.isEmpty()) throw new RagFailure("RAG_DOCUMENT_NOT_FOUND", "Document was not found", 404);

            // A stable order makes promotion deterministic when a canonical document has several aliases.
            List<Map<String, Object>> aliases = jdbc.query("SELECT requested_document_id,total_chunks,chunk_digests FROM rag_document_aliases "
                            + "WHERE account_scope=? AND canonical_document_id=? ORDER BY requested_document_id",
                    (rs, row) -> Map.of("requested_document_id", rs.getString("requested_document_id"),
                            "total_chunks", rs.getInt("total_chunks"), "chunk_digests", rs.getString("chunk_digests")),
                    scope, requested);
            if (aliases.isEmpty()) {
                jdbc.update("DELETE FROM rag_documents WHERE account_scope=? AND document_id=?", scope, requested);
                return deleteResponse(true, null);
            }

            Map<String, Object> old = existing.get(0);
            if (!"ready".equals(old.get("status")) || old.get("content_sha256") == null)
                throw new RagFailure("RAG_DOCUMENT_STATE_CONFLICT", "Shared document content is not ready", 409);
            Map<String, Object> promotedAlias = aliases.get(0);
            String promoted = (String) promotedAlias.get("requested_document_id");

            // Release the account/content unique key inside this transaction, then create the new
            // canonical row before moving chunks and foreign-key references. Other transactions
            // see either the old graph or the complete new graph, never this intermediate state.
            jdbc.update("UPDATE rag_documents SET status='uploading',content_sha256=NULL "
                    + "WHERE account_scope=? AND document_id=?", scope, requested);
            jdbc.update("INSERT INTO rag_documents(account_scope,document_id,content_sha256,title,source,total_chunks,status,created_at,updated_at) "
                            + "VALUES (?,?,?,?,?,?, 'ready', ?, ?)" ,
                    scope, promoted, old.get("content_sha256"), old.get("title"), old.get("source"),
                    old.get("total_chunks"), old.get("created_at"), old.get("updated_at"));
            jdbc.update("INSERT INTO rag_document_upload_manifests(account_scope,document_id,total_chunks,chunk_digests) VALUES (?,?,?,?)",
                    scope, promoted, promotedAlias.get("total_chunks"), promotedAlias.get("chunk_digests"));
            jdbc.update("DELETE FROM rag_document_aliases WHERE account_scope=? AND requested_document_id=?", scope, promoted);
            jdbc.update("DELETE FROM rag_source_chunks WHERE account_scope=? AND document_id=?", scope, requested);
            jdbc.update("UPDATE rag_source_chunks SET canonical_document_id=? WHERE account_scope=? AND canonical_document_id=?",
                    promoted, scope, requested);
            jdbc.update("UPDATE rag_document_aliases SET canonical_document_id=? "
                    + "WHERE account_scope=? AND canonical_document_id=?", promoted, scope, requested);
            jdbc.update("UPDATE rag_chunks SET document_id=? WHERE account_scope=? AND document_id=?", promoted, scope, requested);
            jdbc.update("DELETE FROM rag_documents WHERE account_scope=? AND document_id=?", scope, requested);
            return deleteResponse(false, promoted);
        });
    }

    private static Map<String, Object> deleteResponse(boolean contentDeleted, String canonicalId) {
        LinkedHashMap<String, Object> response = new LinkedHashMap<>();
        response.put("deleted", true);
        response.put("content_deleted", contentDeleted);
        response.put("canonical_id", canonicalId);
        // Keep the existing response key for older clients during rollout.
        response.put("document_id", canonicalId);
        return response;
    }

    private static String resolve(JdbcTemplate jdbc, String scope, String id) {
        List<String> aliases = jdbc.query("SELECT canonical_document_id FROM rag_document_aliases WHERE account_scope=? AND requested_document_id=?",
                (rs, row) -> rs.getString(1), scope, id);
        if (!aliases.isEmpty()) return aliases.get(0);
        List<String> docs = jdbc.query("SELECT document_id FROM rag_documents WHERE account_scope=? AND document_id=?",
                (rs, row) -> rs.getString(1), scope, id);
        return docs.isEmpty() ? null : docs.get(0);
    }

    private static String sourceStatus(JdbcTemplate jdbc,String scope,String id){
        List<Map<String,Object>> rows=jdbc.query("SELECT count(*) n,count(start_byte) located,min(total_chunks) low,max(total_chunks) high FROM rag_source_chunks WHERE account_scope=? AND document_id=?",
                (rs,row)->Map.of("n",rs.getInt("n"),"located",rs.getInt("located"),"low",rs.getInt("low"),"high",rs.getInt("high")),scope,id);
        int n=((Number)rows.get(0).get("n")).intValue(),located=((Number)rows.get(0).get("located")).intValue();
        if(n==0)return "unknown";
        int low=((Number)rows.get(0).get("low")).intValue(),high=((Number)rows.get(0).get("high")).intValue();
        return low==high&&n==high&&located==n?"known":"incomplete";
    }

    private static Map<String,Object> layoutInfo(JdbcTemplate jdbc,String scope,String id,int total){
        LinkedHashMap<String,Object> value=new LinkedHashMap<>();value.put("requested_document_id",id);
        value.put("total_chunks",total);value.put("complete","known".equals(sourceStatus(jdbc,scope,id)));return value;
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
        result.put("deduplicated", deduplicated); result.put("source_layout_status", "uploading".equals(status)?"incomplete":"unknown");
        result.put("source_layout_complete",false);return result;
    }

    private static Map<String,Object> decorateUpload(JdbcTemplate jdbc,String scope,String logical,Map<String,Object> response){
        String status=sourceStatus(jdbc,scope,logical);response.put("source_layout_status",status);
        response.put("source_layout_complete","known".equals(status));return response;
    }

    private static Integer uploadAliasTotal(JdbcTemplate jdbc,String scope,String logical){
        List<Integer> totals=jdbc.query("SELECT total_chunks FROM rag_document_aliases WHERE account_scope=? AND requested_document_id=?",(rs,row)->rs.getInt(1),scope,logical);
        return totals.isEmpty()?0:totals.get(0);
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

    private record LocatedPassage(String documentId,Integer seq,String text,String page,String section,String locationStatus,double score,String canonicalId,long startByte,long endByte){
        LocatedPassage withScore(double value){return new LocatedPassage(documentId,seq,text,page,section,locationStatus,value,canonicalId,startByte,endByte);}
    }
    private record CanonicalRanges(Map<Integer,long[]> bySeq,java.util.NavigableMap<Long,Integer> byStart) {}
}
