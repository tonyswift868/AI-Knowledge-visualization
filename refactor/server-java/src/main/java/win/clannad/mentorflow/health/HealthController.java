package win.clannad.mentorflow.health;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.MediaType;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** Public liveness/startup/readiness endpoints with Python-compatible JSON order. */
@RestController
public final class HealthController {
    private static final String PROFILE = "ACCOUNT_CORE_MVP";
    private static final String SERVICE = "mentorflow-account-core";
    private static final ObjectMapper JSON = new ObjectMapper();
    private final HealthProbe health;

    public HealthController(HealthProbe health) { this.health = health; }

    @GetMapping("/health/live")
    public ResponseEntity<byte[]> live() {
        Map<String, String> body = new LinkedHashMap<>();
        body.put("status", "live");
        body.put("service", SERVICE);
        body.put("runtime_profile", PROFILE);
        return json(body, HttpStatus.OK);
    }

    @GetMapping("/health/startup")
    public ResponseEntity<byte[]> startup() {
        boolean started = health.started();
        Map<String, String> body = new LinkedHashMap<>();
        body.put("status", started ? "started" : "starting");
        body.put("runtime_profile", PROFILE);
        return json(body, started ? HttpStatus.OK : HttpStatus.SERVICE_UNAVAILABLE);
    }

    @GetMapping("/health/ready")
    public ResponseEntity<byte[]> ready() {
        List<HealthProbe.Dependency> dependencies = health.dependencies();
        boolean started = health.started();
        boolean ready = started && dependencies.stream().allMatch(d -> d.ready() || !d.required());
        List<Map<String, Object>> encoded = new ArrayList<>();
        for (HealthProbe.Dependency dependency : dependencies) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("name", dependency.name());
            item.put("ready", dependency.ready());
            item.put("code", dependency.code());
            item.put("duration_ms", dependency.durationMs());
            item.put("required", dependency.required());
            if (dependency.requirementState() != null) item.put("requirement_state", dependency.requirementState());
            encoded.add(item);
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", ready ? "ready" : "not_ready");
        body.put("started", started);
        body.put("runtime_profile", PROFILE);
        body.put("dependencies", encoded);
        return json(body, ready ? HttpStatus.OK : HttpStatus.SERVICE_UNAVAILABLE);
    }

    private static ResponseEntity<byte[]> json(Object body, HttpStatus status) {
        try {
            byte[] bytes = JSON.writeValueAsBytes(body);
            return ResponseEntity.status(status).contentType(MediaType.APPLICATION_JSON)
                .contentLength(bytes.length).body(bytes);
        } catch (JsonProcessingException impossible) {
            throw new IllegalStateException(impossible);
        }
    }
}
