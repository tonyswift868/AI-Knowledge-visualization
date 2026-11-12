package win.clannad.mentorflow.sync;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.Locale;
import java.util.function.Function;
import java.util.logging.Level;
import java.util.logging.Logger;
import javax.sql.DataSource;
import win.clannad.mentorflow.persistence.BusinessDatabase;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.stereotype.Component;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.TransactionException;
import org.springframework.transaction.support.TransactionTemplate;

/** PostgreSQL persistence for the frozen structured-sync protocol. */
@Component
public final class SyncRepository {
    private static final Logger LOG = Logger.getLogger(SyncRepository.class.getName());
    private static final String SCOPE_WHERE = "issuer=? AND subject_id=? AND organization_id=? AND workspace_id=?";
    private static final String SCOPE_VERSION = "SUPPORTED_REHYDRATION_SCOPE_V1";
    private static final String[] SUPPORTED_SCOPE = {"plans", "progress", "resource_metadata", "plan_tasks", "calendars",
        "review_fsrs", "learner_profile", "learning_runs", "teaching_chat_history", "nonsecret_settings"};
    private static final String[] SUPPORTED_KINDS = {"plan", "progress", "resource", "task", "calendar", "review",
        "learner_profile", "learning_run", "history", "setting"};

    public record Operation(String kind, String entityId, String requestId, int expectedRevision,
                            Map<String, Object> payload, boolean deleting, boolean createMissingTombstone) {
        public Operation(String kind, String entityId, String requestId, int expectedRevision,
                Map<String, Object> payload, boolean deleting) {
            this(kind, entityId, requestId, expectedRevision, payload, deleting, false);
        }
    }

    private final DataSource dataSource;
    private final JdbcTemplate jdbc;
    private final DataSourceTransactionManager transactions;
    private final boolean ready;

    /** Does not connect at construction; the existing 142 test fixture can start without a DB URL. */
    public SyncRepository(BusinessDatabase business) {
        DataSource source = business.dataSource();
        this.dataSource = source;
        this.jdbc = source == null ? null : new JdbcTemplate(source);
        this.transactions = source == null ? null : new DataSourceTransactionManager(source);
        this.ready = source != null;
    }

    /** Best-effort account activity marker; telemetry failure must not affect sync. */
    public void recordDailyActivity(Map<String,Object> actor) {
        if (!ready) return;
        try {
            String issuer = requiredText(actor.get("issuer"));
            String subject = requiredText(actor.get("subject_id"));
            String scope = accountScope(actor);
            TransactionTemplate tx = new TransactionTemplate(transactions);
            tx.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
            tx.execute(status -> {
                jdbc.update("SELECT set_config('mentorflow.actor_issuer', ?, true), "
                        + "set_config('mentorflow.actor_subject', ?, true)", issuer, subject);
                jdbc.update("INSERT INTO account_daily_activity(account_scope,day) "
                        + "VALUES (?,(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date) ON CONFLICT DO NOTHING",
                        scope);
                return null;
            });
        } catch (RuntimeException failure) {
            LOG.log(Level.WARNING, "Could not record daily account activity", failure);
        }
    }

    private void requireReady() {
        if (!ready) throw new SyncFailure("SYNC_NOT_READY", 503);
    }

    public Map<String,Object> write(Map<String,Object> actor, Map<String,Object> tenant, String kind,
            String entityId, String requestId, int expectedRevision, Map<String,Object> payload, boolean deleting) {
        return write(actor, tenant, kind, entityId, requestId, expectedRevision, payload, deleting, false);
    }

    public Map<String,Object> write(Map<String,Object> actor, Map<String,Object> tenant, String kind,
            String entityId, String requestId, int expectedRevision, Map<String,Object> payload,
            boolean deleting, boolean createMissingTombstone) {
        if (createMissingTombstone && !deleting) throw new IllegalArgumentException("missing tombstones require a delete operation");
        requireReady();
        Operation operation = new Operation(kind, entityId, requestId, expectedRevision,
            deleting ? Map.of() : payload, deleting, createMissingTombstone);
        return inTransaction(actor, tenant, true, () -> writeRow(scope(actor, tenant), operation));
    }

