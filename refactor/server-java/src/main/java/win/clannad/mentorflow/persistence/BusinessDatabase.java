package win.clannad.mentorflow.persistence;

import com.zaxxer.hikari.HikariDataSource;
import jakarta.annotation.PreDestroy;
import java.net.URI;
import javax.sql.DataSource;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

/** One Spring-managed pool for the mentorflow_app database role. */
@Component
public final class BusinessDatabase implements AutoCloseable {
    private final HikariDataSource pool;

    public BusinessDatabase(Environment environment) {
        String url = environment.getProperty("MENTORFLOW_DATABASE_URL");
        pool = url == null || url.isBlank() ? null : createPool(url);
    }

    /** Null preserves the repositories' existing unconfigured/503 behavior. */
    public DataSource dataSource() {
        return pool;
    }

    static HikariDataSource createPool(String raw) {
        HikariDataSource source = new HikariDataSource();
        String normalized = raw.replaceFirst("^postgresql\\+[^:]+:", "postgresql:");
        if (normalized.startsWith("jdbc:postgresql:")) {
            source.setJdbcUrl(normalized);
        } else {
            if (normalized.startsWith("postgres://")) {
                normalized = "postgresql:" + normalized.substring("postgres:".length());
            }
            URI uri = URI.create(normalized);
            if (!"postgresql".equals(uri.getScheme()) || uri.getHost() == null) {
                source.close();
                throw new IllegalArgumentException("Business database URL must use PostgreSQL with a host");
            }
            String host = uri.getHost();
            if (host.contains(":") && !host.startsWith("[")) host = "[" + host + "]";
            source.setJdbcUrl("jdbc:postgresql://" + host + (uri.getPort() < 0 ? "" : ":" + uri.getPort())
                    + (uri.getRawPath() == null ? "" : uri.getRawPath())
                    + (uri.getRawQuery() == null ? "" : "?" + uri.getRawQuery()));
            if (uri.getUserInfo() != null) {
                String[] parts = uri.getUserInfo().split(":", 2);
                source.setUsername(parts[0]);
                if (parts.length > 1) source.setPassword(parts[1]);
            }
        }
        source.setDriverClassName("org.postgresql.Driver");
        configurePool(source, "mentorflow-business");
        return source;
    }

    static HikariDataSource createPool(DataSource physical) {
        HikariDataSource source = new HikariDataSource();
        source.setDataSource(physical);
        configurePool(source, "mentorflow-business");
        return source;
    }

    private static void configurePool(HikariDataSource source, String poolName) {
        // Lazy initialization keeps construction and deployments without a live DB unchanged.
        source.setPoolName(poolName);
        source.setMaximumPoolSize(10);
        source.setMinimumIdle(0);
        source.setConnectionTimeout(10_000);
        source.setInitializationFailTimeout(-1);
    }

    @Override
    @PreDestroy
    public void close() {
        if (pool != null) pool.close();
    }
}
