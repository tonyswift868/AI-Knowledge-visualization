package receipt396;

import java.util.Arrays;
import java.util.Map;
import java.util.UUID;
import org.springframework.core.env.StandardEnvironment;
import org.springframework.core.env.MapPropertySource;
import win.clannad.mentorflow.persistence.BusinessDatabase;
import win.clannad.mentorflow.sync.SyncRepository;

/** Measures committed repository writes; caller chooses old/new implementation via classpath. */
public final class ReceiptBenchmark396 {
    public static void main(String[] args) {
        StandardEnvironment environment = new StandardEnvironment();
        environment.getPropertySources().addFirst(new MapPropertySource("benchmark", Map.of("MENTORFLOW_DATABASE_URL", args[0])));
        try (BusinessDatabase database = new BusinessDatabase(environment)) {
            SyncRepository repository = new SyncRepository(database);
            Map<String,Object> actor = Map.of("issuer", "bench396", "subject_id", "synthetic");
            Map<String,Object> tenant = Map.of("organization_id", "bench", "workspace_id", "bench");
            Map<String,Object> payload = Map.of("text", "x".repeat(128));
            double[] measured = new double[5];
            for (int i = -2; i < measured.length; i++) {
                String id = UUID.randomUUID().toString();
                long start = System.nanoTime();
                repository.write(actor, tenant, "learning_run", id, id, 0, payload, false);
                double elapsed = (System.nanoTime() - start) / 1_000_000.0;
                if (i >= 0) measured[i] = elapsed;
            }
            System.out.println("SAMPLES " + Arrays.toString(measured));
            Arrays.sort(measured);
            System.out.println("MEDIAN " + measured[2]);
        }
    }
}
