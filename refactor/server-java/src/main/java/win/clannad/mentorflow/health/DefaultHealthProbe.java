package win.clannad.mentorflow.health;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.atomic.AtomicBoolean;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import win.clannad.mentorflow.identity.IdentityDirectory;
import win.clannad.mentorflow.admin.NativeEmailHealthProbe;

/** ACCOUNT_CORE_MVP probes corresponding to observability.health. */
@Component
public final class DefaultHealthProbe implements HealthProbe {
    private static final String PROFILE = "ACCOUNT_CORE_MVP";
    private static final String REQUIRED_AND_HEALTHY = "REQUIRED_AND_HEALTHY";
    private static final String REQUIRED_AND_UNHEALTHY = "REQUIRED_AND_UNHEALTHY";
    private static final String NOT_REQUIRED = "NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP";
    private static final List<String> OPTIONAL = List.of(
        "ai_provider", "assessment", "document_ingestion", "document_parsing",
        "document_worker", "object_store", "otel_collector", "teacher",
        "web_frontend", "worker_dependencies");
    private final Environment env;
    private final IdentityDirectory directory;
    private final NativeEmailHealthProbe emailHealth;
    private final AtomicBoolean started = new AtomicBoolean(false);
    private final HttpClient http = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(2)).followRedirects(HttpClient.Redirect.NEVER).build();
    private final ObjectMapper json = new ObjectMapper();

    public DefaultHealthProbe(Environment env, IdentityDirectory directory, NativeEmailHealthProbe emailHealth) {
        this.env = env;
        this.directory = directory;
        this.emailHealth = emailHealth;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void markStarted() { started.set(true); }

    @Override public boolean started() { return started.get(); }

    @Override
    public List<Dependency> dependencies() {
        Map<String, Dependency> result = new TreeMap<>();
        // Python registers the same native identity authority's cached SMTP probe.
        // No backend SMTP connection or send is introduced here.
        long emailStarted = System.nanoTime();
        Map<String,String> email = emailHealth.sample();
        result.put("email", new Dependency("email", "healthy".equals(email.get("status")),
            email.get("code"), elapsedMs(emailStarted), false, null));

        String databaseUrl = text("MENTORFLOW_DATABASE_URL");
        long start = System.nanoTime();
        boolean dbReady = false;
        String dbCode;
        if (databaseUrl.isEmpty()) {
            dbCode = "DATABASE_URL_MISSING";
        } else {
            try {
                dbReady = directory.reachable();
                dbCode = dbReady ? "POSTGRES_REACHABLE" : "POSTGRES_UNREACHABLE";
            } catch (Exception unavailable) {
                dbCode = "POSTGRES_UNREACHABLE";
            }
        }
        result.put("postgres", required("postgres", dbReady, dbCode, elapsedMs(start)));

        String discovery = text("MENTORFLOW_KEYCLOAK_DISCOVERY_URL");
        start = System.nanoTime();
        boolean keycloakReady = false;
        String keycloakCode;
        if (discovery.isEmpty()) {
            keycloakCode = "KEYCLOAK_DISCOVERY_URL_MISSING";
        } else {
            try {
                HttpRequest request = HttpRequest.newBuilder(URI.create(discovery))
                    .timeout(Duration.ofSeconds(2)).GET().build();
                HttpResponse<byte[]> response = http.send(request, HttpResponse.BodyHandlers.ofByteArray());
                JsonNode value = response.statusCode() == 200 ? json.readTree(response.body()) : null;
                keycloakReady = value != null && pythonTruthy(value.path("issuer"));
                keycloakCode = keycloakReady ? "KEYCLOAK_DISCOVERY_OK" : "KEYCLOAK_DISCOVERY_UNREACHABLE";
            } catch (Exception unavailable) {
                keycloakCode = "KEYCLOAK_DISCOVERY_UNREACHABLE";
            }
        }
        result.put("keycloak_discovery", required("keycloak_discovery", keycloakReady,
            keycloakCode, elapsedMs(start)));

        List<Dependency> all = new ArrayList<>(result.values());
        for (String name : OPTIONAL) all.add(new Dependency(name, false, NOT_REQUIRED, 0, false, NOT_REQUIRED));
        return List.copyOf(all);
    }

    private static Dependency required(String name, boolean ready, String code, long duration) {
        return new Dependency(name, ready, code, duration, true,
            ready ? REQUIRED_AND_HEALTHY : REQUIRED_AND_UNHEALTHY);
    }
    private static boolean pythonTruthy(JsonNode value) {
        if (value == null || value.isMissingNode() || value.isNull()) return false;
        if (value.isBoolean()) return value.booleanValue();
        if (value.isNumber()) return value.decimalValue().signum() != 0;
        if (value.isTextual()) return !value.textValue().isEmpty();
        // Python treats even empty lists and dictionaries as true.
        return value.isArray() || value.isObject();
    }
    private String text(String key) { return env.getProperty(key, "").trim(); }
    private static long elapsedMs(long start) { return Math.max(0, (System.nanoTime() - start) / 1_000_000); }
}