    /** One outer transaction; each invalid/conflicting record rolls back only to its savepoint. */
    public Map<String,Object> writeMany(Map<String,Object> actor, Map<String,Object> tenant,
            List<Map<String,Object>> rows, Function<Map<String,Object>,Operation> validate) {
        requireReady();
        return inTransaction(actor, tenant, true, () -> {
            Map<String,Object> scope = scope(actor, tenant);
            List<Map<String,Object>> results = new ArrayList<>();
            for (Map<String,Object> row : rows) {
                LinkedHashMap<String,Object> identity = new LinkedHashMap<>();
                identity.put("kind", row.get("kind"));
                identity.put("entity_id", row.get("entity_id"));
                SavepointWork item = () -> {
                    Operation operation = validate.apply(row);
                    return writeRow(scope, operation);
                };
                try {
                    Map<String,Object> result = nested(item);
                    LinkedHashMap<String,Object> answer = new LinkedHashMap<>(identity);
                    answer.put("status", 200); answer.put("result", result); results.add(answer);
                } catch (SyncFailure failure) {
                    LinkedHashMap<String,Object> answer = new LinkedHashMap<>(identity);
                    answer.put("status", failure.status); answer.put("code", failure.code); results.add(answer);
                }
            }
            LinkedHashMap<String,Object> response = new LinkedHashMap<>();
            response.put("schema", "mentorflow.structured-sync-bulk.v1"); response.put("results", results);
            return response;
        });
    }

    @FunctionalInterface private interface SavepointWork { Map<String,Object> run(); }

    private Map<String,Object> nested(SavepointWork work) {
        TransactionTemplate nested = new TransactionTemplate(transactions);
        nested.setPropagationBehavior(TransactionDefinition.PROPAGATION_NESTED);
        return nested.execute(status -> work.run());
    }

    public Map<String,Object> snapshot(Map<String,Object> actor, Map<String,Object> tenant,
            List<String> requestedScope, String scopeVersion) {
        requireReady();
        return inTransaction(actor, tenant, false, () -> snapshotInTransaction(scope(actor, tenant), requestedScope, scopeVersion));
    }

    public Map<String,Object> changes(Map<String,Object> actor, Map<String,Object> tenant,
            long since, String kind, int limit) {
        requireReady();
        if (since < 0 || !Arrays.asList(SUPPORTED_KINDS).contains(kind) || limit < 1 || limit > 500)
            throw new SyncFailure("SYNC_CHANGES_QUERY_INVALID", 422);
        return inTransaction(actor, tenant, false, () -> changesInTransaction(scope(actor, tenant), since, kind, limit));
    }

    private <T> T inTransaction(Map<String,Object> actor, Map<String,Object> tenant, boolean writing,
            java.util.concurrent.Callable<T> action) {
        TransactionTemplate tx = new TransactionTemplate(transactions);
        try {
            return tx.execute(status -> {
                bind(actor, tenant, writing);
                try { return action.call(); }
                catch (RuntimeException error) { throw error; }
                catch (Exception error) { throw new IllegalStateException(error); }
            });
        } catch (DataAccessException | TransactionException failure) {
            throw new SyncFailure("SYNC_QUERY_FAILED", 503);
        }
    }

    private void bind(Map<String,Object> actor, Map<String,Object> tenant, boolean writing) {
        String issuer = requiredText(actor.get("issuer"));
        String subject = requiredText(actor.get("subject_id"));
        String organization = requiredText(tenant.get("organization_id"));
        String workspace = requiredText(tenant.get("workspace_id"));
        jdbc.execute((ConnectionCallback<Void>) connection -> {
            try (PreparedStatement statement = connection.prepareStatement("SELECT set_config('mentorflow.actor_issuer', ?, true), set_config('mentorflow.actor_subject', ?, true), set_config('mentorflow.organization_id', ?, true), set_config('mentorflow.workspace_id', ?, true)")) {
                statement.setString(1, issuer); statement.setString(2, subject); statement.setString(3, organization); statement.setString(4, workspace); statement.execute();
            }
            return null;
        });
        if (writing) {
            jdbc.execute((ConnectionCallback<Void>) connection -> {
                try (PreparedStatement statement = connection.prepareStatement("SELECT pg_advisory_xact_lock(?)")) {
                    statement.setLong(1, advisoryKey(scope(actor, tenant))); statement.execute();
                }
                return null;
            });
            jdbc.execute("SET LOCAL synchronous_commit = on");
        }
    }

