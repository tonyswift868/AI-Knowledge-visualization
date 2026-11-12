package win.clannad.mentorflow.account;

import com.fasterxml.jackson.core.type.TypeReference;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Instant;
import java.time.Duration;
import java.util.Map;
import java.util.Set;
import javax.sql.DataSource;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.env.Environment;
import win.clannad.mentorflow.identity.IdentityDirectory;
import win.clannad.mentorflow.identity.IdentityFailure;
import win.clannad.mentorflow.identity.IdentitySettings;

/** Existing Python environment names and Alembic-owned PostgreSQL schema. */
@Configuration
@ConditionalOnProperty(name="MENTORFLOW_NATIVE_IDENTITY_ENDPOINT")
public class NativeConfiguration144 {
    @Bean NativeIdentityBridge nativeIdentityBridge(Environment env,IdentitySettings settings,Clock clock) throws Exception {
        if(!"1".equals(env.getProperty("MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS"))||!"1".equals(env.getProperty("WEB_CONCURRENCY","1")))
            throw new IllegalArgumentException("Native sessions require explicitly approved single-process deployment");
        Map<String,Object> lifecycle=null;
        boolean enabled="1".equals(env.getProperty("MENTORFLOW_ACCOUNT_LIFECYCLE_ENABLED"));
        boolean test="1".equals(env.getProperty("MENTORFLOW_ACCOUNT_MVP_TEST"));
        if(enabled||test) {
            Path file=Path.of(env.getRequiredProperty("MENTORFLOW_ACCOUNT_LIFECYCLE_CONFIG"));
            if(Files.isSymbolicLink(file)||Files.size(file)>8192)throw new IllegalArgumentException("Invalid lifecycle configuration");
            lifecycle=com.fasterxml.jackson.databind.json.JsonMapper.builder()
                .enable(com.fasterxml.jackson.core.StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                .enable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                .build().readValue(Files.readString(file),new TypeReference<Map<String,Object>>(){});
            Set<String> required=Set.of("origin","realm","client_id","secret_file","ca_file");
            if(!lifecycle.keySet().containsAll(required)||!Set.of("origin","realm","client_id","secret_file","ca_file","candidate_sha","expires_at","client_cert_file","client_key_file").containsAll(lifecycle.keySet()))throw new IllegalArgumentException("Invalid lifecycle configuration");
            if(lifecycle.containsKey("client_cert_file")!=lifecycle.containsKey("client_key_file"))throw new IllegalArgumentException("Lifecycle client certificate and key must be paired");
            String candidate=env.getProperty("MENTORFLOW_ACCOUNT_LIFECYCLE_CANDIDATE","").strip();
            if(!candidate.isEmpty()&&(!candidate.matches("[0-9a-f]{40,64}")||!candidate.equals(lifecycle.get("candidate_sha"))))throw new IllegalArgumentException("Lifecycle candidate binding mismatch");
            if(test&&!lifecycle.containsKey("expires_at"))throw new IllegalArgumentException("Test lifecycle expiry required");
            if(lifecycle.containsKey("expires_at")) {
                Instant expiry=Instant.parse((String)lifecycle.get("expires_at"));
                if(!expiry.isAfter(clock.instant())||expiry.isAfter(clock.instant().plus(Duration.ofDays(7))))throw new IllegalArgumentException("Lifecycle candidate expired or expiry too long");
            }
        }
        return new NativeIdentityBridge(settings,clock,env.getRequiredProperty("MENTORFLOW_NATIVE_IDENTITY_ENDPOINT"),
            env.getRequiredProperty("MENTORFLOW_NATIVE_IDENTITY_CA_FILE"),env.getRequiredProperty("MENTORFLOW_NATIVE_IDENTITY_CERT_FILE"),
            env.getRequiredProperty("MENTORFLOW_NATIVE_IDENTITY_KEY_FILE"),env.getProperty("MENTORFLOW_NATIVE_EXTERNAL_ISSUER"),lifecycle);
    }
    @Bean NativeSessionService nativeSessionService(DataSource source,IdentitySettings settings,NativeIdentityBridge bridge,
            IdentityDirectory directory,Clock clock,Environment env) {
        String rawTtl=env.getProperty("MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS","0");
        long ttl=Long.parseLong(IdentitySettings.stripIdentityWhitespace(rawTtl.isEmpty()?"0":rawTtl));
        if(ttl<0)throw new IllegalArgumentException("Session TTL must be non-negative");
        return new NativeSessionService(source,settings,bridge,(issuer,subject)-> {
            try{directory.ensureOpen(issuer,subject);}catch(IdentityFailure error){if(error.status==403)throw new IdentityFailure(401,"ENTERPRISE_SESSION_REAUTH_REQUIRED","登录已失效，请重新登录。");throw error;}
            return bridge.currentBinding(issuer,subject);
        },clock,ttl);
    }
}
