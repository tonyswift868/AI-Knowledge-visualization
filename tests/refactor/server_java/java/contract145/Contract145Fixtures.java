package contract145;

import java.util.List;
import java.util.Map;
import java.sql.SQLException;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.core.env.Environment;
import win.clannad.mentorflow.identity.IdentityDirectory;

/** Only identity membership is a fixture; token verification and sync use production code. */
@Configuration(proxyBeanMethods=false)
public class Contract145Fixtures {
    @Bean @Primary
    IdentityDirectory contract145Directory(Environment env) {
        String issuer=env.getRequiredProperty("MENTORFLOW_KEYCLOAK_ISSUER");
        return new IdentityDirectory() {
            @Override public void ensureOpen(String actualIssuer,String subject) {}
            @Override public Membership resolve(String actualIssuer,String subject,String organization,String workspace) {
                if ("user-145-db-failure".equals(subject))
                    throw new IllegalStateException("test membership database outage", new SQLException("connection reset"));
                if (issuer.equals(actualIssuer) && ("user-145".equals(subject) || subject.startsWith("user-145-"))
                    && "org-145".equals(organization) && "ws-145".equals(workspace))
                    return new Membership("membership-145-"+subject,List.of("LEARNER"));
                return null;
            }
            @Override public Map<String,Object> service(String actualIssuer,String subject,String clientId) { return null; }
            @Override public boolean reachable() { return true; }
        };
    }
}