    private static Map<String,Object> scope(Map<String,Object> actor, Map<String,Object> tenant) {
        LinkedHashMap<String,Object> value = new LinkedHashMap<>();
        value.put("issuer", actor.get("issuer")); value.put("subject_id", actor.get("subject_id"));
        value.put("organization_id", tenant.get("organization_id")); value.put("workspace_id", tenant.get("workspace_id"));
        return value;
    }

    private static String accountScope(Map<String,Object> actor) {
        return sha256(requiredText(actor.get("issuer")) + "\u0000" + requiredText(actor.get("subject_id")));
    }

    /** Matches Python hashlib.sha256(json.dumps(scope, sort_keys=True))[:8] as signed big-endian. */
    private static long advisoryKey(Map<String,Object> scope) {
        try {
            TreeMap<String,Object> ordered = new TreeMap<>(scope);
            StringBuilder json = new StringBuilder("{");
            boolean first = true;
            for (var entry : ordered.entrySet()) {
                if (!first) json.append(", ");
                first = false;
                json.append(pythonQuoted(entry.getKey())).append(": ").append(pythonQuoted(text(entry.getValue())));
            }
            json.append('}');
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(json.toString().getBytes(StandardCharsets.UTF_8));
            return java.nio.ByteBuffer.wrap(digest, 0, 8).getLong();
        } catch (Exception error) { throw new IllegalStateException(error); }
    }

    private static String pythonQuoted(String value) {
        String encoded = SyncJson.dump(value, false);
        StringBuilder ascii = new StringBuilder(encoded.length());
        for (int i = 0; i < encoded.length(); i++) {
            char c = encoded.charAt(i);
            if (c <= 0x7f) ascii.append(c);
            else if (Character.isHighSurrogate(c) && i + 1 < encoded.length() && Character.isLowSurrogate(encoded.charAt(i + 1))) {
                int cp = Character.toCodePoint(c, encoded.charAt(++i));
                int n = cp - 0x10000;
                ascii.append(String.format("\\u%04x\\u%04x", 0xd800 + (n >> 10), 0xdc00 + (n & 0x3ff)));
            } else ascii.append(String.format("\\u%04x", (int)c));
        }
        return ascii.toString();
    }

