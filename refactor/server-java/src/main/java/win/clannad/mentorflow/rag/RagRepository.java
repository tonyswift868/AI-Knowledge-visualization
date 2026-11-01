package win.clannad.mentorflow.rag;

import java.net.URI;
import java.sql.PreparedStatement;
import java.util.function.Function;
import javax.sql.DataSource;
import org.springframework.core.env.Environment;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
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
    public RagRepository(Environment environment) {
        String url = environment.getProperty("MENTORFLOW_DATABASE_URL");
        if (url == null || url.isBlank()) {
            this.dataSource = null;
            this.jdbc = null;
            this.transactions = null;
            this.ready = false;
            return;
        }
        DriverManagerDataSource source = new DriverManagerDataSource();
        if (url.startsWith("jdbc:postgresql:")) source.setUrl(url);
        else {
            String normalized = url.replaceFirst("^postgresql\\+[^:]+:", "postgresql:");
            if (normalized.startsWith("postgres://")) normalized = "postgresql:" + normalized.substring("postgres:".length());
            URI uri = URI.create(normalized);
            if (!"postgresql".equals(uri.getScheme())) throw new IllegalArgumentException("RAG database URL must use PostgreSQL");
            String userInfo = uri.getUserInfo();
            if (userInfo != null) {
                String[] parts = userInfo.split(":", 2);
                source.setUsername(parts[0]);
                if (parts.length > 1) source.setPassword(parts[1]);
            }
            String host = uri.getHost();
            if (host == null) throw new IllegalArgumentException("RAG database URL must include a host");
            if (host.contains(":")) host = "[" + host + "]";
            source.setUrl("jdbc:postgresql://" + host + (uri.getPort() < 0 ? "" : ":" + uri.getPort())
                + (uri.getRawPath() == null ? "" : uri.getRawPath())
                + (uri.getRawQuery() == null ? "" : "?" + uri.getRawQuery()));
        }
        source.setDriverClassName("org.postgresql.Driver");
        this.dataSource = source;
        this.jdbc = new JdbcTemplate(source);
        this.transactions = new DataSourceTransactionManager(source);
        this.ready = true;
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
            throw new RagFailure("RAG_STORAGE_UNAVAILABLE", "RAG storage is temporarily unavailable", 503);
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
