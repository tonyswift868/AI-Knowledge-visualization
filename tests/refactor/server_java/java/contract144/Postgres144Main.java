package contract144;

import io.zonky.test.db.postgres.embedded.EmbeddedPostgres;
import java.nio.file.Path;
import java.time.Duration;

/** Small subprocess host so Python HTTP tests can use a real local PostgreSQL. */
public final class Postgres144Main {
    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new IllegalArgumentException("expected data-dir and port");
        Path data = Path.of(args[0]).toAbsolutePath();
        Path work = data.resolveSibling(data.getFileName() + "-work");
        java.nio.file.Files.createDirectories(data.getParent());
        java.nio.file.Files.createDirectories(work);
        EmbeddedPostgres postgres = EmbeddedPostgres.builder()
            .setDataDirectory(data)
            .setOverrideWorkingDirectory(work.toFile())
            .setPort(Integer.parseInt(args[1]))
            .setPGStartupWait(Duration.ofSeconds(60))
            .setRegisterShutdownHook(false)
            .start();
        System.out.println("PG144_READY " + postgres.getPort() + " "
            + postgres.getJdbcUrl("postgres", "postgres"));
        System.out.flush();
        try {
            while (System.in.read() != -1) { /* parent owns lifetime */ }
        } finally {
            postgres.close();
        }
    }
}