    private Map<String,Object> writeRow(Map<String,Object> scope, Operation op) {
        List<?> fingerprint = op.createMissingTombstone()
            ? List.of("tombstone-if-missing-v1", op.kind(), op.entityId(), op.expectedRevision(), op.deleting(), op.payload())
            : List.of(op.kind(), op.entityId(), op.expectedRevision(), op.deleting(), op.payload());
        String digest = sha256(SyncJson.dumpAsciiSorted(fingerprint));
        List<Map<String,Object>> receipts = jdbc.query("SELECT digest,response FROM structured_sync_requests WHERE " + SCOPE_WHERE + " AND request_id=?",
            (rs, row) -> Map.of("digest", rs.getString("digest"), "response", jsonObject(rs, "response")), scopeValues(scope, op.requestId()));
        if (!receipts.isEmpty()) {
            Map<String,Object> receipt = receipts.get(0);
            if (!digest.equals(receipt.get("digest"))) throw new SyncFailure("SYNC_IDEMPOTENCY_KEY_REUSED", 409);
            @SuppressWarnings("unchecked") Map<String,Object> response = (Map<String,Object>)receipt.get("response");
            return response;
        }
        List<Map<String,Object>> currentRows = jdbc.query("SELECT revision,deleted FROM structured_sync_entities WHERE " + SCOPE_WHERE + " AND kind=? AND entity_id=? FOR UPDATE",
            (rs, row) -> Map.of("revision", rs.getInt("revision"), "deleted", rs.getBoolean("deleted")), entityValues(scope, op.kind(), op.entityId()));
        boolean exists = !currentRows.isEmpty();
        int revision = exists ? (Integer)currentRows.get(0).get("revision") : 0;
        if (op.deleting() && !exists && !op.createMissingTombstone()) throw new SyncFailure("SYNC_ENTITY_NOT_FOUND", 404);
        if (revision != op.expectedRevision()) throw new SyncFailure("SYNC_STALE_REVISION", 409);
        if (exists && Boolean.TRUE.equals(currentRows.get(0).get("deleted"))) throw new SyncFailure("SYNC_ENTITY_TOMBSTONED", 410);
        verifyReferences(scope, op);
        LinkedHashMap<String,Object> result = new LinkedHashMap<>();
        result.put("kind", op.kind()); result.put("entity_id", op.entityId()); result.put("revision", revision + 1);
        result.put("deleted", op.deleting()); result.put("payload", op.payload());
        String payloadJson = SyncJson.dumpAscii(op.payload());
        if (exists) {
            int updated = jdbc.update("UPDATE structured_sync_entities SET revision=?,deleted=?,payload=?::json WHERE " + SCOPE_WHERE + " AND kind=? AND entity_id=? AND revision=?",
                revision + 1, op.deleting(), payloadJson, scope.get("issuer"), scope.get("subject_id"), scope.get("organization_id"), scope.get("workspace_id"), op.kind(), op.entityId(), revision);
            if (updated != 1) throw new SyncFailure("SYNC_STALE_REVISION", 409);
        } else {
            jdbc.update("INSERT INTO structured_sync_entities (issuer,subject_id,organization_id,workspace_id,kind,entity_id,revision,deleted,payload) VALUES (?,?,?,?,?,?,?, ?,?::json)",
                scope.get("issuer"), scope.get("subject_id"), scope.get("organization_id"), scope.get("workspace_id"), op.kind(), op.entityId(), revision + 1, op.deleting(), payloadJson);
        }
        jdbc.update("INSERT INTO structured_sync_requests (issuer,subject_id,organization_id,workspace_id,request_id,digest,response) VALUES (?,?,?,?,?,?,?::json)",
            scope.get("issuer"), scope.get("subject_id"), scope.get("organization_id"), scope.get("workspace_id"), op.requestId(), digest, SyncJson.dumpAscii(result));
        jdbc.update("UPDATE rehydration_manifests SET generation=generation+1,updated_at=CURRENT_TIMESTAMP WHERE " + SCOPE_WHERE + " AND initialized AND scope_version=?",
            scope.get("issuer"), scope.get("subject_id"), scope.get("organization_id"), scope.get("workspace_id"), SCOPE_VERSION);
        return result;
    }

    private void verifyReferences(Map<String,Object> scope, Operation op) {
        if (op.deleting()) return;
        Object resources = op.payload().get("resource_ids");
        if (resources instanceof List<?> list) for (Object resource : list) requireLiveReference(scope, "resource", String.valueOf(resource));
        if ("progress".equals(op.kind()) && op.payload().get("plan_id") instanceof String planId && !planId.isEmpty())
            requireLiveReference(scope, "plan", planId);
    }

    private void requireLiveReference(Map<String,Object> scope, String kind, String entityId) {
        Integer found = jdbc.query("SELECT 1 FROM structured_sync_entities WHERE " + SCOPE_WHERE + " AND kind=? AND entity_id=? AND deleted=false",
            rs -> rs.next() ? 1 : null, entityValues(scope, kind, entityId));
        if (found == null) throw new SyncFailure("SYNC_REFERENCE_NOT_IN_CURRENT_SCOPE", 422);
    }

