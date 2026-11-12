package win.clannad.mentorflow.rag;

import java.sql.PreparedStatement;
import java.util.function.Function;
import javax.sql.DataSource;
import win.clannad.mentorflow.persistence.BusinessDatabase;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.stereotype.Component;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.TransactionException;
import org.springframework.transaction.support.TransactionTemplate;

/** Account-scoped PostgreSQL access. RLS context is transaction-local and fail-closed. */
@Component
public final class RagRepository {
    private final DataSource dataSource;
    private final JdbcTemplate jdbc;
    private final DataSourceTransactionManager transactions;
    private final boolean ready;

    /** Does not connect at construction, so deployments without database config still start. */
    public RagRepository(BusinessDatabase business) {
        DataSource source = business.dataSource();
        this.dataSource = source;
        this.jdbc = source == null ? null : new JdbcTemplate(source);
        this.transactions = source == null ? null : new DataSourceTransactionManager(source);
        this.ready = source != null;
    }

    public <T> T transaction(String accountScope, boolean writing, Function<JdbcTemplate, T> work) {
        if (!ready) throw new RagFailure("RAG_NOT_READY", "RAG storage is not configured", 503);
        TransactionTemplate tx = new TransactionTemplate(transactions);
        if (!writing) tx.setIsolationLevel(TransactionDefinition.ISOLATION_REPEATABLE_READ);
        try {
            return tx.execute(status -> {
                bind(accountScope, writing);
                return work.apply(jdbc);
            });
        } catch (RagFailure failure) {
            throw failure;
        } catch (DataAccessException | TransactionException failure) {
            throw new RagFailure("RAG_STORAGE_UNAVAILABLE", "RAG storage is temporarily unavailable", 503, failure);
        }
    }

    private void bind(String accountScope, boolean writing) {
        jdbc.execute((ConnectionCallback<Void>) connection -> {
            if ("PostgreSQL".equalsIgnoreCase(connection.getMetaData().getDatabaseProductName())) {
                try (PreparedStatement statement = connection.prepareStatement(
                        "SELECT set_config('mentorflow.rag_account_scope', ?, true)")) {
                    statement.setString(1, accountScope);
                    statement.execute();
                }
                if (writing) {
                    try (PreparedStatement statement = connection.prepareStatement(
                            "SELECT pg_advisory_xact_lock(hashtextextended('mentorflow.rag:' || ?, 0))")) {
                        statement.setString(1, accountScope);
                        statement.execute();
                    }
                    try (PreparedStatement statement = connection.prepareStatement("SET LOCAL synchronous_commit = on")) {
                        statement.execute();
                    }
                }
            }
            return null;
        });
    }
}
