package question391;

import io.zonky.test.db.postgres.embedded.EmbeddedPostgres;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.Statement;

/** Isolated PostgreSQL with collector logs enabled for auto_explain assertions. */
public final class EmbeddedPostgres391 {
    private EmbeddedPostgres391() {}

    public static void main(String[] args) throws Exception {
        Path data = Path.of(args[0]).toAbsolutePath();
        Path logs = data.resolveSibling("postgres-logs");
        Files.createDirectories(logs);
        EmbeddedPostgres postgres = EmbeddedPostgres.builder().setPort(0)
                .setDataDirectory(data)
                .setServerConfig("logging_collector", "on")
                .setServerConfig("log_destination", "stderr")
                .setServerConfig("log_directory", logs.toString().replace('\\', '/'))
                .setServerConfig("log_filename", "postgresql.log")
                .setServerConfig("log_min_messages", "log")
                .start();
        try {
            String jdbcUrl;
            try (Connection connection = postgres.getPostgresDatabase().getConnection()) {
                jdbcUrl = connection.getMetaData().getURL();
                try (Statement statement = connection.createStatement()) {
                    statement.execute("CREATE ROLE mentorflow_migrator LOGIN PASSWORD 'migration145'");
                    statement.execute("CREATE ROLE mentorflow_app LOGIN PASSWORD 'application145'");
                    statement.execute("CREATE ROLE mentorflow_closure NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB");
                    statement.execute("GRANT mentorflow_closure TO mentorflow_migrator");
                    statement.execute("CREATE DATABASE sync145_python OWNER mentorflow_migrator");
                    statement.execute("CREATE DATABASE sync145_java OWNER mentorflow_migrator");
                }
            }
            for (String database : new String[] {"sync145_python", "sync145_java"}) {
                String databaseUrl = jdbcUrl.substring(0, jdbcUrl.lastIndexOf('/') + 1) + database;
                try (Connection connection = DriverManager.getConnection(databaseUrl, "postgres", "postgres");
                     Statement statement = connection.createStatement()) {
                    statement.execute("CREATE SCHEMA account_closure AUTHORIZATION mentorflow_closure");
                }
            }
            System.out.println("READY " + jdbcUrl);
            System.out.flush();
            while (System.in.read() != -1) { /* keep embedded PostgreSQL alive */ }
        } finally {
            postgres.close();
        }
    }
}