    private Map<String,Object> changesInTransaction(Map<String,Object> scope, long since, String kind, int limit) {
        List<Map<String,Object>> rows = jdbc.query("SELECT kind,entity_id,revision,deleted,payload,change_seq FROM structured_sync_entities WHERE " + SCOPE_WHERE + " AND kind=? AND change_seq>? ORDER BY change_seq LIMIT ?",
            (rs, row) -> {
                LinkedHashMap<String,Object> value = new LinkedHashMap<>(); value.put("kind", rs.getString("kind")); value.put("entity_id", rs.getString("entity_id"));
                value.put("revision", rs.getInt("revision")); value.put("deleted", rs.getBoolean("deleted")); value.put("payload", jsonObject(rs, "payload")); value.put("change_seq", rs.getLong("change_seq")); return value;
            }, scopeValues(scope, kind, since, limit + 1));
        List<Map<String,Object>> entities = new ArrayList<>(rows.subList(0, Math.min(limit, rows.size())));
        long next = entities.isEmpty() ? since : ((Number)entities.get(entities.size() - 1).get("change_seq")).longValue();
        LinkedHashMap<String,Object> result = new LinkedHashMap<>(); result.put("schema", "mentorflow.structured-sync-changes.v1");
        result.put("contract_version", 1); result.put("scope", scope); result.put("kind", kind); result.put("since", Long.toString(since));
        result.put("next_cursor", Long.toString(next)); result.put("has_more", rows.size() > limit); result.put("entities", entities);
        return result;
    }

