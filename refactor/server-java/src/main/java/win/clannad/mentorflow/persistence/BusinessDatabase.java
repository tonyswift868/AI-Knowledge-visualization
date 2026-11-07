package win.clannad.mentorflow.persistence;

import com.zaxxer.hikari.HikariDataSource;
import jakarta.annotation.PreDestroy;
import java.net.URI;
import javax.sql.DataSource;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.stereotype.Component;

/** One lazy business pool; account/admin DataSource beans keep their own lifecycle. */
@Component
public final class BusinessDatabase implements AutoCloseable {
    private final HikariDataSource pool;

    public BusinessDatabase(Environment environment) {
        String url = environment.getProperty("MENTORFLOW_DATABASE_URL");
        pool = url == null || url.isBlank() ? null : createPool(physicalSource(url));
    }

    /** Null preserves the repositories' existing unconfigured/503 behavior. */
    public DataSource dataSource() {
        return pool;
    }

    static HikariDataSource createPool(DataSource physical) {
        // Lazy initialization keeps construction and deployments without a live DB unchanged.
        HikariDataSource source = new HikariDataSource();
        source.setDataSource(physical);
        source.setPoolName("mentorflow-business");
        source.setMaximumPoolSize(10);
        source.setMinimumIdle(0);
        source.setConnectionTimeout(10_000);
        source.setInitializationFailTimeout(-1);
        return source;
    }

    private static DriverManagerDataSource physicalSource(String raw) {
        // Keep the repositories' existing driver/URL timeout and credential semantics.
        DriverManagerDataSource source = new DriverManagerDataSource();
        if (raw.startsWith("jdbc:postgresql:")) {
            source.setUrl(raw);
        } else {
            String normalized = raw.replaceFirst("^postgresql\\+[^:]+:", "postgresql:");
            if (normalized.startsWith("postgres://")) {
                normalized = "postgresql:" + normalized.substring("postgres:".length());
            }
            URI uri = URI.create(normalized);
            if (!"postgresql".equals(uri.getScheme()) || uri.getHost() == null) {
                throw new IllegalArgumentException("Business database URL must use PostgreSQL with a host");
            }
            String host = uri.getHost();
            if (host.contains(":") && !host.startsWith("[")) host = "[" + host + "]";
            source.setUrl("jdbc:postgresql://" + host + (uri.getPort() < 0 ? "" : ":" + uri.getPort())
                    + (uri.getRawPath() == null ? "" : uri.getRawPath())
                    + (uri.getRawQuery() == null ? "" : "?" + uri.getRawQuery()));
            if (uri.getUserInfo() != null) {
                String[] parts = uri.getUserInfo().split(":", 2);
                source.setUsername(parts[0]);
                if (parts.length > 1) source.setPassword(parts[1]);
            }
        }
        source.setDriverClassName("org.postgresql.Driver");
        return source;
    }

    @Override
    @PreDestroy
    public void close() {
        if (pool != null) pool.close();
    }
}
