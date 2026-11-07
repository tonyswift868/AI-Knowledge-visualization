package win.clannad.mentorflow.persistence;

import com.zaxxer.hikari.HikariDataSource;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import javax.sql.DataSource;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.core.env.StandardEnvironment;
import org.springframework.jdbc.core.JdbcTemplate;
import win.clannad.mentorflow.rag.RagRepository;
import win.clannad.mentorflow.sync.SyncRepository;

/** Disposable migrated PostgreSQL integration gate for shared-pool transaction wiring. */
public final class BusinessDatabasePostgres323Main {
    private BusinessDatabasePostgres323Main() {}

    public static void main(String[] args) throws Exception {
        String url = new StandardEnvironment().getProperty("MENTORFLOW_DATABASE_URL");
        check(url != null && !url.isBlank(), "MENTORFLOW_DATABASE_URL is required");

        AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext();
        try {
            context.register(BusinessDatabase.class, RagRepository.class, SyncRepository.class);
            try {
                context.refresh();
            } catch (RuntimeException | Error failure) {
                context.close();
                throw failure;
            }

            BusinessDatabase holder = context.getBean(BusinessDatabase.class);
            RagRepository rag = context.getBean(RagRepository.class);
            SyncRepository sync = context.getBean(SyncRepository.class);
            HikariDataSource pool = (HikariDataSource) holder.dataSource();
            check(pool != null, "business pool is not configured");
            check(repositoryDataSource(rag) == pool && repositoryDataSource(sync) == pool,
                    "both repositories must use the holder's shared pool");
            pool.setMaximumPoolSize(1);

            JdbcTemplate outside = new JdbcTemplate(pool);
            Map<String, Object> role = outside.queryForMap("""
                    SELECT current_user AS role_name, r.rolsuper, r.rolbypassrls
                    FROM pg_roles r WHERE r.rolname = current_user
                    """);
            check(!Boolean.TRUE.equals(role.get("rolsuper")) && !Boolean.TRUE.equals(role.get("rolbypassrls")),
                    "database role must be non-superuser and must not bypass RLS");

            String nonce = UUID.randomUUID().toString();
            String scopeA = sha256("323-A:" + nonce);
            String scopeB = sha256("323-B:" + nonce);
            String docA = "323-" + nonce + "-A";
            String docB = "323-" + nonce + "-B";
            String rollbackDoc = "323-" + nonce + "-rollback";
            int[] pids = new int[2];
            int[] visible = new int[2];
            int[] hidden = new int[2];
            int[] transactionCount = {0};
            try {
                pids[0] = insertAndCount(rag, scopeA, docA, docB, visible, hidden, 0);
                assertCleanSession(outside, "RAG scope A commit");
                pids[1] = insertAndCount(rag, scopeB, docB, docA, visible, hidden, 1);
                assertCleanSession(outside, "RAG scope B commit");
                check(pids[0] == pids[1], "single-slot pool must reuse the same backend PID");
                check(visible[0] == 1 && visible[1] == 1 && hidden[0] == 0 && hidden[1] == 0,
                        "each RAG scope must see only its own document");
                check(countDocument(rag, scopeA, docB) == 0,
                        "scope A must not see B after both documents have committed");
                transactionCount[0] += 2;

                try {
                    rag.transaction(scopeA, true, jdbc -> {
                        jdbc.update("""
                                INSERT INTO rag_documents(account_scope,document_id,title,source,total_chunks,status)
                                VALUES (?,?,?,'fixture',1,'uploading')
                                """, scopeA, rollbackDoc, "323 rollback fixture");
                        throw new IllegalStateException("323 rollback fixture");
                    });
                    throw new AssertionError("injected failure did not escape the RAG transaction");
                } catch (IllegalStateException expected) {
                    check("323 rollback fixture".equals(expected.getMessage()), "unexpected rollback failure");
                }
                check(countDocument(rag, scopeA, rollbackDoc) == 0, "rolled-back RAG row must not persist");
                assertCleanSession(outside, "RAG rollback");
                transactionCount[0]++;

                Map<String, Object> actor = Map.of("issuer", "https://323.invalid", "subject_id", "subject-" + nonce);
                Map<String, Object> tenant = Map.of("organization_id", "org-" + nonce, "workspace_id", "workspace-" + nonce);
                Map<String, Object> snapshot = sync.snapshot(actor, tenant, List.of("plans"), null);
                check(snapshot.containsKey("schema"), "Sync snapshot should return its normal empty-scope response");
                assertCleanSession(outside, "Sync snapshot commit");
                transactionCount[0]++;

                System.out.println("BUSINESS_DATABASE_POSTGRES_323_OK transactions=" + transactionCount[0]
                        + " rag_documents=" + (visible[0] + visible[1]) + " backend_pid=" + pids[0]);
            } finally {
                try {
                    deleteDocument(rag, scopeA, docA);
                    deleteDocument(rag, scopeB, docB);
                    deleteDocument(rag, scopeA, rollbackDoc);
                } finally {
                    context.close();
                }
            }
        } finally {
            context.close();
        }
    }

    private static int insertAndCount(RagRepository rag, String scope, String ownId, String otherId,
            int[] visible, int[] hidden, int index) {
        return rag.transaction(scope, true, jdbc -> {
            jdbc.update("""
                    INSERT INTO rag_documents(account_scope,document_id,title,source,total_chunks,status)
                    VALUES (?,?,?,'fixture',1,'uploading')
                    """, scope, ownId, "323 wiring fixture " + index);
            int own = jdbc.queryForObject("SELECT count(*) FROM rag_documents WHERE document_id IN (?,?)",
                    Integer.class, ownId, otherId);
            int foreign = jdbc.queryForObject("SELECT count(*) FROM rag_documents WHERE document_id=?",
                    Integer.class, otherId);
            int pid = jdbc.queryForObject("SELECT pg_backend_pid()", Integer.class);
            visible[index] = own;
            hidden[index] = foreign;
            return pid;
        });
    }

    private static int countDocument(RagRepository rag, String scope, String documentId) {
        return rag.transaction(scope, false, jdbc -> jdbc.queryForObject(
                "SELECT count(*) FROM rag_documents WHERE document_id=?", Integer.class, documentId));
    }

    private static void deleteDocument(RagRepository rag, String scope, String documentId) {
        rag.transaction(scope, true, jdbc -> jdbc.update(
                "DELETE FROM rag_documents WHERE account_scope=? AND document_id=?", scope, documentId));
    }

    private static void assertCleanSession(JdbcTemplate outside, String operation) {
        Map<String, Object> state = outside.queryForMap("""
                SELECT pg_backend_pid() AS backend_pid,
                       current_setting('mentorflow.actor_issuer', true) AS actor_issuer,
                       current_setting('mentorflow.actor_subject', true) AS actor_subject,
                       current_setting('mentorflow.organization_id', true) AS organization_id,
                       current_setting('mentorflow.workspace_id', true) AS workspace_id,
                       current_setting('mentorflow.rag_account_scope', true) AS rag_account_scope
                """);
        for (String key : List.of("actor_issuer", "actor_subject", "organization_id", "workspace_id", "rag_account_scope")) {
            Object value = state.get(key);
            check(value == null || value.toString().isEmpty(), operation + " leaked session setting " + key);
        }
    }

    private static DataSource repositoryDataSource(Object repository) throws Exception {
        var field = repository.getClass().getDeclaredField("dataSource");
        field.setAccessible(true);
        return (DataSource) field.get(repository);
    }

    private static String sha256(String value) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
        return java.util.HexFormat.of().formatHex(digest);
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