    private Map<String,Object> snapshotInTransaction(Map<String,Object> scope, List<String> requestedScope, String requestedVersion) {
        Map<String,Object> row = jdbc.queryForMap("""
            WITH scoped AS MATERIALIZED (
              SELECT kind, entity_id, revision, deleted, payload FROM structured_sync_entities
              WHERE issuer=? AND subject_id=? AND organization_id=? AND workspace_id=? ORDER BY kind,entity_id LIMIT 32769
            )
            SELECT pg_current_snapshot()::text AS revision, statement_timestamp() AS observed_at,
              (SELECT count(*) FROM scoped) AS item_count,
              (SELECT COALESCE(sum(octet_length(payload::text) + 512),0) FROM scoped) AS byte_count,
              CASE WHEN (SELECT count(*) <= 32768 AND COALESCE(sum(octet_length(payload::text) + 512),0) <= 134217728 FROM scoped)
                THEN COALESCE((SELECT json_agg(scoped ORDER BY kind,entity_id) FROM scoped), '[]'::json) ELSE '[]'::json END AS entities,
              (SELECT row_to_json(m) FROM (SELECT scope_version,initialized,generation,created_at,updated_at
                FROM rehydration_manifests WHERE issuer=? AND subject_id=? AND organization_id=? AND workspace_id=?) m) AS manifest
            """, scope.get("issuer"), scope.get("subject_id"), scope.get("organization_id"), scope.get("workspace_id"),
            scope.get("issuer"), scope.get("subject_id"), scope.get("organization_id"), scope.get("workspace_id"));
        int itemCount = ((Number)row.get("item_count")).intValue(); long byteCount = ((Number)row.get("byte_count")).longValue();
        if (itemCount > 32768 || byteCount > 128L * 1024 * 1024) throw new SyncFailure("SYNC_SNAPSHOT_LIMIT_EXCEEDED", 413);
        List<Map<String,Object>> all = objectList(row.get("entities"));
        List<Map<String,Object>> live = all.stream().filter(item -> !Boolean.TRUE.equals(item.get("deleted"))).toList();
        Map<String,Object> manifest = objectOrNull(row.get("manifest"));
        String version = requestedVersion == null ? SCOPE_VERSION : requestedVersion;
        List<String> requested = requestedScope == null ? List.of(SUPPORTED_SCOPE) : requestedScope;
        boolean initialized = manifest != null && Boolean.TRUE.equals(manifest.get("initialized")) && SCOPE_VERSION.equals(manifest.get("scope_version"));
        boolean unsupported = !SCOPE_VERSION.equals(version) || requested.stream().anyMatch(name -> !Arrays.asList(SUPPORTED_SCOPE).contains(name));
        unsupported |= manifest != null && !SCOPE_VERSION.equals(manifest.get("scope_version"));
        String state = unsupported ? "PARTIAL_UNSUPPORTED" : !initialized ? "NOT_INITIALIZED" : live.isEmpty() ? "TRUE_EMPTY_SUPPORTED_SCOPE" : "NONEMPTY";
        String marker = "pg-snapshot:" + row.get("revision");
        Object observedAt = row.get("observed_at");
        String confirmed = pythonIsoTimestamp(observedAt);
        LinkedHashMap<String,Object> learningState = new LinkedHashMap<>();
        learningState.put("status", state); learningState.put("empty_state", state); learningState.put("known_empty", "TRUE_EMPTY_SUPPORTED_SCOPE".equals(state));
        learningState.put("global_account_empty", "UNKNOWN"); learningState.put("account_initialized", "UNKNOWN"); learningState.put("initialized", initialized);
        learningState.put("supported_scope_initialized", initialized); learningState.put("supported_scope_empty", initialized && !unsupported ? live.isEmpty() : null);
        learningState.put("supported_scope", List.of(SUPPORTED_SCOPE)); learningState.put("scope_version", SCOPE_VERSION); learningState.put("requested_scope", requested);
        learningState.put("requested_scope_version", version); learningState.put("initialization_authority", "COMMITTED_BOOTSTRAP_REHYDRATION_MANIFEST");
        learningState.put("state_generation", manifest == null ? null : manifest.get("generation")); learningState.put("manifest", manifest); learningState.put("snapshot_revision", marker);
        LinkedHashMap<String,Object> provenance = new LinkedHashMap<>(); provenance.put("source", "SERVER_CONFIRMED");
        provenance.put("snapshot_revision", marker); provenance.put("server_confirmed_at", confirmed); provenance.put("trusted_cache", false);
        List<Map<String,Object>> resources = resourceProjection(live);
        List<Map<String,Object>> progress = new ArrayList<>();
        for (Map<String,Object> e : live) if ("progress".equals(e.get("kind"))) {
            LinkedHashMap<String,Object> p = new LinkedHashMap<>(); p.put("entity_id",e.get("entity_id")); p.put("revision",e.get("revision")); p.putAll(provenance); progress.add(p);
        }
        Map<String,Object> observationLearningState = new LinkedHashMap<>(learningState);
        LinkedHashMap<String,Object> observation = new LinkedHashMap<>(); observation.put("schema","mentorflow.sync-observation.v1"); observation.putAll(provenance);
        observation.put("empty_state",state); observation.put("supported_scope",List.of(SUPPORTED_SCOPE)); observation.put("scope_version",SCOPE_VERSION);
        observation.put("initialized",initialized); observation.put("state_generation",learningState.get("state_generation")); observation.put("supported_entity_types",List.of(SUPPORTED_SCOPE));
        observation.put("unsupported_entity_types",List.of("model_binary")); observation.put("counts",counts(live, all));
        LinkedHashMap<String,Object> unsupportedCounts = new LinkedHashMap<>(); unsupportedCounts.put("model_binary", null);
        observation.put("unsupported_counts",unsupportedCounts); observation.put("resource_missing_state","NOT_OBSERVED_BY_SERVER"); observation.put("learning_state",observationLearningState);
        LinkedHashMap<String,Object> result = new LinkedHashMap<>(); result.put("schema","mentorflow.structured-sync.v1"); result.put("contract_version",3);
        result.put("complete",true); result.put("completeness_scope","SUPPORTED_ENTITIES_ONLY"); result.put("snapshot_revision",marker);
        result.put("snapshot_authority","POSTGRESQL_SINGLE_STATEMENT_MVCC"); result.put("snapshot_scope",scope); result.put("revision_ordering","OPAQUE_OBSERVATION_NOT_INCREMENTAL_CURSOR");
        result.put("supported_entity_types",List.of(SUPPORTED_SCOPE)); result.put("unsupported_entity_types",List.of("model_binary")); result.put("learning_state",learningState);
        result.put("entities",all); result.put("resources",resources); result.put("progress_provenance",progress); result.put("observation",observation);
        return result;
    }

