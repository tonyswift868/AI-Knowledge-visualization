package contract142;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.core.env.Environment;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import win.clannad.mentorflow.health.HealthProbe;
import win.clannad.mentorflow.identity.IdentityDirectory;

/** Test adapters only; HTTP requests still pass through the production filters/controllers. */
@Configuration(proxyBeanMethods=false)
public class Contract142Fixtures {
    @Bean @Primary
    IdentityDirectory contract142Directory(Environment env) {
        String issuer=env.getRequiredProperty("MENTORFLOW_KEYCLOAK_ISSUER");
        Path healthState=Path.of(env.getRequiredProperty("CONTRACT142_HEALTH_FILE"));
        return new IdentityDirectory() {
            @Override public void ensureOpen(String actualIssuer,String subject) {
                try {
                    Map<?,?> state=new ObjectMapper().readValue(Files.readAllBytes(healthState),Map.class);
                    Object closureValue=state.get("closure");
                    String closure=closureValue==null?"open":String.valueOf(closureValue);
                    if ("closed".equals(closure)) throw new win.clannad.mentorflow.identity.IdentityFailure(403,"ACCOUNT_CLOSURE_IN_PROGRESS","账号注销处理中或身份已关闭。");
                    if ("unavailable".equals(closure)) throw new win.clannad.mentorflow.identity.IdentityFailure(503,"CLOSURE_STORAGE_UNAVAILABLE","账号状态暂不可用。");
                } catch (win.clannad.mentorflow.identity.IdentityFailure failure) { throw failure; }
                catch (Exception failure) { throw new win.clannad.mentorflow.identity.IdentityFailure(503,"CLOSURE_STORAGE_UNAVAILABLE","账号状态暂不可用。"); }
            }
            @Override public Membership resolve(String actualIssuer,String subject,String organization,String workspace) {
                if (issuer.equals(actualIssuer) && "user-142".equals(subject)
                    && "org-142".equals(organization) && ("ws-142".equals(workspace) || workspace==null))
                    return new Membership("membership-142",List.of("LEARNER"));
                return null;
            }
            @Override public Map<String,Object> service(String actualIssuer,String subject,String clientId) {
                if (!issuer.equals(actualIssuer) || !"svc-sub142".equals(subject)
                    || !"mentorflow-desktop".equals(clientId)) return null;
                var grant=new java.util.LinkedHashMap<String,Object>();
                grant.put("service_id","service-142"); grant.put("issuer",issuer);
                grant.put("subject",subject); grant.put("client_id",clientId);
                grant.put("organization_id","org-142"); grant.put("workspace_id","ws-142");
                grant.put("permissions",List.of("DOCUMENT_READ")); grant.put("status","ACTIVE");
                return grant;
            }
            @Override public boolean reachable() { return true; }
        };
    }

    @Bean @Primary
    HealthProbe contract142Health(Environment env) {
        return new FixtureHealthProbe(Path.of(env.getRequiredProperty("CONTRACT142_HEALTH_FILE")));
    }

    @Bean
    Contract142ContextController contract142ContextController(Environment env) {
        return new Contract142ContextController(Path.of(env.getRequiredProperty("CONTRACT142_HEALTH_FILE")));
    }

    static final class FixtureHealthProbe implements HealthProbe {
        private final Path state;
        private final ObjectMapper mapper=new ObjectMapper();
        FixtureHealthProbe(Path state) { this.state=state; }
        private Map<?,?> read() {
            try { return mapper.readValue(Files.readAllBytes(state),Map.class); }
            catch (Exception ex) { throw new IllegalStateException(ex); }
        }
        @Override public boolean started() { return Boolean.TRUE.equals(read().get("started")); }
        @Override public List<Dependency> dependencies() {
            Map<?,?> values=read();
            boolean pg=Boolean.TRUE.equals(values.get("postgres"));
            boolean kc=Boolean.TRUE.equals(values.get("keycloak"));
            return List.of(
                new Dependency("email",false,"EMAIL_AUTHORITY_NOT_CONFIGURED",0,false,null),
                new Dependency("keycloak_discovery",kc,kc?"KEYCLOAK_DISCOVERY_OK":"KEYCLOAK_DISCOVERY_UNREACHABLE",0,true,kc?"REQUIRED_AND_HEALTHY":"REQUIRED_AND_UNHEALTHY"),
                new Dependency("postgres",pg,pg?"POSTGRES_REACHABLE":"POSTGRES_UNREACHABLE",0,true,pg?"REQUIRED_AND_HEALTHY":"REQUIRED_AND_UNHEALTHY"),
                new Dependency("ai_provider",false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",0,false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP"),
                new Dependency("assessment",false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",0,false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP"),
                new Dependency("document_ingestion",false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",0,false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP"),
                new Dependency("document_parsing",false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",0,false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP"),
                new Dependency("document_worker",false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",0,false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP"),
                new Dependency("object_store",false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",0,false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP"),
                new Dependency("otel_collector",false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",0,false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP"),
                new Dependency("teacher",false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",0,false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP"),
                new Dependency("web_frontend",false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",0,false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP"),
                new Dependency("worker_dependencies",false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",0,false,"NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP")
            );
        }
    }

}

@RestController
class Contract142ContextController {
    private final Path state;
    private final ObjectMapper mapper=new ObjectMapper();
    Contract142ContextController(Path state) { this.state=state; }
    @GetMapping("/api/enterprise/contract142/context")
    public ResponseEntity<byte[]> context(jakarta.servlet.http.HttpServletRequest request) throws java.io.IOException {
        @SuppressWarnings("unchecked") Map<String,Object> actor=(Map<String,Object>)request.getAttribute("actor_context");
        @SuppressWarnings("unchecked") Map<String,Object> tenant=(Map<String,Object>)request.getAttribute("tenant_context");
        if (actor==null || tenant==null) return ResponseEntity.status(500).contentType(MediaType.APPLICATION_JSON)
            .body(mapper.writeValueAsBytes(Map.of("error","missing context")));
        var result=new java.util.LinkedHashMap<String,Object>();
        result.put("actor",actor); result.put("tenant",tenant);
        byte[] body=mapper.writeValueAsBytes(result);
        return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).contentLength(body.length).body(body);
    }
    @PutMapping("/contract142/health-state")
    public Map<String,Object> setHealth(@RequestBody Map<String,Object> value) throws java.io.IOException {
        mapper.writeValue(state.toFile(),value);
        return Map.of("updated",true);
    }
}