    private static List<Map<String,Object>> resourceProjection(List<Map<String,Object>> live) {
        Map<String,List<String>> references = new LinkedHashMap<>();
        for (Map<String,Object> entity : live) if ("plan".equals(entity.get("kind"))) {
            Map<String,Object> payload = objectOrNull(entity.get("payload"));
            Object resourceIds = payload == null ? null : payload.get("resource_ids");
            if (resourceIds instanceof List<?> list) for (Object value : list) references.computeIfAbsent(String.valueOf(value), key -> new ArrayList<>()).add(String.valueOf(entity.get("entity_id")));
        }
        List<Map<String,Object>> result = new ArrayList<>();
        for (Map<String,Object> entity : live) if ("resource".equals(entity.get("kind"))) {
            Map<String,Object> payload = objectOrNull(entity.get("payload"));
            LinkedHashMap<String,Object> projected = new LinkedHashMap<>(); projected.put("resource_id",entity.get("entity_id")); projected.put("revision",entity.get("revision"));
            projected.put("product_classification", payload != null && payload.containsKey("product_classification") ? payload.get("product_classification") : "UNKNOWN");
            projected.put("classification_authority", payload != null && payload.containsKey("product_classification") ? "AUTHENTICATED_RESOURCE_METADATA" : "UNCLASSIFIED_LEGACY_METADATA");
            List<String> planIds = new ArrayList<>(references.getOrDefault(String.valueOf(entity.get("entity_id")), List.of())); planIds.sort(String::compareTo);
            projected.put("plan_ids",planIds); projected.put("device_availability","NOT_OBSERVED_BY_SERVER"); result.add(projected);
        }
        return result;
    }

    private static Map<String,Object> counts(List<Map<String,Object>> live, List<Map<String,Object>> all) {
        LinkedHashMap<String,Object> result = new LinkedHashMap<>();
        String[] domains = {"plans","progress","resource_metadata","plan_tasks","calendars","review_fsrs","learner_profile","learning_runs","teaching_chat_history","nonsecret_settings"};
        for (int i=0;i<SUPPORTED_KINDS.length;i++) {
            String kind = SUPPORTED_KINDS[i], domain = domains[i];
            long count = live.stream().filter(e -> kind.equals(e.get("kind"))).filter(e -> {
                Map<String,Object> payload=objectOrNull(e.get("payload")); return payload==null || !Boolean.TRUE.equals(payload.get("product_auxiliary"));
            }).count(); result.put(domain,count);
        }
        result.put("tombstones", all.size()-live.size()); return result;
    }

    private static String text(Object value) { return value == null ? "" : String.valueOf(value); }
    private static String requiredText(Object value) {
        if (!(value instanceof String text) || text.isEmpty()) throw new IllegalArgumentException("sync tenant context is required");
        return text;
    }
    private static String pythonIsoTimestamp(Object value) {
        java.time.OffsetDateTime offset = value instanceof java.time.OffsetDateTime dateTime
            ? dateTime : ((java.sql.Timestamp)value).toInstant().atOffset(java.time.ZoneOffset.UTC);
        String base = java.time.format.DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss", Locale.ROOT).format(offset);
        int micros = offset.getNano() / 1_000;
        if (micros != 0) base += String.format(Locale.ROOT, ".%06d", micros);
        String zone = offset.getOffset().getId();
        return base + ("Z".equals(zone) ? "+00:00" : zone);
    }
    private static String sha256(String value) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); }
        catch (Exception error) { throw new IllegalStateException(error); }
    }
    private static Object jsonObject(ResultSet rs, String column) throws SQLException {
        Object value = rs.getObject(column);
        if (value == null) return null;
        String json = String.valueOf(value);
        return SyncJson.read(json);
    }
    @SuppressWarnings("unchecked") private static Map<String,Object> objectOrNull(Object value) {
        if (value == null) return null;
        if (value instanceof Map<?,?>) return (Map<String,Object>)value;
        return (Map<String,Object>)SyncJson.read(String.valueOf(value));
    }
    @SuppressWarnings("unchecked") private static List<Map<String,Object>> objectList(Object value) {
        Object parsed = value instanceof List<?> ? value : SyncJson.read(String.valueOf(value));
        List<Map<String,Object>> result = new ArrayList<>();
        for (Object item : (List<?>)parsed) result.add((Map<String,Object>)item);
        return result;
    }
    private static Object[] scopeValues(Map<String,Object> scope, Object... tail) {
        Object[] result = new Object[4 + tail.length]; int i=0;
        for (String key : List.of("issuer","subject_id","organization_id","workspace_id")) result[i++]=scope.get(key);
        for (Object value : tail) result[i++]=value; return result;
    }
    private static Object[] entityValues(Map<String,Object> scope, String kind, String entityId) { return scopeValues(scope,kind,entityId); }
}
